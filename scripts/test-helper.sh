#!/bin/bash

set -xeEo pipefail


function build_rock() {
  sudo snap install rockcraft --classic
  rockcraft clean
  rockcraft pack -v
  mv ceph*.rock ceph.rock
}

function load_to_docker() {
  local tags=${1:?missing}
  # iterate through the tags
  for tag in $tags; do
    echo "$tag"
    skopeo --insecure-policy copy oci-archive:ceph.rock docker-daemon:$tag
  done
  # Check all images
  docker image ls -a
  sleep 10
  docker push ghcr.io/canonical/ceph --all-tags
}



FUNCTION="$1"
shift # remove function arg now that we've recorded it
# call the function with the remainder of the user-provided args
# -e, -E, and -o=pipefail will ensure this script returns a failure if a part of the function fails
$FUNCTION "$@"
