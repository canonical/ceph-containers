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


function deploy_operator_with_custom_image() {
  local yaml=${1:?missing}
  local img=${2:?missing}
  sed -i 's/.*ROOK_CSI_ENABLE_NFS:.*/  ROOK_CSI_ENABLE_NFS: \"true\"/g' $yaml
  sed -i "s|image: rook/ceph:.*|image: $img|g" $yaml
  if [[ "$ALLOW_LOOP_DEVICES" = "true" ]]; then
    sed -i "s|ROOK_CEPH_ALLOW_LOOP_DEVICES: \"false\"|ROOK_CEPH_ALLOW_LOOP_DEVICES: \"true\"|g" $yaml
  fi
  sed -i "s|ROOK_LOG_LEVEL:.*|ROOK_LOG_LEVEL: DEBUG|g" "$yaml"
  kubectl create -f $yaml
}

function deploy_cluster_with_custom_image() {
  local yaml=${1:?missing}
  local img=${2:?missing}
  sed -i "s|#deviceFilter:|deviceFilter: ${BLOCK/\/dev\//}|g" $yaml
  sed -i "s|image: quay.io/ceph/ceph:v17.*|image: $img|g" $yaml
  kubectl create -f $yaml
}

function deploy_cluster() {
  local operator_default="rook/ceph:master"
  local operator_img=${1:-"$operator_default"}
  local cluster_default="$( cat custom-image-spec )"
  local cluster_img=${2:-"$cluster_default"}

  cd rook/deploy/examples
  deploy_operator_with_custom_image operator.yaml $operator_img
  deploy_cluster_with_custom_image cluster-test.yaml $cluster_img
  kubectl create -f object-test.yaml
  kubectl create -f pool-test.yaml
  kubectl create -f filesystem-test.yaml
  sed -i "/resources:/,/ # priorityClassName:/d" rbdmirror.yaml
  kubectl create -f rbdmirror.yaml
  sed -i "/resources:/,/ # priorityClassName:/d" filesystem-mirror.yaml
  kubectl create -f filesystem-mirror.yaml
  kubectl create -f nfs-test.yaml
  kubectl create -f subvolumegroup.yaml
  deploy_operator_with_custom_image toolbox.yaml $img $operator_img
}



FUNCTION="$1"
shift # remove function arg now that we've recorded it
# call the function with the remainder of the user-provided args
# -e, -E, and -o=pipefail will ensure this script returns a failure if a part of the function fails
$FUNCTION "$@"
