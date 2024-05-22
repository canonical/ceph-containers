# Ceph rocks

Home to Ubuntu based Ceph [OCI][wikipedia-oci] images.

## What is a rock?

[Rocks][definition-rocks] are a new generation of secure, stable, and
OCI-compliant container images, based on Ubuntu LTS releases. They are
interoperable with all OCI compliant tools. For enthusiasts, we recommend
checking out our [Hacking Guide](HACKING.md).

## Using Ceph rocks

Ceph rocks are available via our [GitHub Packages Container
registry][github-ceph-containers-registry]. They are compatible with popular
containerised Ceph deployment tools such as:

* [Cephadm][ubuntu-discourse-cephadm]
* [Rook][ubuntu-discourse-ceph-rook]

## Supported features

Our rocks support two backends: Cephadm and Rook. The features that each
support are given below.

| Feature | Cephadm | Rook |
| ------- | ------- | ---- |
| Status | &#x1F7E2; | &#x1F7E2; |
| ps | &#x1F7E2; | &#x1F7E2; |
| ls | &#x1F7E2; | &#x1F7E2; |
| Hosts | &#x1F7E2; | &#x1F7E2; |
| Device ops | &#x1F7E2; | &#x1F7E2; |
| Dashboard | &#x1F7E2; | &#x1F7E2; |
| OSD ops | &#x1F7E2; | &#x1F7E2; |
| RBD Mirror | &#x1F7E2; | &#x1F7E2; |
| RadosGW | &#x1F7E2; | &#x1F7E1; |
| Daemon status | &#x1F7E2; | &#x1F7E1; |
| Upgrade | &#x1F7E2; | &#x1F7E1; |
| Hosts (maintenance) | &#x1F7E2; | &#x1F534; |
| ISCSI | &#x1F7E2; | &#x1F534; |
| Loki | &#x1F7E2; | &#x1F534; |
| Prometheus | &#x1F7E2; | &#x1F534; |
| Alert manager | &#x1F7E2; | &#x1F534; |
| Node exporter | &#x1F7E2; | &#x1F534; |

**Legend**

&#x1F7E2; : fully supported  
&#x1F7E1; : _technically_ supported - requires operator intervention  
&#x1F534; : not supported

<!-- LINKS -->

[wikipedia-oci]: https://en.wikipedia.org/wiki/Open_Container_Initiative
[definition-rocks]: https://canonical-rockcraft.readthedocs-hosted.com/en/latest/explanation/rocks/#rocks-explanation
[github-ceph-containers-registry]: https://github.com/canonical/ceph-containers/pkgs/container/ceph
[ubuntu-discourse-cephadm]: https://discourse.ubuntu.com/t/32408
[ubuntu-discourse-ceph-rook]: https://discourse.ubuntu.com/t/32565
