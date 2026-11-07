#!/usr/bin/env bash
# Managed vLLM (spark-ds41f) launcher for the four-node deepseek TP4 target.
#
# 适配口径：上游固定环境保存在 .env.deepseek.example；deploy.sh 经
# program.py query-managed-env 只写入宿主键和站点覆盖键。本文件以模板键集为
# 容器白名单，宿主键不得进入 `docker run -e`。
#
# 上游固定命令快照——DS41F_DOCKER_RUN_ARGS =
# cluster.json `container.docker_run_args`，DS41F_SERVE_ARGS =
# `serve_args` 经 scripts/recipe.py:resolve(..., "tp4-ring") 渲染后的定值
# （rows=96：max_num_seqs 16 x (1+drafts 5)；capture ladder 1..96；
# 2026-11-08 multi-size final: long_prefill 65424=65520-96；
# engram table_memory=disk）。
# 来源：config/cluster.json @ 2026-10-30-spark-ds41f-1030（镜像 spark-ds41f:1030）。
# 站点渲染点只有四处：serve 模型路径、--host、--port、--api-key；逐 rank
# 注入 VLLM_HOST_IP、NCCL_IB_HCA、SPARKNET_ROCE_PEER_HCAS、
# SPARKNET_ROCE_TOPOLOGY。其余固定键和 serve 参数不得丢失。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env.deepseek}"
FIXED_ENV_FILE="${FIXED_ENV_FILE:-$ROOT/.env.deepseek.example}"
ACTION="${1:-serve}"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8)
REMOTE_ROOT="${DS41F_REMOTE_ROOT:-/opt/spark/deepseek}"
STARTUP_MEMGUARD_GIB="${DS41F_STARTUP_MEMGUARD_GIB:-5}"
STARTUP_MEMGUARD_INTERVAL="${DS41F_STARTUP_MEMGUARD_INTERVAL_SECONDS:-0.25}"
STARTUP_MEMGUARD_WAIT="${DS41F_STARTUP_MEMGUARD_WAIT_SECONDS:-120}"
STEADY_MEMGUARD_GIB="${DS41F_MEMGUARD_GIB:-3}"
STEADY_MEMGUARD_INTERVAL="${DS41F_MEMGUARD_INTERVAL_SECONDS:-2}"
DS41F_GLP_HOME="${DS41F_GLP_HOME:-$HOME}"
DS41F_GLP_FILE="$DS41F_GLP_HOME/.cache/huggingface/hub/models--msuiche--DeepSeek-V4.1-Flash-1017-abliterated-cyber-GLP-39-L1-39-a0.75/snapshots/2715ffd51f59899115853e1118db080536966c44/glp.deepseek-v41-flash-1017-GLP-39-L1-39-a0.75.gguf"
DS41F_GLP_SHA256=9c66d47701e0ceb42c33906f2d3789c0f1f16b21e7d97185f20f7f3f09fbf5cf

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "[+] $*"; }
warn() { echo "[!] $*" >&2; }

[ -f "$ENV_FILE" ] || die "缺少受管环境文件: $ENV_FILE"
[ -f "$FIXED_ENV_FILE" ] || die "缺少上游固定环境模板: $FIXED_ENV_FILE"
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
CONFIG_FILE="${DS41F_CONFIG_FILE:-$ROOT/config/cluster.json}"

container_name_for_rank() { printf '%s-r%s' "$TARGET_CONTAINER_PREFIX" "$1"; }

require() {
  local key="$1"
  [ -n "${!key:-}" ] || die "缺少受管参数 $key"
}

for key in TARGET_ENGINE_ID TARGET_CONTAINER_PREFIX RUNTIME_ASSETS CACHE_DIR \
  MODEL_DIR MODEL_CONTAINER_PATH IMAGE IMAGE_ID HEAD_MANAGEMENT_IP \
  HEAD_SSH_TARGET WORKER_MANAGEMENT_IPS WORKER_SSH_TARGETS WORKER_RANKS \
  NNODES TP_SIZE NCCL_IB_SUBNET_PREFIX_LEN NCCL_IB_GID_INDEX NCCL_IB_HCA \
  SPARKNET_ROCE_PEER_HCAS_BY_RANK API_HOST PORT; do
  require "$key"
