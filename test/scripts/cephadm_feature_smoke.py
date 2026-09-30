#!/usr/bin/env python3
"""Breadth-first Cephadm checks on three disposable LXD VMs.

Run as root on an initialized, ephemeral LXD runner. Never use this harness
against an existing Ceph cluster. Cleanup only deletes this run's resources.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import signal
import subprocess
import time
import uuid


GIB = 1024**3
WORK = "/root/ceph-feature-smoke"


def check_capacity(cpus, memory, disk, kvm):
    if cpus < 8 or memory < 24 * GIB or disk < 50 * GIB or not kvm:
        raise RuntimeError(
            "Need 8 CPUs, 24 GiB RAM, 50 GiB free disk and /dev/kvm; "
            f"found cpus={cpus}, ram={memory // GIB} GiB, "
            f"disk={disk // GIB} GiB, kvm={kvm}. No runner-size fallback.")


def daemons_ready(daemons, count, image_id):
    if len(daemons) != count:
        return False
    for daemon in daemons:
        if daemon.get("status_desc") != "running":
            return False
        actual = daemon.get("container_image_id") or ""
        actual = actual.removeprefix("sha256:")
        if not actual:
            return False
        if actual != image_id:
            name = daemon.get("daemon_name", daemon.get("daemon_id"))
            raise RuntimeError(
                f"Daemon is not using the candidate image: {name}")
    return True


def osds_ready(status, count):
    osds = status.get("osdmap", {})
    return all(osds.get(key) == count for key in
               ("num_osds", "num_up_osds", "num_in_osds"))


def wait_for(label, probe, timeout=360, interval=5):
    deadline = time.monotonic() + timeout
    last_error = "not ready"
    while True:
        try:
            value = probe()
            if value:
                return value
        except RuntimeError as error:
            last_error = str(error)
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out: {label}: {last_error}")
        time.sleep(interval)


def write_results(results, output):
    (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    lines = ["# Cephadm feature smoke", "", "| Check | Result | Seconds |",
             "| --- | --- | ---: |"]
    for result in results:
        lines.append(f"| {result['name']} | {result['status']} | "
                     f"{result['seconds']:.1f} |")
    (output / "summary.md").write_text("\n".join(lines) + "\n")


def run_checks(checks, output, results=None):
    if results is None:
        results = []
    for name, check in checks:
        print(f"START {name}", flush=True)
        started = time.monotonic()
        result = {"name": name, "status": "passed"}
        try:
            check()
        except Exception as error:
            result.update(status="failed", error=str(error))
        result["seconds"] = time.monotonic() - started
        results.append(result)
        write_results(results, output)
        print(f"{result['status'].upper()} {name}", flush=True)
    return results


class Commands:
    def __init__(self, output):
        self.output = output
        self.output.mkdir(parents=True, exist_ok=True)
        self.secrets = set()

    def redact(self, text):
        for secret in sorted(self.secrets, key=len, reverse=True):
            text = text.replace(secret, "[REDACTED]")
        return text

    def run(self, args, *, input=None, timeout=120, sensitive=False):
        display = ("<sensitive command>" if sensitive
                   else self.redact(shlex.join(args)))
        with (self.output / "commands.log").open("a") as log:
            log.write(f"\n$ {display}\n")
            log.flush()
            try:
                result = subprocess.run(args, input=input, text=True,
                                        capture_output=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                log.write("Command timed out\n")
                raise RuntimeError(f"Command timed out: {display}") from None
            if not sensitive:
                log.write(self.redact(result.stdout + result.stderr))
            if result.returncode:
                detail = ("" if sensitive
                          else self.redact(result.stderr)[-4000:])
                raise RuntimeError(
                    f"Exit {result.returncode}: {display}\n{detail}")
            return result.stdout


class Cluster:
    def __init__(self, rock, output):
        self.rock = rock
        self.output = output
        self.cmd = Commands(output)
        suffix = uuid.uuid4().hex[:8]
        self.prefix = f"cfsmoke-{suffix}"
        self.network = f"cfs{suffix}"
        self.nodes = [f"{self.prefix}-{n}" for n in range(3)]
        self.instances = []
        self.volumes = []
        self.rules = []
        self.network_created = False
        self.ips = {}
        self.image = ""
        self.image_id = ""

    def lxc(self, *args, **kwargs):
        return self.cmd.run(["lxc", *args], **kwargs)

    def node(self, name, *args, **kwargs):
        return self.lxc("exec", name, "--", *args, **kwargs)

    def script(self, name, text, *args, **kwargs):
        return self.node(name, "bash", "-euo", "pipefail", "-s", "--",
                         *args, input=text, **kwargs)

    def container(self, *args, **kwargs):
        return self.node(self.nodes[0], "cephadm", "--image", self.image,
                         "shell", "--mount", f"{WORK}:/work", "--",
                         *args, **kwargs)

    def ceph(self, *args, **kwargs):
        return self.container("ceph", *args, **kwargs)

    def ceph_json(self, *args):
        return json.loads(self.ceph(*args, "--format=json"))

    def put(self, name, path, text):
        self.node(name, "bash", "-c", 'umask 077; cat > "$1"', "bash",
                  path, input=text, sensitive=True)

    def pin_image(self):
        # Ceph 20.2 includes NFS and iSCSI in CEPH_IMAGE_TYPES; they use the
        # same container_image option as the core and mirror daemons.
        self.ceph("config", "set", "global", "container_image", self.image)

    def service_ready(self, name, count=1):
        wait_for(name, lambda: daemons_ready(self.ceph_json(
            "orch", "ps", "--service_name", name, "--refresh"),
            count, self.image_id))

    @contextmanager
    def service(self, name, remove=None):
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            try:
                if remove:
                    remove()
                else:
                    self.ceph("orch", "rm", name)
                wait_for(f"remove {name}", lambda: not self.ceph_json(
                    "orch", "ps", "--service_name", name, "--refresh"))
            except Exception:
                if not failed:
                    raise

    def provision(self):
        memory = int(next(
            line.split()[1]
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemTotal:"))) * 1024
        check_capacity(len(os.sched_getaffinity(0)), memory,
                       shutil.disk_usage("/var/snap/lxd/common").free,
                       os.access("/dev/kvm", os.R_OK | os.W_OK))
        self.lxc("network", "create", self.network, "ipv4.address=auto",
                 "ipv4.nat=true", "ipv6.address=none")
        self.network_created = True
        # Scope firewall changes to our bridge; never flush the host firewall.
        for rule in (["-i", self.network, "-j", "ACCEPT"],
                     ["-o", self.network, "-m", "conntrack", "--ctstate",
                      "RELATED,ESTABLISHED", "-j", "ACCEPT"]):
            self.cmd.run(["iptables", "-I", "FORWARD", *rule])
            self.rules.append(rule)
        for index, name in enumerate(self.nodes):
            self.lxc("init", "--vm", "ubuntu:26.04", name,
                     "--network", self.network, "--storage", "default",
                     "-c", "limits.cpu=2", "-c", "limits.memory=6GiB",
                     "-d", "root,size=15GiB", timeout=600)
            self.instances.append(name)
            volume = f"{self.prefix}-osd{index}"
            self.lxc("storage", "volume", "create", "default", volume,
                     "size=8GiB", "--type", "block")
            self.volumes.append(volume)
            self.lxc("storage", "volume", "attach", "default", volume, name)
            self.lxc("start", name)
        for name in self.nodes:
            wait_for(f"agent {name}", lambda n=name:
                     self.node(n, "true", timeout=15) == "", timeout=300)
            self.node(name, "cloud-init", "status", "--wait", timeout=300)
            routes = json.loads(self.node(name, "ip", "-4", "-j", "route",
                                          "show", "default"))
            self.ips[name] = routes[0]["prefsrc"]
            self.script(name, """
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io openssh-server cephadm ceph-common \
  python3-ceph-common lvm2 chrony curl nfs-common kmod
