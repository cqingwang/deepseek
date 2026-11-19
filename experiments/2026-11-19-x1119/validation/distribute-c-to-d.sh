#!/usr/bin/env bash
set -euo pipefail

touch /tmp/x1119-fabric-known-hosts
docker save spark-ds41f:x1119 |
  ssh -o BatchMode=yes -o ConnectTimeout=10 \
    -o UserKnownHostsFile=/tmp/x1119-fabric-known-hosts \
    -o StrictHostKeyChecking=accept-new \
    chan@10.251.20.10 docker load
