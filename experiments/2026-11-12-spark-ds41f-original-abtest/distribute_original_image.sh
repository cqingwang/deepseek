#!/usr/bin/env bash
set -euo pipefail

TAG=${TAG:-spark-ds41f:x1112-original}
OUT=${OUT:-/opt/abtest-results/2026-11-12-original/image-transfer}

mkdir -p "$OUT"

# Fan out to the two physical neighbours first, then relay the second hop.
(docker save "$TAG" | ssh -o BatchMode=yes -o ConnectTimeout=10 10.12.1.2 docker load) \
  >"$OUT/load-b.log" 2>&1 &
p_b=$!
(docker save "$TAG" | ssh -o BatchMode=yes -o ConnectTimeout=10 10.14.1.4 docker load) \
  >"$OUT/load-d.log" 2>&1 &
p_d=$!

wait "$p_b"
wait "$p_d"

ssh -o BatchMode=yes -o ConnectTimeout=10 10.12.1.2 \
  "docker save '$TAG' | ssh -o BatchMode=yes -o ConnectTimeout=10 10.23.1.3 docker load" \
  >"$OUT/load-c.log" 2>&1

{
  for host in 192.168.2.180 192.168.2.161 192.168.2.102 192.168.2.238; do
    echo "===== $host ====="
    ssh -o BatchMode=yes -o ConnectTimeout=10 "chan@$host" \
      "docker image inspect '$TAG' --format '{{.Id}} {{json .RootFS.Layers}}'"
  done
} | tee "$OUT/identity.txt"
