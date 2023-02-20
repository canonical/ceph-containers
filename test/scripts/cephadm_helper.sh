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

function use_local_disk() {
    sudo lsblk -f
    datadisk=$( sudo lsblk --paths | awk '/14G/ {print $1}' | head -1 )
    sudo dmsetup version || true
    sudo swapoff --all --verbose
    sudo umount /mnt

    sudo sgdisk --zap-all -- $datadisk
    sudo sgdisk --clear --mbrtogpt -- $datadisk
    end=$(( $( sudo blockdev --getsz $datadisk ) - 100 ))
    sudo dd if=/dev/zero of=$datadisk bs=1M count=1
    sudo dd if=/dev/zero of=$datadisk bs=512 count=100 seek=${end?}
    sudo lsblk -f
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