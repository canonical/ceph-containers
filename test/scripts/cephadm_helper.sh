#!/bin/bash

set -xeEo pipefail

function prep_docker() {
    # Run a local registry.
    docker run -d -p 5000:5000 --name registry registry:2
    sleep 10
    # Build Ubuntu Ceph container image.
    docker build -t localhost:5000/canonical/ceph:latest $@
    # Push to local registry.
    docker push localhost:5000/canonical/ceph:latest
}

function prep_docker_for_tar() {
    echo "Prepping Docker to serve tarfile:"
    echo $@
    # Run a local registry.
    docker run -d -p 5000:5000 --name registry registry:2
    sleep 10
    docker load --input $@
    docker image ls -a
    docker image tag canonical/ceph:latest localhost:5000/canonical/ceph:latest
    docker push localhost:5000/canonical/ceph:latest
}

function create_loopd_on_host() {
    # Create loop device to be used as OSDs on github runner.
    sudo dd if=/dev/zero of=/mnt/loop/block.img bs=1 count=0 seek=10G
    sudo losetup -fP /mnt/loop/block.img
}

function configure_insecure_registry() {
    echo '{"insecure-registries": []}' > /etc/docker/daemon.json
    jq --arg key "insecure-registries" --arg value "${1}:5000" '.[$key] += [$value]' /etc/docker/daemon.json > tmp.$$.json && sudo mv tmp.$$.json /etc/docker/daemon.json
    systemctl restart docker
}

function install_apt() {
    # Install Apt packages.
    DEBIAN_FRONTEND=noninteractive sudo apt update
    DEBIAN_FRONTEND=noninteractive sudo apt install -y cephadm openssh-server jq
}

FUNCTION="$1"
shift
$FUNCTION "$@"