systemctl enable --now docker ssh chrony
install -d -m 700 /root/.ssh /root/ceph-feature-smoke
""", timeout=900)
        seed = self.nodes[0]
        registry = f"{self.ips[seed]}:5000"
        for name in self.nodes:
            self.put(name, "/etc/docker/daemon.json",
                     json.dumps({"insecure-registries": [registry]}))
            self.node(name, "systemctl", "restart", "docker")
        self.node(seed, "docker", "run", "-d", "--restart=always",
                  "--name", "registry", "-p", "5000:5000", "registry:2",
                  timeout=300)
        wait_for("registry", lambda: self.node(
            seed, "curl", "-fsS", f"http://{registry}/v2/") == "{}")
        tagged = f"{registry}/canonical/ceph:candidate"
        self.cmd.run(["rockcraft.skopeo", "--insecure-policy", "copy",
                      "--dest-tls-verify=false", f"oci-archive:{self.rock}",
                      f"docker://{tagged}"], timeout=600)
        manifest = self.cmd.run(["rockcraft.skopeo", "inspect", "--raw",
                                 "--tls-verify=false", f"docker://{tagged}"])
        image_info = json.loads(self.cmd.run([
            "rockcraft.skopeo", "inspect", "--tls-verify=false",
            f"docker://{tagged}"]))
        if image_info.get("Labels", {}).get("ceph") != "True":
            raise RuntimeError("Candidate lacks cephadm's ceph=True label")
        self.image = f"{registry}/canonical/ceph@{image_info['Digest']}"
        self.image_id = json.loads(manifest)["config"]["digest"].split(":")[1]
        digest = hashlib.sha256()
        with self.rock.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        (self.output / "candidate.json").write_text(json.dumps({
            "rock": self.rock.name, "sha256": digest.hexdigest(),
            "image": self.image, "image_id": self.image_id,
            "nodes": self.ips}, indent=2))
        self.node(seed, "cephadm", "--image", self.image, "bootstrap",
                  "--mon-ip", self.ips[seed], "--skip-dashboard",
                  "--skip-monitoring-stack", timeout=900)
        self.pin_image()
        public_key = self.ceph("cephadm", "get-pub-key")
        for name in self.nodes[1:]:
            self.node(name, "bash", "-c",
                      "cat >> /root/.ssh/authorized_keys; "
                      "chmod 600 /root/.ssh/authorized_keys", input=public_key)
            self.ceph("orch", "host", "add", name, self.ips[name])
        self.ceph("orch", "apply", "mon", "--placement",
                  "3 " + " ".join(self.nodes))
        self.ceph("orch", "apply", "mgr", "--placement",
                  "2 " + " ".join(self.nodes[:2]))
        # Fixed small-cache budget: these are functional, not throughput tests.
        self.ceph("config", "set", "osd", "osd_memory_target_autotune",
                  "false")
        self.ceph("config", "set", "osd", "osd_memory_target", str(2 * GIB))
        self.ceph("orch", "apply", "osd", "--all-available-devices")
        self.service_ready("mon", 3)
        self.service_ready("mgr", 2)
        wait_for("3 OSDs up/in", lambda: osds_ready(
            self.ceph_json("status"), 3), timeout=600)
        wait_for("candidate OSDs", lambda: daemons_ready(self.ceph_json(
            "orch", "ps", "--daemon_type", "osd", "--refresh"),
            3, self.image_id))
        self.put(seed, f"{WORK}/payload", "ceph-container-feature-smoke\n")

    def core(self):
        hosts = self.ceph_json("orch", "host", "ls")
        if {h["hostname"] for h in hosts} != set(self.nodes):
            raise RuntimeError("Not all three hosts are enrolled")
        if len(self.ceph_json("quorum_status")["quorum_names"]) != 3:
            raise RuntimeError("Expected three monitors in quorum")
        if not self.ceph_json("orch", "status").get("available"):
            raise RuntimeError("Cephadm backend unavailable")

    def pool(self, name):
        self.ceph("osd", "pool", "create", name, "8")
        self.ceph("osd", "pool", "set", name, "size", "3")

    def rbd(self):
        self.pool("smoke-rbd")
        self.container("rbd", "pool", "init", "smoke-rbd")
        self.container("rbd", "import", "/work/payload", "smoke-rbd/image")
        try:
            self.container("rbd", "export", "smoke-rbd/image", "/work/rbd-out")
            # RBD rounds image sizes up; compare the original data prefix.
            self.script(self.nodes[0], """
