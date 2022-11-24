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

cd rook
kubectl -n rook-ceph delete deploy/rook-ceph-operator
kubectl -n rook-ceph delete deploy/rook-ceph-osd-1 --grace-period=0 --force
sed -i 's/<OSD-IDs>/1/' deploy/examples/osd-purge.yaml
# the CI must force the deletion since we use replica 1 on 2 OSDs
sed -i 's/false/true/' deploy/examples/osd-purge.yaml
sed -i "s|rook/ceph:.*|rook/ceph:master|" deploy/examples/osd-purge.yaml
kubectl -n rook-ceph create -f deploy/examples/osd-purge.yaml
toolbox=$(kubectl get pod -l app=rook-ceph-tools -n rook-ceph -o jsonpath='{.items[*].metadata.name}')
kubectl -n rook-ceph exec $toolbox -- ceph status
# wait until osd.1 is removed from the osd tree
timeout 120 sh -c "while kubectl -n rook-ceph exec $toolbox -- ceph osd tree|grep -qE 'osd.1'; do echo 'waiting for ceph osd 1 to be purged'; sleep 1; done"
kubectl -n rook-ceph exec $toolbox -- ceph status
kubectl -n rook-ceph exec $toolbox -- ceph osd tree
