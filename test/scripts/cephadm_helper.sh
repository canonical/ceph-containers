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

function install_dependencies() {
  sudo apt-get -y update
  sudo apt-get -y install skopeo
  sudo snap install docker
  sleep 10
}

function prep_registry() {
  ls
  rock_file=$(ls *.rock | head -1)
  docker run -d -p 5000:5000 --restart=always --name registry registry:2
  sleep 10
  skopeo --insecure-policy copy oci-archive:$rock_file docker-daemon:canonical/ceph:latest
  docker image ls -a
  docker image tag canonical/ceph:latest localhost:5000/canonical/ceph:latest
  sleep 10
  docker push localhost:5000/canonical/ceph
  echo $'[registries.insecure]\nregistries = ["localhost:5000"]' | sudo tee -a /etc/containers/registries.conf
  sleep 30
}

function use_local_disk() {
    sudo lsblk -f
    datadisk=$(sudo lsblk --paths | awk '/14G/ || /64G/ {print $1}' | head -1)
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

function set_cloud_archive() {
    # Set provided cloud archive
    DEBIAN_FRONTEND=noninteractive sudo apt install software-properties-common -y
    DEBIAN_FRONTEND=noninteractive sudo add-apt-repository cloud-archive:$1 -y
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
    local ip=$( get_ip )

    install_apt
    bootstrap $ip:5000/canonical/ceph:latest $ip
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

function poll_obj_count() {
  local what=${1:?missing}
  local count=${2:?missing}
  local timeout=${3:?missing}

  echo "Polling for $what to reach $count under $timeout"
  i=0
  for i in $(seq 1 10); do
    num_objs=$( get_num_objs $what )
    if [ $num_objs == $count ]; then
      echo "$what reached $count in ${i}th iteration."
      break
    else 
      echo "."
      sleep 30
    fi
  done

  if [ $i -eq 10 ]; then
    echo "Timeout waiting for $what, only reached $( get_num_objs $what )"
    exit -1
  fi
}

FUNCTION="$1"
shift
$FUNCTION "$@"
