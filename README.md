# Ubuntu Ceph Container Images
Home to Ubuntu based Ceph container Images.

## What features are supported ?

We support 2 backends: Cephadm and Rook. The features each support differ, and this compatibility matrix aims to document what is achievable for both.

> **_NOTE:_**
A green light means that a particular feature is supported, and a red light means that it isn't. A yellow light means that a feature is _technically_ supported, but needs a considerable amount of fiddling.

| Feature | Cephadm | Rook |
| ------- | ------- | ---- |
| Upgrade | &#x1F7E2; | &#x1F7E1; |
| Status | &#x1F7E2; | &#x1F7E2; |
| ps | &#x1F7E2; | &#x1F7E2; |
| ls | &#x1F7E2; | &#x1F7E2; |
| RadosGW | &#x1F7E2; | &#x1F7E1; |
| Hosts | &#x1F7E2; | &#x1F7E2; |
| Hosts (maintenance) | &#x1F7E2; | &#x1F534; |
| Device ops | &#x1F7E2; | &#x1F7E2; |
| Status | &#x1F7E2; | &#x1F7E1; |
| Dashboard | &#x1F7E2; | &#x1F7E2; |
| Loki | &#x1F7E2; | &#x1F534; |
| Prometheus | &#x1F7E2; | &#x1F534; |
| Alert manager | &#x1F7E2; | &#x1F534; |
| Node exporter | &#x1F7E2; | &#x1F534; |
| OSD ops | &#x1F7E2; | &#x1F7E2; |
| ISCSI | &#x1F7E2; | &#x1F534; |
| RBD Mirror | &#x1F7E2; | &#x1F7E2; |

## How to download pre-built images ?
Visit our [Packages](https://github.com/canonical/ceph-containers/pkgs/container/ceph) page which includes instructions to install images, and also documents image versions and hashes.

## How to build

We provide rockraft.yaml that can be used to build the image and the supporting scripts that go with it. Hence we can simply use rockcraft snap as follows:
```
$ sudo snap install rockcraft
$ rockcraft -v
...
Setting the ROCK's Control Data                                                
Control data written                                                           
Metadata added                                                                 
Exporting to OCI archive                                                       
Exported to OCI archive 'ceph_0.1_amd64.rock' 
```

The freshly built container archive can be loaded into docker for subsequent operations using [skopeo](https://github.com/containers/skopeo) as:
```
$ sudo /snap/rockcraft/current/bin/skopeo --insecure-policy copy oci-archive:ceph_0.1_amd64.rock docker-daemon:canonical/ceph:latest
Getting image source signatures
Copying blob 3153aa388d02 done  
Copying blob e3162b5ec315 done  
Copying blob 9131ac168a8b done  
Copying blob 0e56abe5b4e6 done  
Copying config 4d47f598e7 done  
Writing manifest to image destination
Storing signatures
```

All images available locally can be checked through:
```
$ sudo docker images
REPOSITORY         TAG          IMAGE ID       CREATED       SIZE
canonical/ceph     latest       4d47f598e7ef   4 hours ago   1.51GB
```

This freshly baked Image can now be used for deploying Ceph using:
1. [Cephadm](https://discourse.ubuntu.com/t/using-cephadm-to-deploy-custom-ubuntu-ceph-images-in-a-containerised-manner/)
2. [Rook](https://discourse.ubuntu.com/t/deploying-ceph-with-rook/)

## Automated local deployments using cephadm and lxd

We also provide a python3 script which can deploy a single node ceph cluster for you to tinker with using our image, cephadm and lxd. For this to work the host should have lxd snap installed and initialised.

```
$ sudo snap install lxd
$ lxd init --auto
```

### Script Usage:
1.) Use Script to deploy a custom image:
```
python3 test/deploy.py image <qualified_image_name>
```
> **_NOTE:_**
<qualified_image_name> can be a reference to a container image hosted on any public container registry.
For Example:
```
$ python3 test/deploy.py image ghcr.io/canonical/ceph:main
```
2.) Use Script to clean a deployment (using script generated model file):
```
python3 test/deploy.py delete model-88HJ.json
```

> **_NOTE:_**
You can also use the script to deploy (experimentally) on a LXD container (rather than a VM) using '--container 1', this can be intersting when no KVM support is available on the machine. However, this is not recommended.
For detailed info on script usage use:
```
python3 test/deploy.py --help
```

VM prepared by Script: 
```
ubuntu@lxdhost:~$ lxc ls
+------------------+---------+------------------------+-------------------------------------------------+-----------------+-----------+
|       NAME       |  STATE  |          IPV4          |                      IPV6                       |      TYPE       | SNAPSHOTS |
+------------------+---------+------------------------+-------------------------------------------------+-----------------+-----------+
| ubuntu-ceph-O8HV | RUNNING | 172.17.0.1 (docker0)   | fd42:a569:86fc:7cd5:216:3eff:fea9:5b45 (enp5s0) | VIRTUAL-MACHINE | 0         |
|                  |         | 10.159.51.242 (enp5s0) |                                                 |                 |           |
+------------------+---------+------------------------+-------------------------------------------------+-----------------+-----------+
ubuntu@lxdhost:~$ lxc shell ubuntu-ceph-O8HV 
root@ubuntu-ceph-O8HV:~# cephadm shell -- ceph status
  cluster:
    id:     0506c10c-97e1-11ed-82fe-00163ea95b45
    health: HEALTH_OK
 
  services:
    mon: 1 daemons, quorum ubuntu-ceph-O8HV (age 9m)
    mgr: ubuntu-ceph-O8HV.eumeve(active, since 5m)
    osd: 3 osds: 3 up (since 3m), 3 in (since 4m)
 
  data:
    pools:   1 pools, 1 pgs
    objects: 2 objects, 577 KiB
    usage:   64 MiB used, 30 GiB / 30 GiB avail
    pgs:     1 active+clean
 
  progress:
    Updating prometheus deployment (+1 -> 1) (0s)
      [............................] 
 
root@ubuntu-ceph-O8HV:~#
```
