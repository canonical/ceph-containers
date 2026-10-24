# Cephadm CephX key rotation

Run this test suite through **tox**, which creates the environments and installs
Robot Framework. The `CephadmRobotTest` CI job provisions an empty Ubuntu VM,
loads the built image into its local registry, attaches three data disks, then
runs `tox -e key-rotation` inside the VM. Robot bootstraps and orchestrates the
cluster itself. The old `CephadmTest` job is retained but disabled.

## Local checks (no cluster)

```sh
tox
```

The default environment performs a Robot dry run, validating scenario syntax
and keyword resolution without executing cluster commands. It can also be
selected explicitly:

```sh
tox -e key-rotation-dryrun
```

## Live rotation test

On an **empty, disposable machine with three unused data disks** (all available
OSD devices will be consumed):

```sh
sudo tox -e key-rotation
```

The host must already have cephadm, a supported container runtime, systemd,
OpenSSH, iproute2, Python, and tox installed. There must be no existing Ceph
daemons or `/etc/ceph/ceph.conf`; the suite refuses to overwrite a cluster.
Tox installs Python dependencies, not host packages or virtual machines.

By default the suite uses `localhost:5000/canonical/ceph:latest`, as loaded by CI.
To use another accessible Ceph image:

```sh
sudo env CEPH_IMAGE=ghcr.io/canonical/ceph:main tox -e key-rotation
```

The image must support `ceph auth rotate`, `ceph-authtool`,
`ceph-bluestore-tool`, `rados`, and the manager's volumes module.

The suite bootstraps a single-node cluster, deploys three OSDs, and deploys
CephFS to include a real MDS. It then follows
[Ceph's documented credential rotation procedure](https://docs.ceph.com/en/latest/rados/configuration/auth-config-ref/#upgrading-and-rotating-cephx-keys):

- Rotate the shared `mon.` key and restart the in-quorum monitors, preserving
  their local emergency fallback keyrings.
- Stop, rotate, update the keyring, and start every manager, OSD, and MDS.
  OSD rotation also updates the BlueStore `osd_key` label.
- Require changed keys, unchanged existing capabilities, daemon recovery,
  clean PGs, RADOS read/write integrity, and CephFS metadata access.
- Rotate a dedicated pool-scoped client, import its new key, verify reads and
  writes with fresh processes, and require authentication denial for its old key.

This tests rotation using the preferred cipher, not cipher migration or
uninterrupted availability. It does not rotate `client.admin`, bootstrap, crash,
or other existing client credentials, or wipe rotating service ticket keys.
When `mon.` exists only in a monitor keyring, its first rotation creates the
authoritative auth-database entry rather than preserving nonexistent DB caps.

The live environment stops on the first failure. Robot reports are written to
`.tox/key-rotation/tmp/results/` by default. Credential material and command output
are excluded from these reports. Bootstrap output is retained privately with
recovery material, not uploaded. Protected recovery files remain under
`/root/ceph-key-rotation-*` on failure; **never upload these files**. No automatic
rollback is attempted. On success the dedicated client and temporary credentials
are removed. Test pools and the filesystem remain for disposal with the VM.
