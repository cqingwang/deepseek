#!/usr/bin/env bash
# Measure one already-running deployment with the official servebench client.
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 RUN_ID {prefill|quick|official}" >&2
  exit 2
fi

RUN_ID="$1"
MODE="$2"
USER_NAME="${SPARK_USER:-chan}"
HEAD_IP="${SPARK_HEAD_IP:-192.168.2.180}"
API_KEY_VALUE="${SPARK_API_KEY:-deepseek}"
REMOTE_ROOT="${SPARK_REMOTE_ROOT:-/opt/spark}"
EXP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_RUN="/opt/runtime_assets/deepseek/results/official-1107-alignment/$RUN_ID"
LOCAL_RUN="$EXP_ROOT/runs/$RUN_ID"
WRAPPER="$REMOTE_ROOT/deepseek/experiments/2026-11-08-official-1107-alignment/tools/servebench_auth.py"
SSH_OPTS=(-4 -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=20)

case "$MODE" in
  prefill|quick|official) ;;
  *)
    echo "unknown mode: $MODE" >&2
    exit 2
    ;;
esac

mkdir -p "$LOCAL_RUN"
ssh "${SSH_OPTS[@]}" "${USER_NAME}@${HEAD_IP}" "mkdir -p '$REMOTE_RUN'"

remote_bench() {
  local command="$1"
  local label="$2"
  local output="$3"
  shift 3
  ssh "${SSH_OPTS[@]}" "${USER_NAME}@${HEAD_IP}" \
    "bash -lc $(printf '%q' "
      set -euo pipefail
      cd '$REMOTE_ROOT/official-1107'
      export SERVEBENCH_ROOT='$REMOTE_ROOT/official-1107' SPARK_API_KEY='$API_KEY_VALUE'
      python3 '$WRAPPER' $command http://127.0.0.1:8888 $* \
        --label '$RUN_ID-$label' --output '$REMOTE_RUN/$output'
    ")"
}

decode_extra='{"chat_template_kwargs":{"thinking":false}}'

if [ "$MODE" = prefill ] || [ "$MODE" = quick ] || [ "$MODE" = official ]; then
  remote_bench prefill prefill-six prefill-six.json \
    "--sizes 8192,16384,32768,65536,131072,262144 --repeats 2"
fi

if [ "$MODE" = quick ]; then
  for concurrency in 1 2 3 4 8 16; do
    remote_bench run "c${concurrency}-quick" "c${concurrency}.json" \
      "--mode closed --concurrency $concurrency --warmup 10 --duration 30 --batches 3 --max-tokens 256 --extra-body $(printf '%q' "$decode_extra")"
  done
fi

if [ "$MODE" = official ]; then
  for concurrency in 16 8 1; do
    remote_bench run "c${concurrency}-official" "c${concurrency}.json" \
      "--mode closed --concurrency $concurrency --warmup 60 --duration 300 --batches 10 --max-tokens 256 --extra-body $(printf '%q' "$decode_extra")"
  done
fi

rsync -a \
  "${USER_NAME}@${HEAD_IP}:$REMOTE_RUN/*.json" \
  "$LOCAL_RUN/"

printf 'measurement complete: mode=%s run=%s local=%s\n' "$MODE" "$RUN_ID" "$LOCAL_RUN"
