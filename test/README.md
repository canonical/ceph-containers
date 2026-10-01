# Cephadm feature smoke tests

`CephadmFeatureSmoke` is a breadth check of the candidate image, not a resilience,
performance, soak, or upgrade test. It runs only after **both** `CephadmTest` and
`RookTest` succeed and downloads the same `rock` artifact. Fork PRs are excluded
because their scripts must not execute on the organisation's self-hosted runners.

## Build runner

ROCKs are built locally with Rockcraft and LXD on standard GitHub-hosted
runners: `ubuntu-latest` for Canary builds and `ubuntu-22.04` for publishing.
Only the feature-smoke job uses a custom runner. No Launchpad credentials are
needed or copied into CI.

## Fixture and resource budget

The job uses `self-hosted-linux-amd64-noble-large` only. There is no automatic XL
fallback. The initial fixture is three Resolute LXD VMs, each with two vCPUs,
6 GiB RAM, a 15 GiB root disk and one dedicated 8 GiB OSD disk. The harness checks
for eight CPUs, at least 24 GiB RAM, 50 GiB free storage and accessible KVM before
creating anything. This is sized for the 8-CPU/32-GiB large runner; resource
snapshots are retained so changes can be based on observed limits.

Bootstrap uses normal multi-host defaults. The cluster has three monitors,
two managers and three OSDs, with a fixed 2 GiB OSD memory target and autotuning
disabled for a predictable smoke-test budget. A registry in the first VM serves
the candidate to all nodes. The candidate is referenced by digest, and running daemons' image IDs
are checked against its config digest. The global `container_image` setting is
pinned explicitly; Ceph 20.2 uses this setting for NFS and iSCSI as well as core
and mirror daemons.

## Coverage

| Check | Assertion |
| --- | --- |
| Cephadm | Three enrolled hosts and monitors in quorum; backend available |
| RBD | Import/export a small payload and compare bytes |
| CephFS | Mount using the candidate's ceph-fuse; write/read/delete a file |
| RGW | Signed S3 bucket/object create, read and delete; compare bytes |
| NFS | Export CephFS, mount from another VM, write/read/delete a file |
| Dashboard | Enable module, create certificate/user, authenticate, query health |
| Prometheus | Enable module and retrieve Ceph health metrics |
| RBD mirror | Enable pool mirroring and start the candidate daemon |
| CephFS mirror | Enable filesystem mirroring and start the candidate daemon |
| iSCSI | Start the gateway and query its authenticated configuration API |

Mirroring checks **do not verify peer replication**. The iSCSI check **does not
log in an initiator or perform LUN I/O**. Those limitations are deliberate: this
suite first checks deployment, dependencies and basic service usability across
the image's packaged components. It does not cover services shipped in other
images, such as SMB, NVMe-oF or the standalone monitoring stack.

Checks run sequentially to limit memory use. A feature failure is recorded while
later features still run. Temporary gateway/mirror services are removed after
their checks; the filesystem and tiny pools are shared or retained until fixture
teardown. Any failed feature makes the job fail. Infrastructure setup failure is
reported separately rather than pretending the feature matrix passed.

## Diagnostics and cleanup

`results.json` and `summary.md` report each feature separately. Candidate digest,
command logs, safe cluster queries, per-node journals and resource snapshots are
uploaded even on failure. Credentials and private keyrings are not deliberately
collected; sensitive commands omit their output, and generated API credentials
are redacted from diagnostics. Do not replace this with `ceph auth ls`, service
spec exports or unrestricted configuration dumps.

Each run creates uniquely named VMs, block volumes and a bridge. Firewall changes
are limited to that bridge; the harness never wipes host disks or flushes host
firewall rules. Diagnostics are captured **before** cleanup. The runner must
still be disposable: forced cancellation or runner loss can interrupt cleanup.

## Local use

Use a disposable host with LXD initialized and a `default` storage pool. Install
Rockcraft for `rockcraft.skopeo`, and provide a candidate ROCK already carrying
the `ceph=True` label (as the CI build does):

```sh
sudo python3 test/scripts/cephadm_feature_smoke.py \
  --rock /path/to/candidate.rock --output /path/to/feature-logs
```

This provisions a new cluster; it cannot target an existing one. No Launchpad
or AWS credentials are used.

Fast harness regression checks require only Python's standard library:

```sh
python3 -m unittest discover -s test/unit -v
```
