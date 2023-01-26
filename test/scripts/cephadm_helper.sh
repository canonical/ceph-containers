#!/bin/bash

set -xeEo pipefail

function prep_docker() {
    # Run a local registry.
    docker run -d -p 5000:5000 --name registry registry:2
    # Build Ubuntu Ceph container image.
    docker build -t localhost:5000/canonical/ceph:latest $1
    # Push to local registry.
    docker push localhost:5000/canonical/ceph:latest
}

function install_apt() {
    # Install Apt packages.
    DEBIAN_FRONTEND=noninteractive sudo apt install -y cephadm openssh-server
}

FUNCTION="$1"
shift
$FUNCTION "$@"