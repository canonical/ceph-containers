# Ceph ROCKs
Home to Ubuntu based Ceph [OCI](https://en.wikipedia.org/wiki/Open_Container_Initiative) Images.

## What on EARTH is a ROCK ?
[ROCKs](https://canonical-rockcraft.readthedocs-hosted.com/en/latest/explanation/rocks/#rocks-explanation) are a new generation of secure, stable and OCI-compliant container images, based on Ubuntu LTS releases. They are interoperable with all OCI compliant tools. For enthusiasts, we recommend checking out our [Hacking Guide](HACKING.md)

## How to download a pre-built image?
Visit our [Packages](https://github.com/canonical/ceph-containers/pkgs/container/ceph) page which includes instructions to install images, and also documents image versions and hashes.

## Using Ceph ROCKs
The Ceph ROCK available at the [GH Container Repository](https://github.com/canonical/ceph-containers/pkgs/container/ceph) can be used with popular containerised-ceph deployment tools like:
1. [Cephadm](https://discourse.ubuntu.com/t/using-cephadm-to-deploy-custom-ubuntu-ceph-images-in-a-containerised-manner/)
2. [Rook](https://discourse.ubuntu.com/t/deploying-ceph-with-rook/)

## What features are supported ?

We support 2 backends: Cephadm and Rook. The features each support differ, and this compatibility matrix aims to document what is achievable for both.


> **_NOTE:_**
A green light means that a particular feature is supported, and a red light means that it isn't. A yellow light means that a feature is _technically_ supported, but needs operator intervention.

| Feature | Cephadm | Rook | Feature | Cephadm | Rook |
| ------- | ------- | ---- | ------- | ------- | ---- |
| Upgrade | &#x1F7E2; | &#x1F7E1; | Dashboard | &#x1F7E2; | &#x1F7E2; |
| Status | &#x1F7E2; | &#x1F7E2; | Loki | &#x1F7E2; | &#x1F534; |
| ps | &#x1F7E2; | &#x1F7E2; | Prometheus | &#x1F7E2; | &#x1F534; |
| ls | &#x1F7E2; | &#x1F7E2; | Alert manager | &#x1F7E2; | &#x1F534; |
| RadosGW | &#x1F7E2; | &#x1F7E1; | Node exporter | &#x1F7E2; | &#x1F534; |
| Hosts | &#x1F7E2; | &#x1F7E2; | OSD ops | &#x1F7E2; | &#x1F7E2; |
| Hosts (maintenance) | &#x1F7E2; | &#x1F534; | ISCSI | &#x1F7E2; | &#x1F534; |
| Device ops | &#x1F7E2; | &#x1F7E2; | RBD Mirror | &#x1F7E2; | &#x1F7E2; |
| Status | &#x1F7E2; | &#x1F7E1; | | | |

