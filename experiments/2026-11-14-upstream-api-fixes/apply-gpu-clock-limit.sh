#!/usr/bin/env bash
set -euo pipefail

readonly max_clock_mhz="${1:-3003}"
readonly hosts=(
  chan@192.168.2.180
  chan@192.168.2.161
  chan@192.168.2.102
  chan@192.168.2.238
)

for host in "${hosts[@]}"; do
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$host" \
    "sudo -n nvidia-smi -lgc 0,${max_clock_mhz} >/dev/null; nvidia-smi --query-gpu=clocks.sm --format=csv,noheader"
done
