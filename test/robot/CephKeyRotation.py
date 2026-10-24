"""Robot keywords for a disposable, single-host cephadm CI cluster.

Credentials stay in private files or Python memory, never keyword arguments,
return values, command diagnostics, or Robot logs. Do not enable subprocess
tracing or upload the recovery directory.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from robot.api import logger
from robot.api.deco import keyword, library


@library(scope="SUITE", auto_keywords=False)
class CephKeyRotation:
    def __init__(self):
        self.workdir = None
        self.client_created = False

    def _run(self, args, check=True, timeout=60, output_prefix=None):
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired as error:
            if output_prefix:
                self._private_file(
                    f"{output_prefix}.stdout", error.stdout or ""
                )
                self._private_file(
                    f"{output_prefix}.stderr", error.stderr or ""
                )
            raise AssertionError(
                "Command timed out; output suppressed"
            ) from None
        if output_prefix:
            self._private_file(f"{output_prefix}.stdout", result.stdout)
            self._private_file(f"{output_prefix}.stderr", result.stderr)
        if check:
            if result.returncode:
                raise AssertionError(
                    f"{args[0]} failed with exit {result.returncode}; "
                    "output suppressed to protect credentials"
                )
            return result.stdout
        return result

    def _shell(self, *args, name=None, check=True):
        cmd = [
            "cephadm",
            "shell",
            "--fsid",
            self.fsid,
            "--mount",
            f"{self.workdir}:/mnt/rotation",
        ]
        if name:
            cmd += ["--name", name]
        return self._run(cmd + ["--", *args], check=check)

    def _ceph(self, *args):
        return self._shell("ceph", *args)

    def _json(self, *args):
        try:
            return json.loads(self._ceph(*args, "--format", "json"))
        except ValueError:
            raise AssertionError("Invalid Ceph JSON response") from None

    def _private_file(self, name, content):
        path = self.workdir / name
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        path.write_bytes(
            content if isinstance(content, bytes) else content.encode()
        )
        return path

    def _credential(self, entity):
        if entity == "mon.":
            # Older bootstraps keep mon. only in the monitor keyring.
            entries = self._json("auth", "ls")["auth_dump"]
            for entry in entries:
                if entry["entity"] == entity:
                    return entry
            key = self._shell(
                "ceph-authtool",
                "/var/lib/ceph/mon/ceph-"
                + self.daemons["mon"][0].split(".", 1)[1]
                + "/keyring",
                "--name",
                "mon.",
                "--print-key",
                name=self.daemons["mon"][0],
            ).strip()
            assert key, "Monitor fallback key is empty"
            return {"entity": entity, "key": key, "caps": None}
        return self._json("auth", "get", entity)[0]

    @staticmethod
    def _assert_rotated(before, after):
        assert before["entity"] == after["entity"], "Credential entity changed"
        assert before["key"] and after["key"], "Empty credential key"
        assert before["key"] != after["key"], "Credential key did not change"
        if before["caps"] is not None:
            assert before["caps"] == after["caps"], "Credential caps changed"

    def _systemctl(self, action, name):
        self._run(["systemctl", action, f"ceph-{self.fsid}@{name}.service"])

    def _wait(self, predicate, description):
        deadline = time.monotonic() + 300
        while True:
            try:
                if predicate():
                    return
            except AssertionError:
                # Monitor/mgr restarts can briefly make commands unavailable.
                pass
            if time.monotonic() >= deadline:
                raise AssertionError(f"Timed out waiting for {description}")
            time.sleep(5)

    def _daemon_ready(self, entity):
        kind, daemon_id = entity.split(".", 1)
        if kind == "mon":
            return daemon_id in self._json("quorum_status")["quorum_names"]
        if kind == "mgr":
            return self._daemon_registration(entity) is not None
        if kind == "osd":
            return any(
                str(o["osd"]) == daemon_id and o["up"] and o["in"]
                for o in self._json("osd", "dump")["osds"]
            )
        return self._daemon_registration(entity) is not None

    def _daemon_registration(self, entity):
        kind, daemon_id = entity.split(".", 1)
        if kind == "mgr":
            data = self._json("mgr", "dump")
            if data.get("active_name") == daemon_id:
                return data["active_gid"]
            entries = data.get("standbys", [])
        else:
            data = self._json("fs", "dump")
            entries = data.get("standbys", []) + [
                m
                for fs in data["filesystems"]
                for m in fs["mdsmap"]["info"].values()
                if m["state"] == "up:active"
            ]
        return next(
            (e["gid"] for e in entries if e["name"] == daemon_id), None
        )

    def _wait_daemon(self, entity, previous_gid=None):
        def ready():
            if previous_gid is None:
                return self._daemon_ready(entity)
            current = self._daemon_registration(entity)
            return current is not None and current != previous_gid

        self._wait(ready, f"{entity} to rejoin with a fresh registration")

    @keyword
    def deploy_single_node_cluster(self, image):
        assert os.geteuid() == 0, "Run as root in a disposable CI VM"
        self.workdir = Path(
            tempfile.mkdtemp(prefix="ceph-key-rotation-", dir="/root")
        )
        self.workdir.chmod(0o700)
        assert not json.loads(
            self._run(["cephadm", "ls"])
        ), "Refusing to bootstrap over existing Ceph daemons"
        assert not Path(
            "/etc/ceph/ceph.conf"
        ).exists(), "Refusing to overwrite an existing Ceph configuration"
        route = json.loads(
            self._run(["ip", "-4", "-j", "route", "get", "1.1.1.1"])
        )
        mon_ip = route[0].get("prefsrc")
        assert mon_ip, "Could not determine the monitor IPv4 address"
        logger.info("Bootstrapping a single-node cluster from the test image")
        self._run(
            [
                "cephadm",
                "--image",
                image,
                "bootstrap",
                "--mon-ip",
                mon_ip,
                "--single-host-defaults",
                "--skip-dashboard",
                "--skip-monitoring-stack",
            ],
            timeout=900,
            output_prefix="bootstrap",
        )
        local = json.loads(self._run(["cephadm", "ls"]))
        fsids = {d["fsid"] for d in local if d.get("style") == "cephadm:v1"}
        assert len(fsids) == 1, "Expected one bootstrapped cephadm cluster"
        self.fsid = fsids.pop()

        def three_available_devices():
            hosts = self._json("orch", "device", "ls", "--refresh")
            return (
                sum(
                    d.get("available", False)
                    for h in hosts
                    for d in h.get("devices", [])
                )
                == 3
            )

        self._wait(three_available_devices, "three available OSD devices")
        self._ceph("orch", "apply", "osd", "--all-available-devices")
        self._wait(
            lambda: len(self._json("osd", "ls")) == 3,
            "three OSDs to be deployed",
        )

    @keyword
    def prepare_rotation_suite(self):
        assert (
            self.workdir is not None
        ), "Deploy the cluster before preparing rotation"
        local = json.loads(self._run(["cephadm", "ls"]))
        fsids = {d["fsid"] for d in local if d.get("style") == "cephadm:v1"}
        assert len(fsids) == 1, "Expected one local cephadm cluster"
        self.fsid = fsids.pop()
        suffix = self.workdir.name.removeprefix("ceph-key-rotation-")
        self.pool = f"rotation-{suffix}"
        self.filesystem = f"rotation-{suffix}"
        self.client = f"client.rotation-{suffix}"
        self.payload = b"CephX rotation data integrity test\n"
        self._private_file("payload", self.payload)
        assert (
            len(self._json("orch", "host", "ls")) == 1
        ), "This suite supports only the disposable single-host CI cluster"
        # A real MDS is required; never silently omit MDS rotation.
        self._ceph(
            "fs", "volume", "create", self.filesystem, "--placement", "1"
        )

        def discover():
            daemons = self._json("orch", "ps", "--refresh")
            self.daemons = {
                kind: [
                    d["daemon_name"]
                    for d in daemons
                    if d["daemon_type"] == kind
                ]
                for kind in ("mon", "mgr", "osd", "mds")
            }
            return (
                all(self.daemons.values())
                and len(self.daemons["osd"]) == 3
                and all(
                    d.get("status_desc") == "running"
                    for d in daemons
                    if d["daemon_type"] in self.daemons
                )
            )

        self._wait(discover, "all four service types to be running")
        local_names = {
            d["name"]
            for d in json.loads(self._run(["cephadm", "ls"]))
            if d.get("fsid") == self.fsid
        }
        assert all(
            name in local_names
            for names in self.daemons.values()
            for name in names
        ), "Service daemon is not local"
        self.cluster_should_be_healthy()
        self._ceph("osd", "pool", "create", self.pool, "1")
        self._ceph("osd", "pool", "set", self.pool, "size", "3")
        self._ceph("osd", "pool", "application", "enable", self.pool, "rados")
        self.cluster_should_be_healthy()
        self._shell(
            "rados",
            "-p",
            self.pool,
            "put",
            "baseline",
            "/mnt/rotation/payload",
        )
        self._ceph("fs", "subvolume", "create", self.filesystem, "probe")
        keyring = self._ceph(
            "auth",
            "get-or-create",
            self.client,
            "mon",
            "allow r",
            "osd",
            f"allow rw pool={self.pool}",
        )
        self.client_created = True
        self._private_file("client-old.keyring", keyring)
        self._private_file("client-current.keyring", keyring)
        self._client_io("put", "client-baseline", "payload")

    @keyword
    def cluster_should_be_healthy(self):
        def ready():
            status = self._json("status")
            osds, pgs = status["osdmap"], status["pgmap"]
            return (
                osds["num_osds"]
                == osds["num_up_osds"]
                == osds["num_in_osds"]
                == 3
                and pgs["num_pgs"] > 0
                and pgs["pgs_by_state"]
                and all(
                    p["state_name"] == "active+clean"
                    for p in pgs["pgs_by_state"]
                )
                and all(
                    self._daemon_ready(d)
                    for names in self.daemons.values()
                    for d in names
                )
            )

        self._wait(
            ready, "quorum, managers, MDSs and three up/in OSDs with clean PGs"
        )

    def _install_keyring(self, entity, filename):
        kind, daemon_id = entity.split(".", 1)
        path = f"/var/lib/ceph/{kind}/ceph-{daemon_id}"
        self._shell(
            "ceph-authtool",
            "--import-keyring",
            f"/mnt/rotation/{filename}",
            f"{path}/keyring",
            name=entity,
        )
        if kind == "osd":
            # BlueStore needs the actual key value, not the keyring filename.
            self._shell(
                "bash",
                "-euo",
                "pipefail",
                "-c",
                """
