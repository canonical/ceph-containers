# Ceph ROCK Hacking Guide
The aim of this document is to enable new developers/users get familiarised with the build tools and Ceph ROCK repository so that they can build and contribute to [Ceph-Containers](https://github.com/canonical/ceph-containers).

## Table of Contents
1. [Introduction to ROCKs](#introduction-to-rocks)
2. [Tools](#tools)
3. [References](#references)
4. [Build Guide](#build-guide)
6. [Uisng Ceph ROCKs](#using-ceph-rocks)

## Introduction to ROCKs
> A [ROCK](https://canonical-rockcraft.readthedocs-hosted.com/en/latest/explanation/rocks/) is an [OCI](https://en.wikipedia.org/wiki/Open_Container_Initiative) compliant container archive built on top of Ubuntu LTS releases. ROCKs are interoperable with all OCI-compliant tools and platforms.

The Ceph ROCK packages all the required ceph-binaries along with utilities, [kubectl](https://kubernetes.io/docs/reference/kubectl/), [confd](https://github.com/kelseyhightower/confd), some configuration files and scripts.

## Tools
ROCKs are built using a snap called [rockcraft](https://github.com/canonical/rockcraft). It uses [lxd](https://github.com/canonical/lxd) to pull dependencies and build an artefact completely isolated from the host system. This makes it easier for developers to work on ROCKs without polluting their host system with unwanted dependencies.
You can install rockcraft and lxd using snap tool.
```bash
sudo snap install rockcraft --classic
sudo snap install lxd
```

> **_NOTE:_**
For a detailed how-to-use rockcraft tool guide, check-out [ROCK Docs](https://canonical-rockcraft.readthedocs-hosted.com/en/latest/)

## References
The `rockcraft` tool uses a declarative file called `rockcraft.yaml` as the blueprint to prepare the artefact. Below is a brief description of all the `parts` that make the Ceph ROCK.

### Parts
1. **[ceph](rockcraft.yaml#L26)**

    Contains the related ceph and utility packages that are fetched from the ubuntu package repository for building this part.

2. **[confd](rockcraft.yaml#L76)**

    Confd is a configuration management system that can actively watch a consistent kv store like etcd and change config files based on templates. Used for rook based deployments.

3. **[kubectl](rockcraft.yaml#L86)**

    Kubernetes provides a command line tool for communicating with the cluster's control plane, using the Kubernetes API. This tool is named `kubectl`. Kubectl is built using [kubectl.go](kubectl/kubectl.go) and `go` snap.

4. **[local-files](rockcraft.yaml#L93)**

    Contains bash scripts, configuration files (s3cfg, ceph.defaults, confd templates, etc) that go in the artefact.

5. **[ceph-container-service](rockcraft.yaml#L19)**

    ROCKs use [pebble](https://github.com/canonical/pebble) as the official entrypoint of the OCI artefact. `ceph-container-service` is the pebble definiton of the entrypoint to the ROCK. However, orchestration tools like [Rook](https://rook.io/) and [Cephadm](https://docs.ceph.com/en/latest/cephadm/) don't rely on the configured entrypoint and override it when spawning containers. Hence, the pebble entrypoint is an experimental feature that has not been tested for production deployments.


## Build Guide
Building Ceph ROCK is as easy as a snap!
```bash
rockcraft -v
...
Starting Rockcraft 0.0.1.dev1
Logging execution to '/home/ubuntu/.local/state/rockcraft/log/rockcraft-20230904-104703.679764.log'
Launching instance..
...
Control data written
Metadata added
Exporting to OCI archive
Exported to OCI archive 'ceph_0.1_amd64.rock'
```

The newly created .rock artefact can be loaded into docker for subsequent operations using skopeo as:
```bash
sudo /snap/rockcraft/current/bin/skopeo --insecure-policy copy oci-archive:ceph_0.1_amd64.rock docker-daemon:canonical/ceph:latest
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
```bash
sudo docker images
REPOSITORY         TAG          IMAGE ID       CREATED       SIZE
canonical/ceph     latest       4d47f598e7ef   4 hours ago   1.51GB
```

## Automated local deployments using cephadm and lxd

We also provide a [python3 script](test/deploy.py) which can deploy a single node ceph cluster for you to tinker with using our image, cephadm and lxd. For this to work the host should have lxd snap installed and initialised.

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
