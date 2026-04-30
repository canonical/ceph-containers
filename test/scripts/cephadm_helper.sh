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
  ls
  rock_file=$(ls *.rock | head -1)
  docker run -d -p 5000:5000 --restart=always --name registry registry:2
  sleep 10
  rockcraft.skopeo --insecure-policy copy \
    --dest-tls-verify=false \
    oci-archive:$rock_file \
    docker://localhost:5000/canonical/ceph:latest
  sudo touch /etc/docker/daemon.json
  # dont need insecure registry since it's localhost.
  # echo $'[registries.insecure]\nregistries = ["localhost:5000"]' | sudo tee -a /etc/docker/daemon.json
  # jq --arg key "insecure-registries" --arg value "${1}:5000" '.[$key] += [$value]' /etc/docker/daemon.json > tmp.$$.json && sudo mv tmp.$$.json /etc/docker/daemon.json
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
    local image="${1:?missing}"
    local ip="${2:?missing}"
    sudo cephadm --image $image bootstrap --mon-ip $ip --single-host-defaults --skip-dashboard --skip-monitoring-stack
    df -H
}

function get_ip() {
    ip -4 -j route | jq -r '.[] | select(.dst | contains("default")) | .prefsrc' | tr -d '[:space:]'
}

function setupLXDMachine() {
    sudo lxc launch --vm ubuntu:22.04 cephadm0 -c limits.cpu=4 -c limits.memory=8GiB
    sleep 30s

    sudo lxc file push ./test/scripts/cephadm_helper.sh cephadm0/root/
    sudo lxc file push ./*.rock cephadm0/root/

    sudo lxc shell cephadm0 -- sh -c "snap install rockcraft --classic"
}

function runOnHost() {
  local host=${1:?missing}
  shift
  sudo lxc shell $host -- /root/cephadm_helper.sh $@
}

function add_storage_devices() {
  sudo lxc storage volume create default osd0 size=10GiB --type block
  sudo lxc storage volume create default osd1 size=10GiB --type block
  sudo lxc storage volume create default osd2 size=10GiB --type block

  sudo lxc storage volume attach default osd0 cephadm0
  sudo lxc storage volume attach default osd1 cephadm0
  sudo lxc storage volume attach default osd2 cephadm0
}

function deploy_cephadm() {
    local image=${1:?missing}
    install_apt
    bootstrap $image $( get_ip )
    test_num_objs mon 1
}

function deploy_osd() {
  echo "=== Pre-OSD host device state ==="
  lsblk
  # The Quincy orchestrator's per-host device cache is populated
  # asynchronously and a single up-front --refresh does not block on
  # completion. ceph-volume sees the LXD-attached block volumes
  # immediately, but `ceph orch device ls` returns an empty list until
  # the mgr/cephadm module pulls them in. Re-issue --refresh on each
  # poll iteration to nudge the cache.
  local available=0
  for i in {1..20}; do
    echo "=== device inventory (attempt $i) ==="
    sudo cephadm shell -- ceph orch device ls --refresh
    sleep 15
    sudo cephadm shell -- ceph orch device ls
    available=$(sudo cephadm shell -- ceph orch device ls --format json-pretty 2>/dev/null \
      | jq '[.. | objects | select(has("available")) | select(.available == true)] | length' 2>/dev/null || echo 0)
    echo "available device count: ${available}"
    if [ "${available}" -ge 3 ]; then
      break
    fi
  done
  if [ "${available}" -lt 3 ]; then
    echo "deploy_osd: timed out waiting for >=3 available devices (last seen: ${available})" >&2
    exit 1
  fi
  sudo cephadm shell -- ceph orch apply osd --all-available-devices
}

function get_num_objs() {
    local what=${1:?missing}
    sudo cephadm shell -- ceph status -f json | jq -r ".${what}map | .num_${what}s"
}

function wait_num_objs() {
  local what=${1:?missing}
  local expect=${2:?missing}

  for i in {1..15}; do
    num_objs=$( get_num_objs $what )
    if [ "$num_objs" == "$expect" ]; then
      break
    else
      echo "$what $expect, got $num_objs..."
      sleep 30s
    fi
  done

  if [ "$num_objs" != "$expect" ]; then
    echo "Timedout waiting for $what to reach $expect count"
    exit 1
  fi
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
        exit 1
    fi
}

FUNCTION="$1"
shift
$FUNCTION "$@"
