# Cephadm CephX key rotation

Run this test suite through **tox**, which creates the environments and installs
Robot Framework. No workflow or deployment helper automatically runs the suite.

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

On a **disposable, single-host cephadm machine** with three healthy OSDs:

```sh
sudo tox -e key-rotation
```

The host must already have cephadm, systemd, Python, and tox installed. Its Ceph
image must support `ceph auth rotate`, `ceph-authtool`, `ceph-bluestore-tool`,
`rados`, and the manager's volumes module. Tox installs the Python dependencies;
it does not provision a cluster or install host packages.

The suite deploys CephFS to include a real MDS, then follows
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
are excluded from these reports. Protected recovery files remain under
`/root/ceph-key-rotation-*` on failure; **never upload these files**. No automatic
rollback is attempted. On success the dedicated client and temporary credentials
are removed. Test pools and the filesystem remain for disposal with the VM.
