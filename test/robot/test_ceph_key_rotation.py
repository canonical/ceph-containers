"""Local keyword tests; no live Ceph cluster is used."""

import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from robot.api import ResultWriter, TestSuite

from CephKeyRotation import CephKeyRotation


class RotationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lib = CephKeyRotation()
        self.lib.workdir = Path(self.tmp.name)
        self.lib.fsid = "test-fsid"
        self.lib.daemons = {
            "mon": ["mon.node"],
            "mgr": ["mgr.node.a"],
            "osd": ["osd.0", "osd.1", "osd.2"],
            "mds": ["mds.fs.node.a"],
        }
        self.lib.client = "client.rotation"
        self.lib.pool = "rotation-pool"
        self.lib.payload = b"rotation test\n"
        self.lib._private_file("payload", self.lib.payload)

    def test_command_errors_do_not_disclose_secrets(self):
        secret = "SECRET-KEY-MUST-NOT-LEAK"
        with patch(
            "CephKeyRotation.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, secret, secret),
        ):
            with self.assertRaises(AssertionError) as error:
                self.lib._run(["cephadm", "shell", "--", "secret-command"])
        self.assertNotIn(secret, str(error.exception))

    def test_failed_robot_reports_do_not_contain_command_secrets(self):
        secret = "SECRET-CREDENTIAL-MUST-NOT-APPEAR"
        recovery = self.lib.workdir / "recovery"
        recovery.mkdir()
        suite = TestSuite("Credential report safety")
        suite.resource.imports.library("CephKeyRotation")
        suite.tests.create("Command failure").body.create_keyword(
            "Prepare Rotation Suite"
        )
        output = self.lib.workdir / "output.xml"
        local = [{"style": "cephadm:v1", "fsid": "test-fsid"}]

        def command(args, **kwargs):
            if args == ["cephadm", "ls"]:
                return subprocess.CompletedProcess(
                    args, 0, json.dumps(local), ""
                )
            return subprocess.CompletedProcess(args, 1, secret, secret)

        with patch("CephKeyRotation.os.geteuid", return_value=0), patch(
            "CephKeyRotation.tempfile.mkdtemp", return_value=str(recovery)
        ), patch("CephKeyRotation.subprocess.run", side_effect=command):
            result = suite.run(output=str(output), stdout=io.StringIO())
        self.assertEqual(result.return_code, 1)
        ResultWriter(str(output)).write_results(
            log=str(self.lib.workdir / "log.html"),
            report=str(self.lib.workdir / "report.html"),
        )
        for name in ("output.xml", "log.html", "report.html"):
            self.assertNotIn(secret, (self.lib.workdir / name).read_text())

    def test_timeout_does_not_disclose_output(self):
        with patch(
            "CephKeyRotation.subprocess.run",
            side_effect=subprocess.TimeoutExpired([], 60, output="SECRET"),
        ):
            with self.assertRaises(AssertionError) as error:
                self.lib._run(["cephadm"])
        self.assertNotIn("SECRET", str(error.exception))

    def test_private_keyring_permissions(self):
        path = self.lib._private_file("credential", b"secret")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_changed_key_and_equal_caps_required(self):
        before = {"entity": "osd.0", "key": "old", "caps": {"mon": "r"}}
        for after in ({**before}, {**before, "key": "new", "caps": {}}):
            with self.subTest(after=after):
                with self.assertRaises(AssertionError):
                    self.lib._assert_rotated(before, after)
        self.lib._assert_rotated(before, {**before, "key": "new"})

    def test_rotates_every_osd_while_stopped(self):
        events = []

        def ceph(*args):
            events.append(args)
            if args[:2] == ("auth", "get"):
                rotated = ("auth", "rotate", args[2]) in events
                return json.dumps(
                    [
                        {
                            "entity": args[2],
                            "key": "new" if rotated else "old",
                            "caps": {"mon": "allow profile osd"},
                        }
                    ]
                )
            return "[osd]\nkey = new"

        with patch.object(self.lib, "_ceph", side_effect=ceph), patch.object(
            self.lib,
            "_systemctl",
            side_effect=lambda action, name: events.append((action, name)),
        ), patch.object(
            self.lib,
            "_install_keyring",
            side_effect=lambda name, path: events.append(("install", name)),
        ), patch.object(
            self.lib, "_wait_daemon"
        ), patch.object(
            self.lib, "cluster_should_be_healthy"
        ):
            self.lib.all_service_keys_are_rotated("osd")
        for entity in self.lib.daemons["osd"]:
            self.assertLess(
                events.index(("stop", entity)),
                events.index(("auth", "rotate", entity)),
            )
            self.assertLess(
                events.index(("auth", "rotate", entity)),
                events.index(("install", entity)),
            )
            self.assertLess(
                events.index(("install", entity)),
                events.index(("start", entity)),
            )
            self.assertIn(("osd", "down", entity.split(".", 1)[1]), events)

    def test_mon_rotation_does_not_stop_monitor_before_auth_command(self):
        before = {"entity": "mon.", "key": "old", "caps": {}}
        with patch.object(
            self.lib,
            "_credential",
            side_effect=[before, {**before, "key": "new"}],
        ), patch.object(
            self.lib, "_ceph", return_value="keyring"
        ) as ceph, patch.object(
            self.lib, "_systemctl"
        ) as systemctl, patch.object(
            self.lib, "_wait_daemon"
        ), patch.object(
            self.lib, "cluster_should_be_healthy"
        ):
            self.lib.all_service_keys_are_rotated("mon")
        ceph.assert_called_once_with("auth", "rotate", "mon.")
        systemctl.assert_called_once_with("restart", "mon.node")

    def test_osd_label_is_given_key_value_not_filename(self):
        with patch.object(self.lib, "_shell", return_value="") as shell:
            self.lib._install_keyring("osd.0", "new.keyring")
        args = shell.call_args_list[-1].args
        script = args[args.index("-c") + 1]
        self.assertIn("ceph-authtool", script)
        self.assertIn('--value "$key"', script)
        self.assertIn("$path/block", script)

    def test_recovery_requires_a_fresh_manager_registration(self):
        with patch.object(
            self.lib, "_daemon_ready", return_value=True
        ), patch.object(
            self.lib, "_daemon_registration", side_effect=[7, 8]
        ) as registration, patch(
            "CephKeyRotation.time.sleep"
        ):
            self.lib._wait_daemon("mgr.node.a", previous_gid=7)
        self.assertEqual(registration.call_count, 2)

    def test_bad_rotation_does_not_start_daemon(self):
        credential = {"entity": "mgr.node.a", "key": "old", "caps": {}}
        with patch.object(
            self.lib, "_credential", return_value=credential
        ), patch.object(
            self.lib, "_ceph", return_value="keyring"
        ), patch.object(
            self.lib, "_daemon_registration", return_value=7
        ), patch.object(
            self.lib, "_systemctl"
        ) as systemctl:
            with self.assertRaises(AssertionError):
                self.lib.all_service_keys_are_rotated("mgr")
        systemctl.assert_called_once_with("stop", "mgr.node.a")

    def test_client_rotation_keeps_old_keyring_and_imports_new_one(self):
        before = {
            "entity": self.lib.client,
            "key": "old",
            "caps": {"mon": "r"},
        }
        with patch.object(
            self.lib,
            "_credential",
            side_effect=[before, {**before, "key": "new"}],
        ), patch.object(
            self.lib, "_ceph", return_value="new keyring"
        ), patch.object(
            self.lib, "_shell", return_value=""
        ) as shell:
            self.lib.the_client_key_is_rotated_and_imported()
        self.assertEqual(
            (self.lib.workdir / "client-new.keyring").read_text(),
            "new keyring",
        )
        self.assertIn("ceph-authtool", shell.call_args.args)
        self.assertEqual(
            shell.call_args_list[0].args,
            (
                "cp",
                "/mnt/rotation/client-old.keyring",
                "/mnt/rotation/client-current.keyring",
            ),
        )

    def test_old_client_key_requires_auth_failure_not_arbitrary_error(self):
        for rc, stderr, accepted in [
            (0, "", False),
            (1, "pool does not exist", False),
            (1, "Permission denied", True),
        ]:
            with self.subTest(rc=rc, stderr=stderr), patch.object(
                self.lib,
                "_shell",
                return_value=subprocess.CompletedProcess([], rc, "", stderr),
            ):
                if accepted:
                    self.lib.the_old_client_key_should_be_rejected()
                else:
                    with self.assertRaises(AssertionError):
                        self.lib.the_old_client_key_should_be_rejected()

    def test_corrupt_readback_fails(self):
        (self.lib.workdir / "readback").write_bytes(b"corrupt")
        with patch.object(self.lib, "_shell", return_value=""):
            with self.assertRaises(AssertionError):
                self.lib.data_should_survive_rotation()

    def test_metadata_probe_requires_successful_readback(self):
        self.lib.filesystem = "rotation-fs"
        with patch.object(self.lib, "_ceph", side_effect=["", "verified\n"]):
            self.lib.cephfs_metadata_should_be_accessible()
        with patch.object(self.lib, "_ceph", side_effect=["", "wrong"]):
            with self.assertRaises(AssertionError):
                self.lib.cephfs_metadata_should_be_accessible()

    def test_bootstrap_monitor_key_comes_from_local_fallback(self):
        with patch.object(
            self.lib, "_json", return_value={"auth_dump": []}
        ), patch.object(self.lib, "_shell", return_value="old-key\n") as shell:
            credential = self.lib._credential("mon.")
        self.assertEqual(credential["entity"], "mon.")
        self.assertEqual(credential["key"], "old-key")
        self.assertIsNone(credential["caps"])
        self.assertEqual(shell.call_args.kwargs["name"], "mon.node")

    def test_active_and_standby_mds_are_recognized(self):
        fsmap = {
            "standbys": [{"name": "standby", "gid": 2}],
            "filesystems": [
                {
                    "mdsmap": {
                        "info": {
                            "1": {
                                "name": "active",
                                "state": "up:active",
                                "gid": 1,
                            }
                        }
                    }
                }
            ],
        }
        with patch.object(self.lib, "_json", return_value=fsmap):
            self.assertTrue(self.lib._daemon_ready("mds.active"))
            self.assertTrue(self.lib._daemon_ready("mds.standby"))
            self.assertFalse(self.lib._daemon_ready("mds.missing"))
        fsmap["filesystems"][0]["mdsmap"]["info"]["1"]["state"] = "up:replay"
        with patch.object(self.lib, "_json", return_value=fsmap):
            self.assertFalse(self.lib._daemon_ready("mds.active"))

    def test_wait_retries_transient_command_failures(self):
        with patch("CephKeyRotation.time.sleep"), patch(
            "CephKeyRotation.time.monotonic", side_effect=[0, 1, 2]
        ), patch.object(
            self.lib,
            "_daemon_ready",
            side_effect=[
                AssertionError("temporarily unavailable"),
                False,
                True,
            ],
        ) as ready:
            self.lib._wait_daemon("mgr.node.a")
        self.assertEqual(ready.call_count, 3)

    def test_wait_has_a_deadline(self):
        with patch("CephKeyRotation.time.monotonic", side_effect=[0, 301]):
            with self.assertRaisesRegex(AssertionError, "Timed out"):
                self.lib._wait(lambda: False, "OSD recovery")

    def test_failed_suite_preserves_recovery_files(self):
        self.lib.client_created = True
        with patch.object(self.lib, "_ceph") as ceph, patch(
            "CephKeyRotation.logger.warn"
        ) as warning:
            self.lib.finish_rotation_suite("FAIL")
        self.assertTrue((self.lib.workdir / "payload").exists())
        ceph.assert_not_called()
        warning.assert_called_once()

    def test_successful_suite_removes_client_and_credentials(self):
        self.lib.client_created = True
        with patch.object(self.lib, "_ceph") as ceph:
            self.lib.finish_rotation_suite("PASS")
        self.assertFalse(self.lib.workdir.exists())
        ceph.assert_called_once_with("auth", "rm", self.lib.client)

    def test_missing_daemon_types_are_not_silently_skipped(self):
        self.lib.daemons["mds"] = []
        with self.assertRaises(AssertionError):
            self.lib.all_service_keys_are_rotated("mds")


if __name__ == "__main__":
    unittest.main()
