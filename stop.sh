#!/usr/bin/env bash
# Stop the deepseek TP4 target containers and fail closed on residual state.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env.deepseek}"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8)
[ -f "$ENV_FILE" ] || { echo "ERROR: 缺少受管 env: $ENV_FILE" >&2; exit 2; }
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "[+] $*"; }

[ "${TARGET_ENGINE_ID:-}" = "deepseek" ] || die "TARGET_ENGINE_ID 必须为 deepseek"
TARGET_CONTAINER_PREFIX="${TARGET_CONTAINER_PREFIX:-$TARGET_ENGINE_ID}"
read -r -a WORKER_TARGETS <<< "${WORKER_SSH_TARGETS:?}"
read -r -a WORKER_RANK_VALUES <<< "${WORKER_RANKS:?}"
[ "${#WORKER_TARGETS[@]}" -eq 3 ] || die "必须提供三个 worker SSH 目标"
[ "${#WORKER_RANK_VALUES[@]}" -eq 3 ] || die "必须提供三个 worker rank"

remote_run() {
  local target="$1"
  local command="$2"
  if [ "$target" = "$HEAD_SSH_TARGET" ]; then
    bash -lc "$command"
  else
    ssh "${SSH_OPTS[@]}" "$target" "$command" </dev/null
  fi
}

remove_rank() {
  local target="$1" rank="$2"
  remote_run "$target" \
    "docker rm -f $(printf '%q' "$TARGET_CONTAINER_PREFIX-r$rank") >/dev/null 2>&1 || true"
}

assert_no_rank() {
  local target="$1" names
  names="$(remote_run "$target" \
    "docker ps -a --format '{{.Names}}' | awk -v p=$(printf '%q' "$TARGET_CONTAINER_PREFIX-r") 'index(\$0,p)==1 && substr(\$0,length(p)+1) ~ /^[0-9]+\$/ {print}'")"
  [ -z "$names" ] || die "$target 仍有 target 容器: $names"
}

info "stop $TARGET_ENGINE_ID ranks 0..3"
remove_rank "$HEAD_SSH_TARGET" 0
for index in "${!WORKER_TARGETS[@]}"; do
  remove_rank "${WORKER_TARGETS[$index]}" "${WORKER_RANK_VALUES[$index]}"
done

sleep 2
assert_no_rank "$HEAD_SSH_TARGET"
for target in "${WORKER_TARGETS[@]}"; do
  assert_no_rank "$target"
done

code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:${PORT}/v1/models" || true)"
[ "$code" = 000 ] || die "head :${PORT}/v1/models=${code}，停止后仍有服务应答"

pids="$(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | sed '/^[[:space:]]*$/d' || true)"
if [ -n "$pids" ]; then
  echo "WARN: head 仍有 GPU compute 进程: $(printf '%s ' $pids)" >&2
fi
info "deepseek stopped: target containers=0, head :${PORT}/v1/models=000"