done

if [ "${TARGET_ENGINE_ID:-}" != "deepseek" ]; then
  die "TARGET_ENGINE_ID 必须为 deepseek: ${TARGET_ENGINE_ID:-<empty>}"
fi
[ "$RUNTIME_ASSETS" = "/opt/runtime_assets/deepseek" ] \
  || die "runtime assets 必须精确为 /opt/runtime_assets/deepseek"
[ "$NNODES" = "4" ] && [ "$TP_SIZE" = "4" ] \
  || die "deepseek 必须按 common.ring 使用 nnodes=4 的 TP4 环"
if [ "${DS41F_ALLOW_UNVERIFIED_IMAGE:-0}" = "1" ]; then
  case "$IMAGE" in
    spark-ds41f:*) ;;
    *) die "实验镜像 tag 不以 spark-ds41f: 开头: $IMAGE" ;;
  esac
  [[ "$IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || die "实验镜像 image ID 非法: $IMAGE_ID"
else
  case "$IMAGE" in
    spark-ds41f:1030) ;;
    *) die "镜像 tag 非本站已验收组合: $IMAGE" ;;
  esac
  case "$IMAGE_ID" in
    sha256:84a8b73d0993200044b5eceb0892342d34a787416b7ae91e4b38f42fffc63e41) ;;
    *) die "镜像 image ID 非本站已验收组合: $IMAGE_ID" ;;
  esac
fi

read -r -a WORKER_TARGETS <<< "$WORKER_SSH_TARGETS"
read -r -a WORKER_RANK_VALUES <<< "$WORKER_RANKS"
read -r -a WORKER_IP_VALUES <<< "$WORKER_MANAGEMENT_IPS"
[ "${#WORKER_TARGETS[@]}" -eq 3 ] || die "必须提供三个 worker SSH 目标"
[ "${#WORKER_RANK_VALUES[@]}" -eq 3 ] || die "必须提供三个 worker rank"
[ "${#WORKER_IP_VALUES[@]}" -eq 3 ] || die "必须提供三个 worker 管理 IP"

# 上游固定面：cluster.json `container.docker_run_args`，逐字（含带空格的
# --device-cgroup-rule 单元素，与上游 JSON 数组一一对应）。
DS41F_DOCKER_RUN_ARGS=(
  --network=host
  --ipc=host
  --gpus=all
  --device=/dev/infiniband:/dev/infiniband:rwm
  --cap-add=IPC_LOCK
  --ulimit=memlock=-1:-1
  --ulimit=stack=67108864:67108864
  --ulimit=core=0
  --security-opt=seccomp=unconfined
  --security-opt=label=disable
  --shm-size=16g
  --memory=112g
  --memory-swap=112g
  --mount=type=bind,source=/dev/dri/by-path/pci-000f:01:00.0-card,target=/dev/dri/card0
  '--device-cgroup-rule=c 226:* rw'
  --cpuset-cpus=5-9,15-19
)

