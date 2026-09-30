"""Fast checks for the multi-node smoke harness; no LXD or Ceph required."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "test/scripts/cephadm_feature_smoke.py"
smoke = None
if SCRIPT.exists():
    spec = importlib.util.spec_from_file_location("smoke", SCRIPT)
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)


class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(smoke, "multi-node smoke harness is missing")

    def test_large_capacity(self):
        smoke.check_capacity(8, 32 * 1024**3, 60 * 1024**3, True)

    def test_insufficient_resources_fail_without_selecting_xlarge(self):
        for args in ((4, 32, 60, True), (8, 16, 60, True),
                     (8, 32, 20, True), (8, 32, 60, False)):
            with self.subTest(args=args), self.assertRaises(RuntimeError):
                cpu, mem, disk, kvm = args
                smoke.check_capacity(cpu, mem * 1024**3,
                                     disk * 1024**3, kvm)

    def test_service_requires_running_candidate_image(self):
        daemon = {"status_desc": "running", "container_image_id": "a" * 64}
        self.assertTrue(smoke.daemons_ready([daemon], 1, "a" * 64))
        self.assertFalse(smoke.daemons_ready([], 1, "a" * 64))
        self.assertFalse(smoke.daemons_ready([daemon], 2, "a" * 64))
        stopped = dict(daemon, status_desc="starting")
        self.assertFalse(smoke.daemons_ready([stopped], 1, "a" * 64))
        pending = dict(daemon, container_image_id=None)
        try:
            ready = smoke.daemons_ready([pending], 1, "a" * 64)
        except AttributeError:
            self.fail("Missing image metadata must be treated as pending")
        self.assertFalse(ready)
        with self.assertRaisesRegex(RuntimeError, "candidate image"):
            smoke.daemons_ready([daemon], 1, "b" * 64)

    def test_osd_readiness_counts_up_and_in_not_only_total(self):
        status = {"osdmap": {"num_osds": 3, "num_up_osds": 2,
                             "num_in_osds": 3}}
        self.assertFalse(smoke.osds_ready(status, 3))
        status["osdmap"]["num_up_osds"] = 3
        self.assertTrue(smoke.osds_ready(status, 3))

    def test_wait_is_bounded(self):
        with self.assertRaisesRegex(TimeoutError, "never ready"):
            smoke.wait_for("never ready", lambda: False, timeout=0)

    def test_feature_failure_does_not_hide_later_checks(self):
        called = []

        def fail():
            raise RuntimeError("expected probe failure")

        def succeed():
            called.append(True)

        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                results = smoke.run_checks(
                    [("first", fail), ("second", succeed)], Path(tmp))
            self.assertEqual([r["status"] for r in results],
                             ["failed", "passed"])
            self.assertEqual(called, [True])
            saved = json.loads((Path(tmp) / "results.json").read_text())
            self.assertEqual(saved, results)
            self.assertIn("first", (Path(tmp) / "summary.md").read_text())

    def test_candidate_uses_supported_ceph_image_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            cluster = smoke.Cluster(Path("candidate.rock"), Path(tmp))
            cluster.image = "registry/ceph@sha256:candidate"
            calls = []
            cluster.ceph = lambda *args: calls.append(args)
            self.assertTrue(hasattr(cluster, "pin_image"))
            cluster.pin_image()
            self.assertEqual(calls, [("config", "set", "global",
                                      "container_image", cluster.image)])

    def test_failure_report_does_not_include_sensitive_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            commands = smoke.Commands(Path(tmp))
            with self.assertRaises(RuntimeError) as raised:
                commands.run(["sh", "-c", "echo secret-value; exit 1"],
                             sensitive=True)
            self.assertNotIn("secret-value", str(raised.exception))
            self.assertNotIn("secret-value",
                             (Path(tmp) / "commands.log").read_text())

    def test_nfs_cleanup_removes_cluster_only_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            cluster = smoke.Cluster(Path("candidate.rock"), Path(tmp))
            cluster.ips = {n: "10.0.0.1" for n in cluster.nodes}
            calls = []
            cluster.ceph = lambda *args, **kw: calls.append(args) or ""
            cluster.ceph_json = lambda *args: []
            cluster.filesystem = lambda: None
            cluster.service_ready = lambda *args: None
            cluster.script = lambda *args, **kw: ""
            mounts = []

            def node(*args, **kwargs):
                if args[1] == "mount":
                    mounts.append(args)
                    if len(mounts) == 1:
                        raise RuntimeError("export not ready")
                return ""

            cluster.node = node
            with mock.patch.object(smoke.time, "sleep"):
                cluster.nfs()
            self.assertEqual(len(mounts), 2)
            removals = [args for args in calls if "rm" in args
                        and args[0] == "orch"]
            self.assertEqual(removals, [])
            self.assertEqual(calls.count(("nfs", "cluster", "rm", "smoke")), 1)
            self.assertIn(("mgr", "module", "enable", "nfs"), calls)

    def test_rgw_accepts_successful_nonempty_create_bucket_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            cluster = smoke.Cluster(Path("candidate.rock"), Path(tmp))
            cluster.ips = {n: "10.0.0.1" for n in cluster.nodes}
            cluster.ceph = lambda *args, **kw: ""
            cluster.ceph_json = lambda *args: []
            cluster.service_ready = lambda *args: None
            cluster.container = lambda *args, **kw: json.dumps({
                "keys": [{"access_key": "access", "secret_key": "secret"}]})
            cluster.node = lambda *args, **kw: "<CreateBucketResult/>"

            def probe_once(label, probe, **kwargs):
                self.assertTrue(probe(), label)

            with mock.patch.object(smoke, "wait_for", probe_once):
                cluster.rgw()

    def test_registered_secrets_redacted_from_logs_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            commands = smoke.Commands(Path(tmp))
            self.assertTrue(hasattr(commands, "secrets"))
            commands.secrets.add("secret-value")
            with self.assertRaises(RuntimeError) as raised:
                commands.run(["sh", "-c", "echo secret-value >&2; exit 1"])
            self.assertNotIn("secret-value", str(raised.exception))
            self.assertNotIn("secret-value",
                             (Path(tmp) / "commands.log").read_text())

    def test_diagnostics_dont_export_service_specs_or_other_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            cluster = smoke.Cluster(Path("candidate.rock"), Path(tmp))
            cluster.image = "candidate"
            calls = []
            cluster.cmd.run = lambda args, **kw: calls.append(args) or "{}"
            cluster.diagnostics()
            self.assertFalse(any("orch ls" in " ".join(args)
                                 for args in calls))
            listing = next(args for args in calls
                           if args[:2] == ["lxc", "list"])
            self.assertIn(cluster.prefix, listing)
            self.assertNotIn("--format=json", listing)

    def test_interruption_preserves_completed_results_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rock = root / "candidate.rock"
            rock.touch()
            fixture = mock.Mock()
            fixture.provision.return_value = None
            fixture.cleanup.return_value = []

            def interrupted():
                raise KeyboardInterrupt("cancelled")

            fixture.checks.return_value = [("completed", lambda: None),
                                           ("interrupted", interrupted)]
            argv = [str(SCRIPT), "--rock", str(rock), "--output", str(root)]
            with mock.patch.object(smoke, "Cluster", return_value=fixture), \
                    mock.patch("sys.argv", argv), \
                    mock.patch.object(smoke.signal, "signal"), \
                    redirect_stdout(io.StringIO()):
                code = smoke.main()
            self.assertEqual(code, 1)
            results = json.loads((root / "results.json").read_text())
            self.assertEqual(results[0]["name"], "completed")
            fixture.diagnostics.assert_called_once()
            fixture.cleanup.assert_called_once()


class WorkflowTests(unittest.TestCase):
    def test_gate_and_runner(self):
        text = (ROOT / ".github/workflows/build_and_test.yaml").read_text()
        self.assertIn("  CephadmFeatureSmoke:\n", text)
        block = text.split("  CephadmFeatureSmoke:\n", 1)[1]
        block = block.split("\n  RookTest:", 1)[0]
        self.assertIn("needs: [CephadmTest, RookTest]", block)
        self.assertIn("runs-on: self-hosted-linux-amd64-noble-large", block)
        self.assertNotIn("noble-xlarge", block)
        self.assertIn("success()", block)
        self.assertIn("head.repo.full_name == github.repository", block)
        self.assertIn("name: rock", block)
        self.assertIn("if: always()", block)


if __name__ == "__main__":
    unittest.main()