size=$(stat -c %s /root/ceph-feature-smoke/payload)
cmp -n "$size" /root/ceph-feature-smoke/payload \
  /root/ceph-feature-smoke/rbd-out
""")
        finally:
            self.container("rbd", "rm", "smoke-rbd/image")

    def filesystem(self):
        self.ceph("mgr", "module", "enable", "volumes")
        filesystems = self.ceph_json("fs", "ls")
        if not any(fs["name"] == "smoke-fs" for fs in filesystems):
            self.ceph("fs", "volume", "create", "smoke-fs", "--placement",
                      f"1 {self.nodes[1]}")
        self.service_ready("mds.smoke-fs")
        wait_for("active MDS", lambda: any(
            info["state"] == "up:active" for info in self.ceph_json(
                "fs", "get", "smoke-fs")["mdsmap"]["info"].values()))

    def cephfs(self):
        self.filesystem()
        self.node(self.nodes[0], "modprobe", "fuse")
        self.node(self.nodes[0], "docker", "run", "--rm", "--privileged",
                  "--network=host", "-v", "/etc/ceph:/etc/ceph:ro",
                  "--entrypoint", "bash", self.image, "-ec", """
mkdir -p /mnt/smoke
ceph-fuse --client_fs smoke-fs /mnt/smoke
trap 'umount /mnt/smoke' EXIT
printf 'cephfs-smoke\n' > /tmp/expected
cp /tmp/expected /mnt/smoke/probe
cmp /tmp/expected /mnt/smoke/probe
rm /mnt/smoke/probe
""")

    def rgw(self):
        with self.service("rgw.smoke"):
            self.ceph("orch", "apply", "rgw", "smoke", "--placement",
                      f"1 {self.nodes[2]}", "--port", "8080")
            self.service_ready("rgw.smoke")
            user = json.loads(self.container(
                "radosgw-admin", "user", "create", "--uid", "smoke",
                "--display-name", "Smoke test", sensitive=True))
            key = user["keys"][0]
            self.cmd.secrets.update((key["access_key"], key["secret_key"]))
            url = f"http://{self.ips[self.nodes[2]]}:8080/smoke-bucket"
            curl = ["curl", "-fsS", "--connect-timeout", "10",
                    "--max-time", "30", "--aws-sigv4", "aws:amz:us-east-1:s3",
                    "--user", f"{key['access_key']}:{key['secret_key']}"]
            seed = self.nodes[0]
            wait_for("S3 bucket", lambda: (self.node(
                seed, *curl, "-X", "PUT", url, sensitive=True), True)[1])
            try:
                self.node(seed, *curl, "-X", "PUT", "--data-binary",
                          f"@{WORK}/payload", f"{url}/probe", sensitive=True)
                self.node(seed, *curl, f"{url}/probe", "-o", f"{WORK}/s3-out",
                          sensitive=True)
                self.node(seed, "cmp", f"{WORK}/payload", f"{WORK}/s3-out")
            finally:
                self.node(seed, *curl, "-X", "DELETE", f"{url}/probe",
                          sensitive=True)
                self.node(seed, *curl, "-X", "DELETE", url, sensitive=True)
                self.container("radosgw-admin", "user", "rm", "--uid", "smoke")

    def nfs(self):
        self.ceph("mgr", "module", "enable", "nfs")
        self.filesystem()
        with self.service("nfs.smoke", remove=lambda:
                          self.ceph("nfs", "cluster", "rm", "smoke")):
            self.ceph("nfs", "cluster", "create", "smoke", "--placement",
                      f"1 {self.nodes[1]}")
            self.service_ready("nfs.smoke")
            self.ceph("nfs", "export", "create", "cephfs", "smoke",
                      "/smoke", "smoke-fs")
            client = self.nodes[2]
            self.node(client, "mkdir", "-p", "/mnt/nfs-smoke")
            wait_for("NFS export mount", lambda: self.node(
                client, "mount", "-t", "nfs", "-o",
                "vers=4.1,proto=tcp,retry=0",
                f"{self.ips[self.nodes[1]]}:/smoke", "/mnt/nfs-smoke",
                timeout=30) == "")
            try:
                self.script(client, """