# 上游固定面：cluster.json `serve_args` 的 tp4-ring 渲染定值（见文件头）。
# 首元素是受管渲染点 #1 的占位：运行时替换为 $MODEL_CONTAINER_PATH。
DS41F_SERVE_ARGS=(
  /models
  --served-model-name
  deepseek-v4.1-flash
  deepseek-v4.1-flash-weightless
  --host
  0.0.0.0
  --port
  8000
  --tokenizer-mode
  deepseek_v41
  --dtype
  bfloat16
  --tensor-parallel-size
  4
  --load-format
  direct
  --block-size
  256
  --enable-prefix-caching
  --enable-prompt-tokens-details
  --enable-chunked-prefill
  --max-model-len
  1048576
  --max-num-seqs
  16
  --max-parallel-prefills
  1
  --gpu-memory-utilization
  0.82
  --max-num-batched-tokens
  65520
  --kv-cache-memory-bytes
  11274289152
  --max-cudagraph-capture-size
  96
  --generation-config
  vllm
  --override-generation-config
  '{"temperature":1.0,"top_p":1.0}'
  --engram-config
  '{"table_memory":"disk","disk_resident_scales":false,"projection_tp":true}'
  --attention-backend
  TILELANG
  --linear-backend
  tilelang
  --moe-backend
  tilelang
  --speculative-config
  '{"method":"dspark","num_speculative_tokens":5,"draft_tensor_parallel_size":4,"attention_backend":"TILELANG","draft_sample_method":"greedy","rejection_sample_method":"block","enable_adaptive_verification":true,"adaptive_verification_row_cost":0.035}'
  --jit-monitor-mode
  off
  --compilation-config
  '{"cudagraph_mode":"FULL_AND_PIECEWISE","custom_ops":["all"],"cudagraph_capture_sizes":[1,2,3,4,6,8,12,16,20,24,28,32,40,48,56,64,72,80,88,96]}'
  --prefix-cache-retention-interval
  8192
  --long-prefill-token-threshold
  65424
  --async-scheduling
  --reasoning-parser
  deepseek_v41
  --tool-call-parser
  deepseek_v41
  --enable-auto-tool-choice
  --distributed-executor-backend
  mp
  --nnodes
  4
  --no-scheduler-reserve-full-isl
  --no-enable-flashinfer-autotune
  --limit-mm-per-prompt
  '{"image":67}'
  --mm-processor-cache-gb
  0
  --prefill-yield
  short
  --prefill-yield-limit-s
  10
)

# 上游固定面：cluster.json `distributed.master_port`（master_addr 由受管
# HEAD_MANAGEMENT_IP 承担，与上游 read_cluster 的 head 控制面推导一致）。
DS41F_MASTER_PORT=9999

# 受管 env 文件 → 容器 -e 集。只有上游模板键与声明的站点覆盖键可进入容器；
# 宿主键、重复键和多 token 的未引用值均 fail-closed。
container_env_rows() {
  local rank="$1"
  python3 - \
    "$ENV_FILE" "$FIXED_ENV_FILE" "$rank" \
    "$HEAD_MANAGEMENT_IP" "$WORKER_MANAGEMENT_IPS" \
    "$SPARKNET_ROCE_PEER_HCAS_BY_RANK" <<'PY'
import json
import shlex
import sys

CONTAINER_OVERRIDE_KEYS = {
    "GLOO_SOCKET_IFNAME",
    "TP_SOCKET_IFNAME",
    "NCCL_SOCKET_IFNAME",
    "NCCL_IB_SUBNET_PREFIX_LEN",
    "NCCL_IB_GID_INDEX",
    "NCCL_IB_HCA",
}


def assignments(path):
    values = {}
    for raw in open(path, encoding="utf-8"):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(("source ", ". ")):
            continue
        key, separator, raw_value = line.partition("=")
        if not separator or not key.replace("_", "").isalnum():
            continue
        tokens = shlex.split(raw_value) if raw_value else []
        if len(tokens) > 1:
            raise SystemExit(f"{path}: {key} 含未引用空白")
        if key in values:
            raise SystemExit(f"{path}: 重复环境键 {key}")
        values[key] = tokens[0] if tokens else ""
    return values


fixed = assignments(sys.argv[2])
merged = assignments(sys.argv[1])
missing = sorted(set(fixed) - set(merged))
if missing:
    raise SystemExit(f"合并 env 丢失固定键: {', '.join(missing)}")
missing_overrides = sorted(CONTAINER_OVERRIDE_KEYS - set(merged))
if missing_overrides:
    raise SystemExit(f"合并 env 丢失站点覆盖键: {', '.join(missing_overrides)}")

env = {key: merged[key] for key in set(fixed) | CONTAINER_OVERRIDE_KEYS}
rank = sys.argv[3]
if rank == "0":
    env["VLLM_HOST_IP"] = sys.argv[4]
else:
    worker_ips = sys.argv[5].split()
    if len(worker_ips) != 3 or rank not in {"1", "2", "3"}:
        raise SystemExit(f"rank {rank}: worker 管理地址不完整")
    env["VLLM_HOST_IP"] = worker_ips[int(rank) - 1]

peer_maps = json.loads(sys.argv[6])
if rank not in peer_maps:
    raise SystemExit(f"rank {rank}: 缺少 sparknet peer-HCA 映射")
env["SPARKNET_ROCE_PEER_HCAS"] = json.dumps(
    peer_maps[rank], separators=(",", ":"), sort_keys=True
)
env["SPARKNET_ROCE_TOPOLOGY"] = "ring4"

for key in sorted(env):
    if not key or not key.replace("_", "").isalnum():
        raise SystemExit(f"非法容器环境键: {key!r}")
    if "\n" in env[key] or "\t" in env[key]:
        raise SystemExit(f"{key} 含非法换行或制表符")
    print(f"{key}\t{env[key]}")
PY
}

