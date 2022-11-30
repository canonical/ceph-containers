#!/bin/bash

set -xeEo pipefail

PACKAGES="cephadm jq"

function install_pkg() {
    sudo apt-get update
    sudo apt-get install -y $PACKAGES
}

function bootstrap() {
    local image="${1:missing}"
    local ip="${2:?missing}"
    sudo cephadm --image $image bootstrap --mon-ip $ip
}

function get_ip() {
    ip -4 -j route | jq -r '.[] | select(.dst | contains("default")) | .prefsrc' | tr -d '[:space:]'
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


function add_osds() {
    sudo cephadm shell -- ceph orch apply osd --all-available-devices
    # give osd some time to show up
    for n in {0..15} ; do
        sleep 2
        num_osd=$( get_num_objs osd )
        if [ $num_osd -eq 1 ] ; then
            break
        fi
    done
    test_num_objs osd 1
}

function deploy_cephadm() {
    local image=${1:?missing}
    install_pkg
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
shift # remove function arg now that we've recorded it
# call the function with the remainder of the user-provided args
# -e, -E, and -o=pipefail will ensure this script returns a failure if a part of the function fails
$FUNCTION "$@"
