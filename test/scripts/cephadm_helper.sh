#!/bin/bash

set -xeEo pipefail

# Workarounds for missing Depends in resolute's cephadm package:
#  - python3-ceph-common ships the ceph.cephadm.images module that
#    cephadm 20.2.0 imports at startup
#  - ceph-common creates the ceph daemon user (uid/gid 64045) that
#    cephadm bootstrap chowns its runtime dirs to
# Tracked via https://bugs.launchpad.net/ubuntu/+source/ceph/+bug/2150665.
PACKAGES="cephadm openssh-server jq python3-ceph-common ceph-common"

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
  rockcraft.skopeo inspect --tls-verify=false docker://localhost:5000/canonical/ceph:latest | jq -e '.Labels.ceph == "True"'
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

function capture_bootstrap_diagnostics() {
    # Diagnostic-only: dump mgr state and logs to help diagnose the
    # PyO3 / "subinterpreters" failure when `mgr module enable cephadm`
    # aborts during `cephadm bootstrap`. Does not attempt to fix anything.
    echo "=== capture_bootstrap_diagnostics: ceph mgr module ls ==="
    sudo cephadm shell -- ceph mgr module ls || echo "(ceph mgr module ls failed)"

    echo "=== capture_bootstrap_diagnostics: cephadm ls ==="
    sudo cephadm ls || echo "(cephadm ls failed)"

    echo "=== capture_bootstrap_diagnostics: ceph-mgr daemon log files ==="
    # /var/log/ceph/<fsid>/ceph-mgr.<host>.<rand>.log holds the Python
    # traceback for the failed module load.
    local mgr_logs
    mgr_logs=$(sudo find /var/log/ceph -maxdepth 2 -type f -name 'ceph-mgr.*.log' 2>/dev/null || true)
    if [ -n "$mgr_logs" ]; then
        for f in $mgr_logs; do
            echo "--- $f ---"
            sudo cat "$f" || echo "(could not read $f)"
        done
    else
        echo "(no /var/log/ceph/*/ceph-mgr.*.log files found)"
    fi

    echo "=== capture_bootstrap_diagnostics: journalctl ceph-*@mgr.* ==="
    sudo journalctl --no-pager -n 200 -u 'ceph-*@mgr.*' || echo "(journalctl failed)"
}

function bootstrap() {
    local image="${1:?missing}"
    local ip="${2:?missing}"
    # Run cephadm bootstrap in a subshell so we can capture diagnostics on
    # failure without losing the original non-zero exit code.
    sudo cephadm --image "$image" bootstrap --mon-ip "$ip" --single-host-defaults --skip-dashboard --skip-monitoring-stack || {
        rc=$?
        echo "cephadm bootstrap failed with exit code $rc; capturing diagnostics" >&2
        capture_bootstrap_diagnostics || true
        exit "$rc"
    }
    df -H
}

function get_ip() {
    ip -4 -j route | jq -r '.[] | select(.dst | contains("default")) | .prefsrc' | tr -d '[:space:]'
}

function setupLXDMachine() {
    sudo lxc launch --vm ubuntu:26.04 cephadm0 -c limits.cpu=4 -c limits.memory=8GiB
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