# 单个 rank 的完整 docker run argv（与上游 rendered_docker_command 同序：
# name + docker_run_args + 排序后的 -e 集 + 卷 + 镜像 + serve + 分布式参数）。
docker_command_for_rank() {
  local rank="$1"

  local -a command=(docker run -d --name "$(container_name_for_rank "$rank")")
  command+=("${DS41F_DOCKER_RUN_ARGS[@]}")

  local rows key value
  rows="$(container_env_rows "$rank")" || die "rank $rank 容器环境生成失败"
  while IFS=$'\t' read -r key value; do
    [ -n "$key" ] || continue
    command+=("--env=$key=$value")
  done <<< "$rows"

  command+=(-v "$MODEL_DIR:$MODEL_CONTAINER_PATH:ro")
  command+=(-v "$CACHE_DIR:/cache:rw")
  command+=(-v "$DS41F_GLP_FILE:/glp/vector.gguf:ro")
  command+=("$IMAGE")

  # 站点渲染点：模型路径、--host、--port、--api-key。
  local -a serve_args=("${DS41F_SERVE_ARGS[@]}")
  serve_args[0]="$MODEL_CONTAINER_PATH"
  local index
  for index in "${!serve_args[@]}"; do
    case "${serve_args[$index]}" in
      --host) serve_args[$(( index + 1 ))]="$API_HOST" ;;
      --port) serve_args[$(( index + 1 ))]="$PORT" ;;
    esac
  done
  if [ -n "${API_KEY:-}" ]; then
    serve_args+=(--api-key "$API_KEY")
  fi
  command+=("${serve_args[@]}")
  command+=(--node-rank "$rank" --master-addr "$HEAD_MANAGEMENT_IP" --master-port "$DS41F_MASTER_PORT")
  [ "$rank" = "0" ] || command+=(--headless)
  printf '%q ' "${command[@]}"
}

rank_ssh_target() {
  local rank="$1" index
  if [ "$rank" = "0" ]; then
    printf '%s' "$HEAD_SSH_TARGET"
    return 0
  fi
  for index in "${!WORKER_RANK_VALUES[@]}"; do
    if [ "${WORKER_RANK_VALUES[$index]}" = "$rank" ]; then
      printf '%s' "${WORKER_TARGETS[$index]}"
      return 0
    fi
  done
  die "未知 rank: $rank"
}

rank_management_ip() {
  local rank="$1" index
  if [ "$rank" = "0" ]; then
    printf '%s' "$HEAD_MANAGEMENT_IP"
    return 0
  fi
  for index in "${!WORKER_RANK_VALUES[@]}"; do
    if [ "${WORKER_RANK_VALUES[$index]}" = "$rank" ]; then
      printf '%s' "${WORKER_IP_VALUES[$index]}"
      return 0
    fi
  done
  die "未知 rank: $rank"
}

run_rank_docker() {
  local rank="$1" target command
  command="$(docker_command_for_rank "$rank")"
  target="$(rank_ssh_target "$rank")"
  if [ "$target" = "$HEAD_SSH_TARGET" ]; then
    bash -c "$command" >/dev/null
  else
    ssh "${SSH_OPTS[@]}" "$target" "$command" </dev/null >/dev/null
  fi
}