path=$1
key=$(ceph-authtool "$path/keyring" --print-key)
test -n "$key"
ceph-bluestore-tool --dev "$path/block" set-label-key \\
    --key osd_key --value "$key"
""",
                "--",
                path,
                name=entity,
            )

    @keyword
    def all_service_keys_are_rotated(self, kind):
        assert (
            kind in self.daemons and self.daemons[kind]
        ), "Requested service type has no deployed daemons"
        entities = ["mon."] if kind == "mon" else self.daemons[kind]
        for entity in entities:
            logger.info(f"Rotating credential for {entity}")
            before = self._credential(entity)
            self._private_file(f"{entity}.before.json", json.dumps(before))
            previous_gid = (
                self._daemon_registration(entity)
                if kind in ("mgr", "mds")
                else None
            )
            if kind in ("mgr", "mds"):
                assert previous_gid is not None, "Daemon is not registered"
            if kind != "mon":
                self._systemctl("stop", entity)
                if kind == "osd":
                    self._ceph("osd", "down", entity.split(".", 1)[1])
            keyring = self._ceph("auth", "rotate", entity)
            filename = f"{entity}.new.keyring"
            self._private_file(filename, keyring)
            self._assert_rotated(before, self._credential(entity))
            if kind == "mon":
                # In-quorum monitors learn mon. from the auth database.
                # Preserve the local fallback keyring, as upstream recommends.
                for daemon in self.daemons["mon"]:
                    self._systemctl("restart", daemon)
                    self._wait_daemon(daemon)
            else:
                self._install_keyring(entity, filename)
                self._systemctl("start", entity)
                self._wait_daemon(entity, previous_gid=previous_gid)
            self.cluster_should_be_healthy()

    @keyword
    def cephfs_metadata_should_be_accessible(self):
        # Exercise mgr command routing and MDS operations, rather than trusting
        # potentially stale daemon-map entries immediately after a restart.
        self._ceph(
            "fs",
            "subvolume",
            "metadata",
            "set",
            self.filesystem,
            "probe",
            "rotation-marker",
            "verified",
        )
        value = self._ceph(
            "fs",
            "subvolume",
            "metadata",
            "get",
            self.filesystem,
            "probe",
            "rotation-marker",
        )
        assert value.strip() == "verified", "CephFS metadata readback changed"

    @keyword
    def data_should_survive_rotation(self):
        self._shell(
            "rados",
            "-p",
            self.pool,
            "get",
            "baseline",
            "/mnt/rotation/readback",
        )
        assert (
            self.workdir / "readback"
        ).read_bytes() == self.payload, "Pre-rotation data changed"
        self._shell(
            "rados",
            "-p",
            self.pool,
            "put",
            "after-rotation",
            "/mnt/rotation/payload",
        )
        self._shell(
            "rados",
            "-p",
            self.pool,
            "get",
            "after-rotation",
            "/mnt/rotation/readback",
        )
        assert (
            self.workdir / "readback"
        ).read_bytes() == self.payload, "Post-rotation data changed"

    def _client_io(
        self,
        operation,
        obj,
        filename,
        keyring="client-current.keyring",
        check=True,
    ):
        return self._shell(
            "rados",
            "--name",
            self.client,
            "--keyring",
            f"/mnt/rotation/{keyring}",
            "--conf",
            "/etc/ceph/ceph.conf",
            "-p",
            self.pool,
            operation,
            obj,
            f"/mnt/rotation/{filename}",
            check=check,
        )

    @keyword
    def the_client_key_is_rotated_and_imported(self):
        before = self._credential(self.client)
        self._private_file("client.before.json", json.dumps(before))
        self._private_file(
            "client-new.keyring", self._ceph("auth", "rotate", self.client)
        )
        self._assert_rotated(before, self._credential(self.client))
        # Import the new credential, leaving the old copy intact.
        self._shell(
            "cp",
            "/mnt/rotation/client-old.keyring",
            "/mnt/rotation/client-current.keyring",
        )
        self._shell(
            "ceph-authtool",
            "--import-keyring",
            "/mnt/rotation/client-new.keyring",
            "/mnt/rotation/client-current.keyring",
        )

    @keyword
    def the_old_client_key_should_be_rejected(self):
        result = self._client_io(
            "get",
            "client-baseline",
            "old-readback",
            keyring="client-old.keyring",
            check=False,
        )
        assert result.returncode != 0, "Old client key still authenticates"
        assert any(
            marker in result.stderr.lower()
            for marker in ("permission denied", "operation not permitted")
        ), "Old-key probe failed without an authentication denial"

    @keyword
    def the_new_client_key_should_allow_data_access(self):
        self._client_io("get", "client-baseline", "client-readback")
        assert (
            self.workdir / "client-readback"
        ).read_bytes() == self.payload, (
            "Client could not read pre-rotation data correctly"
        )
        self._client_io("put", "client-after", "payload")
        self._client_io("get", "client-after", "client-readback")
        assert (
            self.workdir / "client-readback"
        ).read_bytes() == self.payload, (
            "Client could not read its new write correctly"
        )

    @keyword
    def finish_rotation_suite(self, status):
        if self.workdir is None:
            return
        if status == "PASS":
            if self.client_created:
                self._ceph("auth", "rm", self.client)
            shutil.rmtree(self.workdir)
        else:
            logger.warn(
                f"Protected recovery files retained at {self.workdir}; "
                "do not upload them. No rollback was attempted."
            )
        # Pools/filesystem intentionally remain in the disposable VM. Never
        # enable mon_allow_pool_delete or destroy unrelated cluster resources.