printf 'nfs-smoke\n' > /tmp/nfs-expected
cp /tmp/nfs-expected /mnt/nfs-smoke/nfs-probe
cmp /tmp/nfs-expected /mnt/nfs-smoke/nfs-probe
rm /mnt/nfs-smoke/nfs-probe
""")
            finally:
                self.node(client, "umount", "/mnt/nfs-smoke")
            self.ceph("nfs", "export", "rm", "smoke", "/smoke")

    def manager_url(self, module):
        return wait_for(f"{module} endpoint", lambda:
                        self.ceph_json("mgr", "services").get(module))

    def dashboard(self):
        self.ceph("mgr", "module", "enable", "dashboard")
        self.ceph("dashboard", "create-self-signed-cert")
        password = secrets.token_urlsafe(24) + "aA1!"
        self.cmd.secrets.add(password)
        self.put(self.nodes[0], f"{WORK}/dashboard-password", password)
        self.ceph("dashboard", "ac-user-create", "smoke-admin",
                  "administrator", "-i", "/work/dashboard-password",
                  sensitive=True)
        url = self.manager_url("dashboard").rstrip("/")
        credentials = json.dumps({"username": "smoke-admin",
                                  "password": password})
        curl = ["curl", "-kfsS", "--connect-timeout", "10", "--max-time", "30",
                "-H", "Accept: application/vnd.ceph.api.v1.0+json"]
        login = wait_for("dashboard login", lambda: json.loads(self.node(
            self.nodes[0], *curl, "-H", "Content-Type: application/json",
            "--data-binary", "@-", f"{url}/api/auth", input=credentials,
            sensitive=True)))
        self.cmd.secrets.add(login["token"])
        self.node(self.nodes[0], *curl, "-H",
                  f"Authorization: Bearer {login['token']}",
                  f"{url}/api/health/minimal", sensitive=True)
        self.ceph("dashboard", "ac-user-delete", "smoke-admin")

    def prometheus(self):
        self.ceph("mgr", "module", "enable", "prometheus")
        url = self.manager_url("prometheus").rstrip("/") + "/metrics"
        wait_for("Ceph metrics", lambda: "ceph_health_status" in self.node(
            self.nodes[0], "curl", "-fsS", "--max-time", "30", url))

    def rbd_mirror(self):
        self.pool("smoke-mirror")
        self.container("rbd", "pool", "init", "smoke-mirror")
        self.container("rbd", "mirror", "pool", "enable", "smoke-mirror",
                       "image")
        with self.service("rbd-mirror"):
            self.ceph("orch", "apply", "rbd-mirror", "--placement",
                      f"1 {self.nodes[2]}")
            self.service_ready("rbd-mirror")

    def cephfs_mirror(self):
        self.filesystem()
        self.ceph("mgr", "module", "enable", "mirroring")
        self.ceph("fs", "snapshot", "mirror", "enable", "smoke-fs")
        with self.service("cephfs-mirror"):
            self.ceph("orch", "apply", "cephfs-mirror", "--placement",
                      f"1 {self.nodes[2]}")
            self.service_ready("cephfs-mirror")
        self.ceph("fs", "snapshot", "mirror", "disable", "smoke-fs")

    def iscsi(self):
        self.node(self.nodes[1], "modprobe", "target_core_user")
        self.pool("smoke-iscsi")
        self.container("rbd", "pool", "init", "smoke-iscsi")
        password = secrets.token_urlsafe(24)
        self.cmd.secrets.add(password)
        spec = {"service_type": "iscsi", "service_id": "smoke-iscsi",
                "placement": {"hosts": [self.nodes[1]]},
                "spec": {"pool": "smoke-iscsi", "api_user": "smoke-admin",
                         "api_password": password, "api_secure": False,
                         "trusted_ip_list": ",".join(self.ips.values())}}
        self.put(self.nodes[0], f"{WORK}/iscsi.json", json.dumps(spec))
        with self.service("iscsi.smoke-iscsi"):
            self.ceph("orch", "apply", "-i", "/work/iscsi.json",
                      sensitive=True)
            self.service_ready("iscsi.smoke-iscsi")
            url = f"http://{self.ips[self.nodes[1]]}:5000/api/config"
            wait_for("iSCSI API", lambda: isinstance(json.loads(self.node(
                self.nodes[0], "curl", "-fsS", "--max-time", "30", "--user",
                f"smoke-admin:{password}", url, sensitive=True)), dict))

    def checks(self):
        return [("Cephadm hosts/quorum", self.core),
                ("RBD data round-trip", self.rbd),
                ("CephFS FUSE round-trip", self.cephfs),
                ("RGW S3 round-trip", self.rgw),
                ("NFS round-trip", self.nfs),
                ("Dashboard API", self.dashboard),
                ("Prometheus metrics", self.prometheus),
                ("RBD mirror startup", self.rbd_mirror),
                ("CephFS mirror startup", self.cephfs_mirror),
                ("iSCSI gateway API", self.iscsi)]

    def diagnostics(self):
        probes = [("runner-memory", ["free", "-h"]),
                  ("runner-disk", ["df", "-h"]),
                  ("runner-kernel", ["journalctl", "--no-pager", "-k",
                                     "-n", "300"]),
                  ("lxd-instances", ["lxc", "list", self.prefix,
                                     "-c", "ns4t", "--format=csv"])]
        for name in self.instances:
            for label, args in (
                    ("journal", ["journalctl", "--no-pager", "-n", "1500",
                                 "-u", "ceph-*", "-u", "docker"]),
                    ("disks", ["lsblk", "-f"]),
                    ("kernel", ["journalctl", "--no-pager", "-k",
                                "-n", "300"]),
                    ("containers", ["docker", "ps", "-a"]),
                    ("memory", ["free", "-h"])):
                probes.append((f"{name}-{label}",
                               ["lxc", "exec", name, "--", *args]))
        for label, args in probes:
            try:
                text = self.cmd.run(args, timeout=30)
            except Exception as error:
                text = str(error)
            (self.output / f"{label}.txt").write_text(self.cmd.redact(text))
        if self.image:
            for name, args in (
                    ("status", ["status"]), ("health", ["health", "detail"]),
                    ("hosts", ["orch", "host", "ls"]),
                    ("daemons", ["orch", "ps", "--refresh"]),
                    ("modules", ["mgr", "module", "ls"])):
                try:
                    text = self.ceph(*args, "--format=json", timeout=30)
                except Exception as error:
                    text = str(error)
                (self.output / f"ceph-{name}.json").write_text(
                    self.cmd.redact(text))

    def cleanup(self):
        errors = []
        commands = []
        for name in reversed(self.instances):
            commands.append(["lxc", "delete", "--force", name])
        for name in reversed(self.volumes):
            commands.append(["lxc", "storage", "volume", "delete",
                             "default", name])
        for rule in self.rules:
            commands.append(["iptables", "-D", "FORWARD", *rule])
        if self.network_created:
            commands.append(["lxc", "network", "delete", self.network])
        for args in commands:
            try:
                self.cmd.run(args, timeout=60)
            except Exception as error:
                errors.append(str(error))
        return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rock", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.rock.is_file():
        parser.error("--rock must name the downloaded candidate artifact")
    args.output.mkdir(parents=True, exist_ok=True)
    cluster = Cluster(args.rock.resolve(), args.output.resolve())
    results = []

    def interrupted(signum, frame):
        raise KeyboardInterrupt("CI job terminated")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        cluster.provision()
        run_checks(cluster.checks(), args.output, results)
    except (Exception, KeyboardInterrupt) as error:
        results.append({"name": "fixture", "status": "failed", "seconds": 0,
                        "error": str(error)})
        print(f"Fixture failed: {error}", flush=True)
    finally:
        try:
            cluster.diagnostics()
        finally:
            errors = cluster.cleanup()
            if errors:
                results.append({"name": "cleanup", "status": "failed",
                                "seconds": 0, "error": "\n".join(errors)})
            write_results(results, args.output)
    return int(any(result["status"] != "passed" for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
