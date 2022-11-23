#!/usr/bin/env bash

# Copyright 2021 The Rook Authors. All rights reserved.
# 
# Abridged and adapted by peter.sabaini@canonical.com
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -xeEo pipefail


function deploy_manifest_with_custom_image() {
  local yaml=${1:?missing}
  local img=${2:?missing}
  sed -i 's/.*ROOK_CSI_ENABLE_NFS:.*/  ROOK_CSI_ENABLE_NFS: \"true\"/g' $yaml
  if [[ "$USE_LOCAL_BUILD" != "false" ]]; then
    sed -i "s|image: rook/ceph:.*|image: $img|g" $yaml
  fi
  if [[ "$ALLOW_LOOP_DEVICES" = "true" ]]; then
    sed -i "s|ROOK_CEPH_ALLOW_LOOP_DEVICES: \"false\"|ROOK_CEPH_ALLOW_LOOP_DEVICES: \"true\"|g" $yaml
  fi
  sed -i "s|ROOK_LOG_LEVEL:.*|ROOK_LOG_LEVEL: DEBUG|g" "$yaml"
  kubectl create -f $yaml
}

function deploy_cluster() {
  local img=$( cat custom-image-spec )
  cd rook/deploy/examples
  deploy_manifest_with_custom_image operator.yaml $img
  if [ $# == 0 ]; then
    sed -i "s|#deviceFilter:|deviceFilter: ${BLOCK/\/dev\//}|g" cluster-test.yaml
  elif [ "$1" = "two_osds_in_device" ]; then
    sed -i "s|#deviceFilter:|deviceFilter: ${BLOCK/\/dev\//}\n    config:\n      osdsPerDevice: \"2\"|g" cluster-test.yaml
  elif [ "$1" = "osd_with_metadata_device" ]; then
    sed -i "s|#deviceFilter:|deviceFilter: ${BLOCK/\/dev\//}\n    config:\n      metadataDevice: /dev/test-rook-vg/test-rook-lv|g" cluster-test.yaml
  elif [ "$1" = "encryption" ]; then
    sed -i "s|#deviceFilter:|deviceFilter: ${BLOCK/\/dev\//}\n    config:\n      encryptedDevice: \"true\"|g" cluster-test.yaml
  elif [ "$1" = "lvm" ]; then
    sed -i "s|#deviceFilter:|devices:\n      - name: \"/dev/test-rook-vg/test-rook-lv\"|g" cluster-test.yaml
  elif [ "$1" = "loop" ]; then
    # add both /dev/sdX1 and loop device to test them at the same time
    sed -i "s|#deviceFilter:|devices:\n      - name: \"${BLOCK}\"\n      - name: \"/dev/loop1\"|g" cluster-test.yaml
  else
    echo "invalid argument: $*" >&2
    exit 1
  fi
  kubectl create -f cluster-test.yaml
  kubectl create -f object-test.yaml
  kubectl create -f pool-test.yaml
  kubectl create -f filesystem-test.yaml
  sed -i "/resources:/,/ # priorityClassName:/d" rbdmirror.yaml
  kubectl create -f rbdmirror.yaml
  sed -i "/resources:/,/ # priorityClassName:/d" filesystem-mirror.yaml
  kubectl create -f filesystem-mirror.yaml
  kubectl create -f nfs-test.yaml
  kubectl create -f subvolumegroup.yaml
  deploy_manifest_with_custom_image toolbox.yaml $img
}




FUNCTION="$1"
shift # remove function arg now that we've recorded it
# call the function with the remainder of the user-provided args
# -e, -E, and -o=pipefail will ensure this script returns a failure if a part of the function fails
$FUNCTION "$@"
