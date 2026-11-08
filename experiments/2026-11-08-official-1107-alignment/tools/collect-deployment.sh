#!/usr/bin/env bash
# Collect the actual four-rank runtime state into one immutable run directory.
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 RUN_ID OUTPUT_DIR" >&2
  exit 2
fi

RUN_ID="$1"
OUT="$2"
USER_NAME="${SPARK_USER:-chan}"
API_KEY_VALUE="${SPARK_API_KEY:-deepseek}"
IPS=(192.168.2.180 192.168.2.161 192.168.2.102 192.168.2.238)
SSH_OPTS=(-4 -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)

mkdir -p "$OUT"
printf '%s\n' "$RUN_ID" >"$OUT/run-id.txt"
date -u +%Y-%m-%dT%H:%M:%SZ >"$OUT/collected_utc.txt"

for rank in 0 1 2 3; do
  ip="${IPS[$rank]}"
  name="deepseek-r$rank"
  ssh "${SSH_OPTS[@]}" "${USER_NAME}@${ip}" \
    "docker inspect '$name'" >"$OUT/rank-${rank}-inspect.json"
  python3 - "$OUT/rank-${rank}-inspect.json" >"$OUT/rank-${rank}-summary.json" <<'PY'
import json
import sys

record = json.load(open(sys.argv[1], encoding="utf-8"))[0]
print(json.dumps({
    "name": record["Name"],
    "image": record["Config"]["Image"],
    "image_id": record["Image"],
    "state": record["State"],
    "env": record["Config"]["Env"],
    "cmd": record["Config"]["Cmd"],
    "mounts": record["Mounts"],
    "host_config": {
        "memory": record["HostConfig"].get("Memory"),
        "memory_swap": record["HostConfig"].get("MemorySwap"),
        "shm_size": record["HostConfig"].get("ShmSize"),
        "cpuset_cpus": record["HostConfig"].get("CpusetCpus"),
        "devices": record["HostConfig"].get("Devices"),
        "device_requests": record["HostConfig"].get("DeviceRequests"),
    },
}, indent=2, sort_keys=True))
PY
  image_name="$(python3 -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["image"])' \
    "$OUT/rank-${rank}-summary.json")"
  ssh "${SSH_OPTS[@]}" "${USER_NAME}@${ip}" "
    hostname
    date -u +%Y-%m-%dT%H:%M:%SZ
    docker image inspect '$image_name' --format '{{.Id}}'
    docker image inspect '$image_name' --format '{{json .Config.Labels}}'
    systemctl is-active '$name-memguard.service' || true
    systemctl --failed --no-legend --no-pager || true
    awk '/^(MemTotal|MemAvailable|SwapTotal|SwapFree):/{print}' /proc/meminfo
    nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total --format=csv,noheader 2>/dev/null || true
  " >"$OUT/rank-${rank}-host.txt"
done

curl -sS --max-time 15 \
  -H "Authorization: Bearer $API_KEY_VALUE" \
  "http://${IPS[0]}:8888/v1/models" \
  -o "$OUT/models.json" \
  -w 'http_code=%{http_code}\n' >"$OUT/models.code"

printf 'collected %s to %s\n' "$RUN_ID" "$OUT"