run_rank_shell() {
  local rank="$1" command="$2" target
  target="$(rank_ssh_target "$rank")"
  if [ "$target" = "$HEAD_SSH_TARGET" ]; then
    bash -c "$command"
  else
    ssh "${SSH_OPTS[@]}" "$target" "bash -s" <<< "$command"
  fi
}

memguard_unit_for_rank() {
  printf '%s-memguard.service' "$(container_name_for_rank "$1")"
}

stop_memguard_for_rank() {
  local rank="$1" unit
  unit="$(memguard_unit_for_rank "$rank")"
  run_rank_shell "$rank" \
    "sudo -n systemctl stop $(printf '%q' "$unit") >/dev/null 2>&1 || true; sudo -n systemctl reset-failed $(printf '%q' "$unit") >/dev/null 2>&1 || true" \
    >/dev/null 2>&1 || true
}

memguard_is_active_for_rank() {
  local rank="$1" unit
  unit="$(memguard_unit_for_rank "$rank")"
  run_rank_shell "$rank" "systemctl is-active --quiet $(printf '%q' "$unit")" \
    >/dev/null 2>&1
}

arm_memguard_for_rank() {
  local rank="$1" phase="$2" mode="$3" threshold interval wait
  if [ "$phase" = startup ]; then
    threshold="$STARTUP_MEMGUARD_GIB"
    interval="$STARTUP_MEMGUARD_INTERVAL"
    wait="$STARTUP_MEMGUARD_WAIT"
  elif [ "$phase" = steady ]; then
    threshold="$STEADY_MEMGUARD_GIB"
    interval="$STEADY_MEMGUARD_INTERVAL"
    wait=0
  else
    die "未知 memguard phase: $phase"
  fi
  run_rank_shell "$rank" \
    "cd $(printf '%q' "$REMOTE_ROOT") && scripts/memguard-start.sh $(printf '%q' "$(container_name_for_rank "$rank")") $(printf '%q' "$phase") $(printf '%q' "$threshold") $(printf '%q' "$interval") $(printf '%q' "$wait") $(printf '%q' "$mode") 0"
}

container_state_for_rank() {
  run_rank_shell "$1" \
    "docker inspect --format '{{.State.Running}} {{.State.ExitCode}} {{.State.OOMKilled}}' $(printf '%q' "$(container_name_for_rank "$1")")" \
    2>/dev/null || true
}

preserve_failed_start_logs() {
  local stamp dir rank state logs
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  dir="$ROOT/results/private/failed-starts/$stamp-deepseek"
  mkdir -p "$dir"
  for rank in 0 1 2 3; do
    state="$(run_rank_shell "$rank" \
      "docker inspect $(printf '%q' "$(container_name_for_rank "$rank")")" 2>&1 || true)"
    logs="$(run_rank_shell "$rank" \
      "docker logs --timestamps --tail 4000 $(printf '%q' "$(container_name_for_rank "$rank")") 2>&1" 2>&1 || true)"
    printf '%s\n' "$state" >"$dir/rank-${rank}-inspect.txt"
    printf '%s\n' "$logs" >"$dir/rank-${rank}.log"
  done
  warn "失败启动日志已保存: $dir"
}

cleanup_failed_launch() {
  local rc=$? rank
  trap - EXIT
  if [ "$rc" -ne 0 ]; then
    preserve_failed_start_logs
    for rank in 0 1 2 3; do
      stop_memguard_for_rank "$rank"
      run_rank_shell "$rank" \
        "docker rm -f $(printf '%q' "$(container_name_for_rank "$rank")") >/dev/null 2>&1 || true" \
        >/dev/null 2>&1 || true
    done
    warn "已回滚本次 deepseek 启动并停止四机 memguard"
  fi
  exit "$rc"
}

