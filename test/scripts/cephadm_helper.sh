#!/bin/bash

set -xeEo pipefail

PACKAGES="cephadm openssh-server jq"

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
    sudo apt purge snapd -y
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
    echo '{"insecure-registries": []}' | sudo tee /etc/docker/daemon.json
    jq --arg key "insecure-registries" --arg value "${1}:5000" '.[$key] += [$value]' /etc/docker/daemon.json > tmp.$$.json && sudo mv tmp.$$.json /etc/docker/daemon.json
    sudo systemctl restart docker
}

function install_apt() {
    # Install Apt packages.
    DEBIAN_FRONTEND=noninteractive sudo apt update
    DEBIAN_FRONTEND=noninteractive sudo apt install $PACKAGES -y 
}

function bootstrap() {
    local image="${1:missing}"
    local ip="${2:?missing}"
    sudo cephadm --image $image bootstrap --mon-ip $ip --single-host-defaults
    df -H
}

function get_ip() {
    ip -4 -j route | jq -r '.[] | select(.dst | contains("default")) | .prefsrc' | tr -d '[:space:]'
}

function deploy_cephadm() {
    local image=${1:?missing}
    install_apt
    bootstrap $image $( get_ip )
    test_num_objs mon 1
}

function get_num_objs() {
    local what=${1:?missing}   
    sudo cephadm shell -- ceph status -f json | jq -r ".${what}map | .num_${what}s"
}

function test_num_objs() {
    local what=${1:?missing}
    local expect=${2:?missing}
    
    num_objs=$( get_num_objs $what )
    if [ $num_objs == $expect ]; then
        echo "[OK] test_num_objs $what $expect"
    else
        echo "[FAIL] test_num_objs $what $expect, got $num_objs"
        echo "Ceph status"
        sudo cephadm shell -- ceph status
        exit -1
    fi
}

FUNCTION="$1"
shift
$FUNCTION "$@"