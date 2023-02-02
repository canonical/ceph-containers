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

## How to build

We provide a Dockerfile that can be used to build the image and the supporting scripts that go with it. Hence we can simply use Docker to build an image:
```
$ sudo docker build -t canonical/ceph:latest .
Sending build context to Docker daemon  1.655MB
Step 1/27 : FROM ubuntu:jammy
 ---> 6b7dfa7e8fdb
...
...
 ---> Running in f431f665b976
Removing intermediate container f431f665b976
 ---> 4edce85e2d97
Successfully built 4edce85e2d97
Successfully tagged canonical/ceph:latest
```

**_NOTE:_**
Due to a provisional fix additional build arguments are temporarily required to be provided for building the container image.
```
$ sudo docker build -t canonical/ceph:latest --build-arg CUSTOM_APT_REPO=ppa:peter-sabaini/ceph-test .
Sending build context to Docker daemon  1.655MB
...
Successfully tagged canonical/ceph:latest
```

All images available locally can be checked through:
```
$ sudo docker images
REPOSITORY     TAG    IMAGE ID     CREATED            SIZE
canonical/ceph latest 4edce85e2d97 About a minute ago 1.49GB
ubuntu         jammy  6b7dfa7e8fdb 4 weeks ago        77.8MB
```

This freshly baked Image can now be used for deploying Ceph using:
1. [Cephadm](https://discourse.ubuntu.com/t/using-cephadm-to-deploy-custom-ubuntu-ceph-images-in-a-containerised-manner/)
2. [Rook](https://discourse.ubuntu.com/t/deploying-ceph-with-rook/)

## Automated local deployments using cephadm and lxd

We also provide a python3 script which can deploy a single node ceph cluster for you to tinker with using our image, cephadm and lxd. For this to work the host should have lxd snap installed.

```$ sudo snap install lxd```

### Script Usage:
1.) Use Script to deploy a new Cephadm host:
```
python3 test/deploy.py
```
2.) Use Script to clean a deployment:
```
python3 test/deploy.py delete <model_file_path>
```
3.) Use Script to deploy a custom image:
e.g. <image_name>: ceph/ceph:latest
```
python3 test/deploy.py image <image_name>
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