wait_for_api() {
  local deadline=$(( $(date +%s) + ${VLLM_ENGINE_READY_TIMEOUT_S:-3600} ))
  local -a auth_args=()
  local rank state logs
  if [ -n "${API_KEY:-}" ]; then
    auth_args=(-H "Authorization: Bearer $API_KEY")
  fi
  info "等待 head API 就绪: http://127.0.0.1:${PORT}/v1/models（上限 ${VLLM_ENGINE_READY_TIMEOUT_S:-3600}s）"
  while [ "$(date +%s)" -lt "$deadline" ]; do
    for rank in 0 1 2 3; do
      state="$(container_state_for_rank "$rank")"
      case "$state" in
        true\ *) ;;
        *)
          logs="$(run_rank_shell "$rank" \
            "docker logs --timestamps --tail 80 $(printf '%q' "$(container_name_for_rank "$rank")") 2>&1" 2>&1 || true)"
          die "rank $rank 在 API readiness 前停止: ${state:-inspect-failed}; logs: ${logs: -2000}"
          ;;
      esac
      memguard_is_active_for_rank "$rank" \
        || die "rank $rank startup memguard 在 API readiness 前停止"
    done
    if [ "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "${auth_args[@]}" \
      "http://127.0.0.1:${PORT}/v1/models" 2>/dev/null || true)" = 200 ]; then
      info "deepseek stack UP: http://${API_HOST}:${PORT}/v1/models"
      return 0
    fi
    sleep 10
  done
  die "head API 在 ${VLLM_ENGINE_READY_TIMEOUT_S:-3600}s 内未就绪: http://127.0.0.1:${PORT}/v1/models"
}

preflight() {
  local rank target image_id names actual key expected label_rows
  for rank in 0 1 2 3; do
    target="$(rank_ssh_target "$rank")"
    if [ "$target" = "$HEAD_SSH_TARGET" ]; then
      [ -d "$MODEL_DIR" ] || die "head 缺少模型目录: $MODEL_DIR"
      [ -f "$MODEL_DIR/model.safetensors.index.json" ] \
        || die "head 模型目录缺少 model.safetensors.index.json: $MODEL_DIR"
      [ -f "$DS41F_GLP_FILE" ] || die "head 缺少 GLP steering 文件: $DS41F_GLP_FILE"
      mkdir -p "$CACHE_DIR"
      printf '%s  %s\n' "$DS41F_GLP_SHA256" "$DS41F_GLP_FILE" | sha256sum -c - >/dev/null \
        || die "head GLP steering 文件摘要不一致: $DS41F_GLP_FILE"
    else
      ssh "${SSH_OPTS[@]}" "$target" "test -d $(printf '%q' "$MODEL_DIR")" </dev/null \
        || die "$target 缺少模型目录: $MODEL_DIR"
      ssh "${SSH_OPTS[@]}" "$target" \
        "test -f $(printf '%q' "$MODEL_DIR/model.safetensors.index.json")" </dev/null \
        || die "$target 模型目录缺少 model.safetensors.index.json: $MODEL_DIR"
      ssh "${SSH_OPTS[@]}" "$target" "test -f $(printf '%q' "$DS41F_GLP_FILE")" </dev/null \
        || die "$target 缺少 GLP steering 文件: $DS41F_GLP_FILE"
      ssh "${SSH_OPTS[@]}" "$target" "mkdir -p $(printf '%q' "$CACHE_DIR")" </dev/null \
        || die "$target 创建 cache 目录失败: $CACHE_DIR"
      ssh "${SSH_OPTS[@]}" "$target" \
        "printf '%s  %s\\n' $(printf '%q' "$DS41F_GLP_SHA256") $(printf '%q' "$DS41F_GLP_FILE") | sha256sum -c - >/dev/null" \
        </dev/null || die "$target GLP steering 文件摘要不一致: $DS41F_GLP_FILE"
    fi
    names="$(run_rank_shell "$rank" \
      "docker ps --format '{{.Names}}' | grep -E '^(deepseek|deepseek_flash|deepseek_v4|spark-ds41f)' || true" 2>/dev/null)"
    [ -z "$names" ] \
      || die "rank $rank 仍有冲突 LLM 容器，停止后再启动: $(printf '%s' "$names" | paste -sd, -)"
  done
  if run_rank_shell 0 "ss -H -ltn 'sport = :$PORT'" | grep -q .; then
    die "head :$PORT 已被占用，拒绝启动 deepseek"
  fi
  info "镜像身份校验: $IMAGE ($IMAGE_ID)"
  label_rows="$(python3 - "$CONFIG_FILE" <<'PY'
import json
import sys

labels = json.load(open(sys.argv[1], encoding="utf-8"))["container"]["expected_labels"]
for key, value in sorted(labels.items()):
    print(f"{key}\t{value}")
PY
  )" || die "无法从 config/cluster.json 读取 expected_labels"
  for rank in 0 1 2 3; do
    target="$(rank_ssh_target "$rank")"
    image_id="$(remote_docker_inspect_id "$target")"
    [ "$image_id" = "$IMAGE_ID" ] || die "rank $rank（$target）镜像身份不一致: $image_id"
    while IFS=$'\t' read -r key expected; do
      [ -n "$key" ] || continue
      actual="$(run_rank_shell "$rank" \
        "docker image inspect $(printf '%q' "$IMAGE") --format $(printf '%q' "{{index .Config.Labels \"$key\"}}")" 2>/dev/null || true)"
      [ "$actual" = "$expected" ] \
        || die "rank $rank（$target）镜像 label $key=$actual，期望 $expected"
    done <<< "$label_rows"
  done
}

