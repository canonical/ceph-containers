"""Fast checks for the multi-node smoke harness; no LXD or Ceph required."""
import argparse
from contextlib import redirect_stdout
import hashlib
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

    def test_service_matches_repo_digest_not_runtime_image_id(self):
        image = "registry.example/ceph@sha256:" + "a" * 64
        daemon = {"status_desc": "running", "container_image_name": image,
                  "container_image_digests": [image]}
        # Runtime image IDs may be config or manifest digests. Neither is
        # needed when the reported repository digest identifies the image.
        for image_id in ("a" * 64, "b" * 64, None):
            with self.subTest(image_id=image_id):
                daemon["container_image_id"] = image_id
                try:
                    ready = smoke.daemons_ready([daemon], 1, image)
                except RuntimeError as error:
                    self.fail(f"Correct repository digest rejected: {error}")
                self.assertTrue(ready)
        self.assertFalse(smoke.daemons_ready([], 1, image))
        self.assertFalse(smoke.daemons_ready([daemon], 2, image))
        stopped = dict(daemon, status_desc="starting")
        self.assertFalse(smoke.daemons_ready([stopped], 1, image))

    def test_image_name_alone_does_not_prove_actual_image(self):
        image = "registry.example/ceph@sha256:" + "a" * 64
        daemon = {"status_desc": "running", "container_image_name": image,
                  "container_image_id": "a" * 64}
        for digests in (None, [], image):
            daemon["container_image_digests"] = digests
            try:
                ready = smoke.daemons_ready([daemon], 1, image)
            except RuntimeError as error:
                self.fail(
                    f"Missing digest metadata should be pending: {error}")
            self.assertFalse(ready)
        daemon["container_image_digests"] = [
            "registry.example/ceph@sha256:" + "b" * 64]
        with self.assertRaisesRegex(RuntimeError, "candidate image"):
            smoke.daemons_ready([daemon], 1, image)

    def test_image_input_accepts_explicit_tags_and_digests(self):
        validate = getattr(smoke, "image_reference", None)
        self.assertIsNotNone(validate)
        for image in ("ghcr.io/canonical/ceph:tentacle-edge",
                      "registry.example:5000/team/ceph:v20.2.4",
                      "ghcr.io/canonical/ceph@sha256:" + "a" * 64):
            self.assertEqual(validate(image), image)
        for image in ("ceph:latest", "ghcr.io/canonical/ceph",
                      "https://ghcr.io/canonical/ceph:latest",
                      "user:password@registry.example/ceph:latest",
                      "ghcr.io/canonical/ceph:latest; echo injected",
                      "ghcr.io/canonical/ceph@sha256:short",
                      "ghcr.io/canonical/ceph:"):
            with self.subTest(image=image), \
                    self.assertRaises(argparse.ArgumentTypeError):
                validate(image)

    def test_tag_resolution_preserves_registry_port_and_explicit_digest(self):
        pin = getattr(smoke, "pinned_reference", None)
        self.assertIsNotNone(pin)
        digest = "sha256:" + "a" * 64
        self.assertEqual(pin("registry.example:5000/team/ceph:edge", digest),
                         "registry.example:5000/team/ceph@" + digest)
        explicit = "ghcr.io/canonical/ceph@" + digest
        self.assertEqual(pin(explicit, "sha256:" + "b" * 64), explicit)

    def test_registry_source_is_resolved_once_before_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                cluster = smoke.Cluster(None, Path(tmp),
                                        image="ghcr.io/canonical/ceph:edge")
            except TypeError:
                self.fail("Registry image input is not supported")
            data = {"Architecture": "amd64", "Os": "linux",
                    "Digest": "sha256:" + "a" * 64,
                    "Labels": {"ceph": "True"}}
            cluster.cmd.run = mock.Mock(return_value=json.dumps(data))
            source = cluster.resolve_source()
            self.assertEqual(source, "docker://ghcr.io/canonical/ceph@"
                             "sha256:" + "a" * 64)
            self.assertEqual(cluster.cmd.run.call_count, 1)
            self.assertEqual(cluster.source_metadata["requested_image"],
                             "ghcr.io/canonical/ceph:edge")
            self.assertEqual(cluster.source_metadata["source_image"],
                             source.removeprefix("docker://"))
            data["Architecture"] = "arm64"
            cluster.cmd.run.return_value = json.dumps(data)
            with self.assertRaisesRegex(RuntimeError, "linux/amd64"):
                cluster.resolve_source()
            data["Architecture"] = "amd64"
            data["Labels"] = None
            cluster.cmd.run.return_value = json.dumps(data)
            with self.assertRaisesRegex(RuntimeError, "ceph=True"):
                cluster.resolve_source()

    def test_local_archive_source_still_records_its_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rock = root / "candidate.rock"
            rock.write_bytes(b"local archive fixture")
            cluster = smoke.Cluster(rock, root)
            self.assertTrue(hasattr(cluster, "resolve_source"))
            cluster.cmd.run = mock.Mock()
            self.assertEqual(cluster.resolve_source(), f"oci-archive:{rock}")
            self.assertEqual(cluster.source_metadata["sha256"],
                             hashlib.sha256(rock.read_bytes()).hexdigest())
            cluster.cmd.run.assert_not_called()

    def test_cli_passes_registry_image_without_needing_a_rock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = "ghcr.io/canonical/ceph:tentacle-edge"
            fixture = mock.Mock()
            fixture.cleanup.return_value = []
            fixture.checks.return_value = [("probe", lambda: None)]
            argv = [str(SCRIPT), "--image", image, "--output", str(root)]
            with mock.patch.object(smoke, "Cluster", return_value=fixture) \
                    as factory, mock.patch("sys.argv", argv), \
                    mock.patch.object(smoke.signal, "signal"), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(smoke.main(), 0)
            factory.assert_called_once_with(None, root.resolve(), image=image)
            fixture.provision.assert_called_once()
            fixture.cleanup.assert_called_once()

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
    def test_build_jobs_keep_standard_runners_and_local_rockcraft(self):
        for name, runner in (("build_and_test", "ubuntu-latest"),
                             ("publish_edge", "ubuntu-22.04"),
                             ("publish_release", "ubuntu-22.04"),
                             ("publish_hotfix", "ubuntu-22.04")):
            with self.subTest(workflow=name):
                text = (ROOT / f".github/workflows/{name}.yaml").read_text()
                if name == "build_and_test":
                    text = text.split("  build-rock:\n", 1)[1]
                    text = text.split("  flake8-lint:\n", 1)[0]
                self.assertIn(f"runs-on: {runner}\n", text)
                self.assertNotIn("self-hosted", text)
                self.assertIn("canonical/setup-lxd@", text)
                self.assertIn("canonical/craft-actions/rockcraft-pack@", text)
                self.assertNotIn("remote-build", text)
                self.assertNotIn("LAUNCHPAD_CREDENTIALS", text)

    def test_feature_suite_is_manual_only_with_an_image_input(self):
        canary = (ROOT / ".github/workflows/build_and_test.yaml").read_text()
        self.assertNotIn("  CephadmFeatureSmoke:\n", canary)
        path = ROOT / ".github/workflows/cephadm_feature_smoke.yaml"
        self.assertTrue(path.is_file(), "manual workflow is missing")
        text = path.read_text()
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("      image:\n", text)
        self.assertIn("required: true", text)
        self.assertIn("type: string", text)
        self.assertNotIn("pull_request:", text)
        self.assertNotIn("push:", text)
        self.assertNotIn("needs:", text)
        self.assertNotIn("rockcraft-pack", text)
        self.assertNotIn("actions/download-artifact", text)
        self.assertIn("runs-on: self-hosted-linux-amd64-noble-large", text)
        self.assertNotIn("noble-xlarge", text)
        self.assertIn("CEPH_IMAGE: ${{ inputs.image }}", text)
        self.assertIn('--image "$CEPH_IMAGE"', text)
        self.assertIn("if: always()", text)


if __name__ == "__main__":
    unittest.main()