remote_docker_inspect_id() {
  local target="$1"
  if [ "$target" = "$HEAD_SSH_TARGET" ]; then
    docker image inspect "$IMAGE" --format '{{.Id}}'
  else
    ssh "${SSH_OPTS[@]}" "$target" \
      "docker image inspect $(printf '%q' "$IMAGE") --format '{{.Id}}'" </dev/null
  fi
}

launch_stack() {
  preflight
  local rank target
  trap cleanup_failed_launch EXIT
  for rank in 1 2 3; do
    stop_memguard_for_rank "$rank"
    info "预置 rank $rank startup memguard（${STARTUP_MEMGUARD_GIB} GiB / ${STARTUP_MEMGUARD_INTERVAL}s）"
    arm_memguard_for_rank "$rank" startup prelaunch
    memguard_is_active_for_rank "$rank" \
      || die "rank $rank startup memguard 未激活，拒绝启动"
    target="$(rank_ssh_target "$rank")"
    info "启动 rank $rank（$target）: $(container_name_for_rank "$rank")"
    run_rank_docker "$rank"
  done
  rank=0
  stop_memguard_for_rank "$rank"
  info "预置 head rank 0 startup memguard（${STARTUP_MEMGUARD_GIB} GiB / ${STARTUP_MEMGUARD_INTERVAL}s）"
  arm_memguard_for_rank "$rank" startup prelaunch
  memguard_is_active_for_rank "$rank" \
    || die "head startup memguard 未激活，拒绝启动"
  target="$(rank_ssh_target "$rank")"
  info "启动 head rank 0（$target）: $(container_name_for_rank "$rank")"
  run_rank_docker "$rank"
  wait_for_api
  for rank in 0 1 2 3; do
    stop_memguard_for_rank "$rank"
    arm_memguard_for_rank "$rank" steady running >/dev/null
    memguard_is_active_for_rank "$rank" \
      || die "rank $rank steady memguard 未激活"
  done
  info "四机 steady memguard 已激活（${STEADY_MEMGUARD_GIB} GiB / ${STEADY_MEMGUARD_INTERVAL}s）"
  trap - EXIT
}

print_stack() {
  local rank
  for rank in 0 1 2 3; do
    echo "== rank $rank =="
    docker_command_for_rank "$rank"
    printf '\n'
  done
}

case "$ACTION" in
  serve) launch_stack ;;
  print-env) print_stack ;;
  *) die "未知动作: $ACTION（支持 serve/print-env）" ;;
esac
