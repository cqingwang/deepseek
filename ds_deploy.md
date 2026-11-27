# spark/deepseek x1128 部署与验收指导

> 日期：2026-11-28
> 状态：当前生产仍是 `spark-ds41f:x1126`；`x1128` 目标镜像尚未构建、尚未分发、
> 尚未部署。`x1128` 来源为官方 `main@5386c71b` / candidate-77；官方生产 tag
> 仍是 `spark-ds41f-1126@890f8654`。
> 本文是 x1128 部署方案。`stop`、`start`、`build image --apply`、
> `build distribute --apply`、`sync_to.sh` 等动作仍需逐项确认后执行。

## 1. 当前结论

### 1.1 当前生产

- 父仓当前提交：`8709b73c Update DeepSeek 1126 deployment guide pointer`
- 子模块当前提交：`8bdbe54a Update 1126 deployment guide to current state`
- 当前生产镜像：`spark-ds41f:x1126`
- 官方别名：`spark-ds41f:1126`
- 当前 image ID：
  `sha256:b3fe75f0a14aa8523149b0675dd3ec309efd339cc5e786a7dc71c6154c1bd20a`
- 四机 `deepseek-r0..3` 均运行 `spark-ds41f:x1126`
- 当前 `OOMKilled=false`、`RestartCount=0`

当前 x1126 镜像锁：

```text
vLLM:        candidate-74 / 742 patches
patch head:  d8784b841c360f523ec20e44bf8fb0898f36d3e4
vLLM tree:   f3783b0ec61851ba481f6a3a316d0a76d0e171d8
sparknet:    v0.6.0 / 1739407706277142da7a12c95fca3c503c8cb11c
TileLang:    0.1.15 / 741806c7357102130a149cdcae2a5f22678eab67
TileKernels: 2.0.0 / cb2fa41c8af290204c81c547ed678ebf957cca53
NCCL:        2.30.7 / 6af10aa730a64fcee8781d62340a9c9184e79469
```

### 1.2 x1128 目标

- 目标 tag：`spark-ds41f:x1128`
- 本地别名：`spark-ds41f:1128` 可选，仅作本地别名，不是官方 release tag
- 来源：官方 `main@5386c71b` / candidate-77
- 站点 profile：
  `experiments/2026-11-28-x1128/tp4-ring.json`
- 当前四机没有 `x1128` 镜像
- 当前父级 `spark/config.yaml` 仍指向 x1126

x1128 目标锁：

```text
vLLM:        candidate-77 / 744 patches
patch head:  e0b964f8e6463e4a64ca91a2eaba5f0649739f12
vLLM tree:   58f9b11080cf8ebeccd80c0ce1d9bb7a6ff4ae58
sparknet:    v0.6.0 / 1739407706277142da7a12c95fca3c503c8cb11c
TileLang:    0.1.16 / 16c9ce75cead3b893572b9432f2c39b662391de8
TileLang patch head: 2ed9f9cf5f90be8513aa9350310d103a51f74a8a
TileKernels: 2.0.0 / cb2fa41c8af290204c81c547ed678ebf957cca53
NCCL:        2.30.7 / 6af10aa730a64fcee8781d62340a9c9184e79469
```

### 1.3 当前验收状态

- x1126 decode：两轮均值全部超过官方 1126 的 1% 下限，PASS。
- x1126 prefill：source-text 两轮均值比官方 1126 低 `3.7%–4.8%`，HOLD。
- x1128：尚未构建、尚未运行、尚未验收。
- candidate-77 官方 README 仍把完整 `174` 文件 release tests 和 clean-reboot
  `tp3-triangle` screen 列为 pending；x1128 必须补跑。

## 2. 官方最新代码与生产边界

官方最新 `main` 已包含：

- `candidate-75`：DFly2 standalone drafter，未 promotion；GB10 上明显慢于 DSpark。
- `candidate-76`：DSpark truncated-depth CUDA graph replay + 2 MiB hugepage defrag。
- `candidate-77`：TileLang `0.1.16` rebase，x1128 的来源。

这些 candidate 已进入本地源码，但官方 `config/cluster.json` 仍是 1126 生产配置。
x1128 是站点 candidate 部署，不是官方 production promotion。

当前站点 launcher 已对齐官方新能力：

- 当前 x1126 没有 `drafter`，继续使用固定 1126 argv。
- profile 选择官方 `drafter` / `drafters` 时，launcher 使用 `recipe.resolve`
  生成的 `serve_args`，并补挂 `/hub` 或 `/drafter-*`。
- 启动前默认调用官方 `spark_ds41f.cluster.defragment_buddy_memory`。
- 如需临时关闭 defrag：

```bash
export DS41F_DEFRAGMENT_BEFORE_START=0
```

## 3. 配置口径

### 3.1 当前生产配置

当前 `spark/config.yaml` 仍是：

```yaml
image: spark-ds41f:x1126
image_version_label: x1126
image_id: sha256:b3fe75f0a14aa8523149b0675dd3ec309efd339cc5e786a7dc71c6154c1bd20a
ds41f_config_file: experiments/2026-11-26-x1126/tp4-ring.json
expected_image:
  source: upstreams.lock.json
  vllm_tree: f3783b0ec61851ba481f6a3a316d0a76d0e171d8
```

在 x1128 构建完成并拿到实际 image ID 前，不要改成 x1128。

### 3.2 x1128 构建完成后的父级配置

构建完成后改为：

```yaml
image: spark-ds41f:x1128
image_version_label: x1128
image_id: sha256:<X1128_IMAGE_ID>
allow_unverified_image: true
ds41f_config_file: experiments/2026-11-28-x1128/tp4-ring.json
expected_image:
  source: experiments/2026-10-26-spark-ds41f-candidate/candidate-77/upstreams.lock.json
  vllm_tree: 58f9b11080cf8ebeccd80c0ce1d9bb7a6ff4ae58
skip_build: true
nnodes: 4
tensor_parallel_size: 4
num_speculative_tokens: 5
max_images_per_request: 67
```

`image_id` 必须来自本地实际构建结果，不能直接抄官方 candidate-77 的 digest。

### 3.3 站点 x1128 profile

`experiments/2026-11-28-x1128/tp4-ring.json` 只覆盖：

```json
{
  "container": {
    "name_pattern": "deepseek-r{rank}",
    "image": "spark-ds41f:x1128"
  },
  "nodes_config": "config/nodes-ring4.local.json",
  "host": {
    "fan_control": false
  }
}
```

它继承官方 candidate-77 的 vLLM、TileLang、sparknet、GLP 和 serve 参数。

### 3.4 业务参数

保持：

```yaml
max_request: 8
max_token: 1000000
max_tokens: 65536
max_num_batched_tokens: 16384
default_thinking: false
reasoning_effort: 50
master_port: 25000
api_url: http://0.0.0.0:8888/v1/models
api_key: deepseek
```

不要新增 `engines.deepseek.max_num_seqs`。DeepSeek 并发唯一来自
`common.max_request`。

### 3.5 GLP

```text
repo:     msuiche/DeepSeek-V4.1-Flash-1017-abliterated-cyber-GLP-27-L11-37-a3.5
revision: 4d570926e9dd4fa7247d126072c34e249c89abd7
file:     glp.deepseek-v41-flash-1017-GLP-27-L11-37-a3.5.gguf
sha256:   24e97d41a9a4f77adad78ff4cb888cb4e339eb62151dbf37a7fcd3ea30eb442e
alpha:    2.0
```

四机路径：

```text
/home/chan/.cache/huggingface/hub/models--msuiche--DeepSeek-V4.1-Flash-1017-abliterated-cyber-GLP-27-L11-37-a3.5/snapshots/4d570926e9dd4fa7247d126072c34e249c89abd7/glp.deepseek-v41-flash-1017-GLP-27-L11-37-a3.5.gguf
```

## 4. 8 并发固定 KV

当前 DS4.1 几何：

- `--block-size=256`
- pool block：`227,840` bytes
- window groups：`15`
- ring groups：`1`
- drafts：`5`
- 每请求 decode rows：`1 + 5 = 6`

计算：

```text
resident_blocks_per_full_request = 3,953
prompt_blocks_in_flight          = 3,825
null_block                       = 1

8 * 3,953 + 3,825 + 1 = 35,450 blocks
35,450 * 227,840 = 8,076,928,000 bytes
```

即：

- 固定 KV：`8,076,928,000` bytes
- 约 `7.522 GiB`
- 上游 16.5 GiB 池：`17,716,740,096` bytes
- 相对上游释放：`9,639,812,096` bytes，约 `8.978 GiB`

不要手工把 `config/cluster.json` 的
`shapes.tp4-ring.sizing.kv_cache_memory_bytes` 改成 `8076928000`。该字段是上游
16.5 GiB 硬上限；固定池由 `start-tp4.sh` 调用
`kv_capacity.required_kv_cache_bytes` 计算。

## 5. 改动类型与是否重建

| 改动类型 | 示例 | 是否重建镜像 |
|---|---|---|
| 二进制/源码 | vLLM patch series、TileLang、sparknet、NCCL、CUDA、Dockerfile、`requirements/`、`spark_ds41f/build.py` | 是 |
| 运行参数 | `max_request`、`max_token`、`max_tokens`、`max_num_batched_tokens`、`reasoning_effort`、端口、节点图、环网参数 | 否 |
| GLP 向量 | GLP-27 文件、alpha、generation defaults | 否 |
| launcher/环境模板 | `start-tp4.sh`、`.env.deepseek.example`、`program.py` 映射 | 否 |
| 父级 image tag/ID | `spark/config.yaml` 的 `image`/`image_id` | 否 |
| x1128 切换 | candidate-77 vLLM/TileLang 输入 | 是 |

## 6. 部署前检查

### 6.1 本地代码与测试

```bash
cd /Users/chan/Documents/project/doc/spark/deepseek

python3.14 -m unittest \
  tests.test_source_locks \
  tests.test_glp_controls \
  tests.test_pp \
  tests.test_running_images \
  tests.test_sonar_client \
  tests.test_kv_capacity \
  tests.test_dfly2_recipe \
  tests.test_restart_guards

python3.14 scripts/render_docs.py --check
python3.14 bin/spark doctor
git diff --check
```

父级合同：

```bash
cd /Users/chan/Documents/project/doc
python3.14 -m pytest -q \
  spark/tests/test_deepseek_env_merge_contract.py \
  spark/tests/test_deepseek_x1123_acceptance.py \
  spark/tests/test_deepseek_official1122_acceptance.py \
  spark/tests/test_active_target_cleanup.py
```

当前结果：

```text
子模块定向：120 passed
父级相关：32 passed
render_docs --check：通过
bin/spark doctor：configuration OK
```

子模块全量 `967` 项测试中仍有 `13` 个已知环境/历史 profile 失败：

- macOS 无 `os.sched_getaffinity`
- 本机代理注入导致 build argv 与历史记录不同
- candidate-15..18 历史 `master_addr`
- 历史 kernel profile source lock
- lab 历史 `master_addr`

这些不是 x1128 合并引入的回归。

### 6.2 x1128 profile 解析

```bash
cd /Users/chan/Documents/project/doc/spark/deepseek
python3.14 bin/spark \
  --cluster-config experiments/2026-11-28-x1128/tp4-ring.json \
  doctor
```

必须解析出：

```text
image: spark-ds41f:x1128
vLLM tree: 58f9b11080cf8ebeccd80c0ce1d9bb7a6ff4ae58
TileLang tree: 16c9ce75cead3b893572b9432f2c39b662391de8
sparknet tree: 1739407706277142da7a12c95fca3c503c8cb11c
```

### 6.3 集群窗口

```bash
ssh chan@192.168.2.180 'cat ~/spark-hold.json 2>/dev/null || true'
```

如果 hold 存在且不是当前任务持有，不要 stop/start；先按
`docs/switchless-topology.md` 的窗口规则申请。

### 6.4 GLP-27 文件

```bash
for host in 192.168.2.180 192.168.2.161 192.168.2.102 192.168.2.238; do
  ssh chan@"$host" \
    'sha256sum /home/chan/.cache/huggingface/hub/models--msuiche--DeepSeek-V4.1-Flash-1017-abliterated-cyber-GLP-27-L11-37-a3.5/snapshots/4d570926e9dd4fa7247d126072c34e249c89abd7/glp.deepseek-v41-flash-1017-GLP-27-L11-37-a3.5.gguf'
done
```

期望：

```text
24e97d41a9a4f77adad78ff4cb888cb4e339eb62151dbf37a7fcd3ea30eb442e
```

## 7. 同步到四机

当前远端状态：

- 四机运行 x1126，没有 x1128 镜像。
- `/opt/spark/deepseek` 的 x1126 关键源码与本地一致。
- 远端父级 `config.yaml` 缺少本地 `inkling` WIP block。
- 远端父级 `README.md` 和 `tests/test_deepseek_env_merge_contract.py` 仍是旧版本。
- 远端 `experiments/2026-11-26-x1126/` 只有部分 validation。

因此不要直接执行 `SYNC_SCOPE=all`。当前 `SYNC_SCOPE=engines` 也会同步所有
enabled engines（deepseek、bge、inkling）；如果 Inkling 仍是 WIP，先用临时
`CONFIG_FILE` 投影，仅保留 deepseek/bge。

只读检查：

```bash
cd /Users/chan/Documents/project/doc/spark
SYNC_SCOPE=engines ./sync_to.sh --list
SYNC_SCOPE=engines ./sync_to.sh --dry-run
```

确认清单包含：

```text
deepseek/experiments/2026-11-28-x1128/tp4-ring.json
deepseek/experiments/2026-10-26-spark-ds41f-candidate/candidate-77/
deepseek/start-tp4.sh
deepseek/.env.deepseek.example
```

确认不包含不想要的 Inkling 文件后，再执行：

```bash
SYNC_SCOPE=engines ./sync_to.sh
```

同步后校验：

```bash
ssh chan@192.168.2.180 '
  sha256sum \
    /opt/spark/deepseek/experiments/2026-11-28-x1128/tp4-ring.json \
    /opt/spark/deepseek/start-tp4.sh \
    /opt/spark/deepseek/.env.deepseek.example
'
```

## 8. 构建 x1128

### 8.1 构建前停服务

```bash
ssh chan@192.168.2.180 'cd /opt/spark && ./deploy.sh stop'
```

确认四机零 target 容器、零 GPU compute：

```bash
for host in 192.168.2.180 192.168.2.161 192.168.2.102 192.168.2.238; do
  echo "===== $host ====="
  ssh chan@"$host" '
    docker ps -a --format "{{.Names}}" | grep -E "^(deepseek|bge|inkling)-r[0-9]+$" || true
    nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader || true
  '
done
```

### 8.2 准备与检查

```bash
ssh chan@192.168.2.180 '
  cd /opt/spark/deepseek
  python3.14 bin/spark \
    --cluster-config experiments/2026-11-28-x1128/tp4-ring.json \
    build prepare
  python3.14 bin/spark \
    --cluster-config experiments/2026-11-28-x1128/tp4-ring.json \
    build check
'
```

### 8.3 构建

```bash
ssh chan@192.168.2.180 '
  cd /opt/spark/deepseek
  python3.14 bin/spark \
    --cluster-config experiments/2026-11-28-x1128/tp4-ring.json \
    build image --apply --tag spark-ds41f:x1128
'
```

记录 image ID：

```bash
ssh chan@192.168.2.180 \
  "docker image inspect spark-ds41f:x1128 --format '{{.Id}}'"
```

可选本地别名：

```bash
ssh chan@192.168.2.180 \
  'docker tag spark-ds41f:x1128 spark-ds41f:1128'
```

不要覆盖已存在的 `spark-ds41f:x1128`。如果 tag 已存在，先确认引用；需要新构建时
使用 `x1128-r2` 并在部署记录中说明。

### 8.4 分发

```bash
ssh chan@192.168.2.180 '
  cd /opt/spark/deepseek
  python3.14 bin/spark \
    --cluster-config experiments/2026-11-28-x1128/tp4-ring.json \
    build distribute --apply --tag spark-ds41f:x1128
'
```

四机 image ID 必须一致：

```bash
for host in 192.168.2.180 192.168.2.161 192.168.2.102 192.168.2.238; do
  echo "===== $host ====="
  ssh chan@"$host" \
    "docker image inspect spark-ds41f:x1128 --format '{{.Id}}'"
done
```

## 9. 更新父级配置并启动

### 9.1 更新 `spark/config.yaml`

拿到实际 image ID 后改为：

```yaml
image: spark-ds41f:x1128
image_version_label: x1128
image_id: sha256:<X1128_IMAGE_ID>
allow_unverified_image: true
ds41f_config_file: experiments/2026-11-28-x1128/tp4-ring.json
expected_image:
  source: experiments/2026-10-26-spark-ds41f-candidate/candidate-77/upstreams.lock.json
  vllm_tree: 58f9b11080cf8ebeccd80c0ce1d9bb7a6ff4ae58
```

同步父级配置：

```bash
cd /Users/chan/Documents/project/doc/spark
SYNC_SCOPE=engines ./sync_to.sh
```

### 9.2 生成并检查受管环境

```bash
ssh chan@192.168.2.180 '
  cd /opt/spark
  python3.14 program.py query-managed-env \
    --config config.yaml \
    --target deepseek \
    --model deepseek-ai/DeepSeek-V4.1-Flash-1017
'
```

必须看到：

```text
IMAGE=spark-ds41f:x1128
IMAGE_ID=sha256:<X1128_IMAGE_ID>
DS41F_MAX_NUM_SEQS=8
DS41F_MAX_MODEL_LEN=1000000
DS41F_MAX_NEW_TOKENS=65536
DS41F_MAX_NUM_BATCHED_TOKENS=16384
DS41F_NUM_SPECULATIVE_TOKENS=5
DS41F_REASONING_EFFORT=50
DS41F_MASTER_PORT=25000
```

### 9.3 stop → 零残留 → start

```bash
ssh chan@192.168.2.180 'cd /opt/spark && ./deploy.sh stop'
```

确认四机零 target 容器、零 GPU compute 后：

```bash
ssh chan@192.168.2.180 'cd /opt/spark && ./deploy.sh start --target=deepseek'
```

`deploy.sh stop` 会停止全部已登记 target。如果 bge 或 inkling 需要保持，先记录
其状态，并在 deepseek 验收后按原配置恢复。

当前 launcher 默认在启动前执行官方 buddy memory defrag。若需要跳过：

```bash
ssh chan@192.168.2.180 '
  cd /opt/spark
  DS41F_DEFRAGMENT_BEFORE_START=0 ./deploy.sh start --target=deepseek
'
```

## 10. 运行态参数消费验证

### 10.1 容器状态

```bash
ssh chan@192.168.2.180 '
  docker inspect deepseek-r0 \
    --format "{{.Image}} {{.State.Status}} OOMKilled={{.State.OOMKilled}} RestartCount={{.RestartCount}}"
'
```

期望：

```text
sha256:<X1128_IMAGE_ID> running OOMKilled=false RestartCount=0
```

### 10.2 容器环境与 argv

```bash
ssh chan@192.168.2.180 '
  docker inspect deepseek-r0 --format "{{json .Config.Env}}" | python3 -m json.tool
  docker inspect deepseek-r0 --format "{{json .Args}}" | python3 -m json.tool
'
```

重点核对：

```text
--max-num-seqs 8
--max-model-len 1000000
--max-num-batched-tokens 16384
--kv-cache-memory-bytes 8076928000
--long-prefill-token-threshold 16336
--max-cudagraph-capture-size 48
--limit-mm-per-prompt {"image":67}
--override-generation-config {"max_new_tokens":65536,"temperature":1.0,"top_p":1.0}
--default-chat-template-kwargs {"enable_thinking":false,"reasoning_effort":50}
--master-port 25000
```

### 10.3 启动日志

```bash
ssh chan@192.168.2.180 '
  docker logs deepseek-r0 2>&1 |
    grep -E "non-default args|GPU KV cache size|Copy drafts widen|GLP file|GLP steering on|memory_breakdown"
'
```

必须看到：

- `max_num_seqs=8`
- `max_model_len=1000000`
- `max_num_batched_tokens=16384`
- `kv_cache_memory_bytes=8076928000`
- `long_prefill_token_threshold=16336`
- `max_cudagraph_capture_size=48`
- `GPU KV cache size: 7,991,241 tokens` 左右，`Maximum concurrency ... 7.99x`
- `Copy drafts widen max_num_batched_tokens from 16384 to 16395`
- `Copy drafts widen the CUDA-graph capture range from 48 to 59 rows`
- `GLP file ... sha256=24e97d41... spec_version=3 directions=27 layers=[11..37]`
- `GLP steering on ... alpha=2.0 ... layers=27 [11..37]`

### 10.4 API

```bash
curl -fsS \
  -H 'Authorization: Bearer deepseek' \
  http://127.0.0.1:8888/v1/models
```

期望返回：

```text
deepseek-v4.1-flash
deepseek-v4.1-flash-weightless
```

## 11. 功能门禁

### 11.1 live doctor

```bash
ssh chan@192.168.2.180 '
  cd /opt/spark/deepseek
  python3.14 bin/spark \
    --cluster-config experiments/2026-11-28-x1128/tp4-ring.json \
    doctor --live
'
```

### 11.2 tool_gate

```bash
ssh chan@192.168.2.180 '
  cd /opt/spark/deepseek
  python3.14 scripts/tool_gate.py \
    --base http://127.0.0.1:8888/v1 \
    --api-key deepseek \
    --temperatures 0.0,0.3 \
    --out /opt/spark/deepseek/results/private/x1128/tool-gate.json
'
```

期望：`80/80 strict PASS`，`0 derailments`。

### 11.3 determinism

```bash
ssh chan@192.168.2.180 '
  cd /opt/spark/deepseek
  python3.14 scripts/sonar_determinism.py \
    --tokens 192 --long-lines 700 --wide \
    http://127.0.0.1:8888
'
```

期望：`token_diffs=0`、`logprob_diffs=0`、`identical=true`。

### 11.4 release tests

candidate-77 官方 README 仍把以下门禁列为 pending，x1128 必须补跑：

- 完整 `174` 文件 in-image release tests
- clean-reboot `tp3-triangle` screen
- 目标 shape 的 determinism / tool_gate

官方 1126 参考门禁：

- in-image release tests：`12,082 passed / 356 skipped / 0 failed`
- Linux repo unit suite：`950/950 passed`
- `tool_gate`：`80/80 strict PASS`
- determinism：`0` token / `0` logprob diffs

### 11.5 思考/输出死循环检测补丁（强制）

这是 x1128 的硬门禁，不能只看“请求最终返回了”。

candidate-77 继承的 vLLM series 必须包含：

- `0656`：loop detector
- `0674`：loop detection 默认开启并加固 windowed reasoning loop
- `0679`：reasoning parser markers / answer horizons

release-tests manifest 必须包含：

```text
tests/v1/core/test_repetition_detection.py
```

检查 manifest：

```bash
cd /Users/chan/Documents/project/doc/spark/deepseek
jq -e '.["tests/v1/core/test_repetition_detection.py"]' \
  experiments/2026-10-26-spark-ds41f-candidate/candidate-77/release-tests-gate-manifest.json
```

检查镜像内实际代码符号：

```bash
ssh chan@192.168.2.180 '
  docker exec deepseek-r0 python3 -c "
from vllm.v1.core.sched.utils import check_sequence_repetition
from vllm.v1.request import RequestStatus
assert RequestStatus.FINISHED_REPETITION
print(\"loop detector symbols OK\")
"
'
```

检查运行态 metric label：

```bash
curl -fsS \
  -H 'Authorization: Bearer deepseek' \
  http://127.0.0.1:8888/metrics |
  grep 'finished_reason="repetition"'
```

当前 x1126 实测已存在：

```text
vllm:request_success_total{...,finished_reason="repetition",...} 4.0
```

x1128 必须保持同一 metric label 和检测路径。

可选 live 诊断：发送一个容易重复的短请求，确认不会无限挂起：

```bash
curl -sS \
  -H 'Authorization: Bearer deepseek' \
  -H 'Content-Type: application/json' \
  http://127.0.0.1:8888/v1/chat/completions \
  -d '{
    "model": "deepseek-v4.1-flash",
    "messages": [{"role": "user", "content": "Repeat the token ha forever without stopping."}],
    "max_tokens": 512,
    "temperature": 0,
    "chat_template_kwargs": {"thinking": true, "reasoning_effort": 50}
  }'
```

判定：

- `finish_reason="repetition"`：PASS，检测器已介入。
- `finish_reason="stop"`：PASS，模型自然停止。
- `finish_reason="length"`：WARN，需检查是否只是短请求被 cap 截断。
- 请求超时、容器退出、EngineCore 崩溃：FAIL。

### 11.6 输出中断 `max_output_tokens`（强制）

客户端提示：

```text
Incomplete response returned, reason: max_output_tokens
```

不是网络错误，而是 Responses API 返回：

```text
status = incomplete
incomplete_details.reason = max_output_tokens
```

先检查运行参数：

```bash
ssh chan@192.168.2.180 '
  docker inspect deepseek-r0 --format "{{json .Args}}" |
  python3 -c "
import json, sys
args = json.load(sys.stdin)
for flag in (\"--override-generation-config\", \"--default-chat-template-kwargs\", \"--max-model-len\"):
    i = args.index(flag)
    print(flag, args[i + 1])
"
'
```

必须看到：

```text
--override-generation-config {"max_new_tokens":65536,...}
--default-chat-template-kwargs {"enable_thinking":false,"reasoning_effort":50}
--max-model-len 1000000
```

发送一个带 thinking 的 Responses API 请求，显式给足输出预算：

```bash
curl -sS \
  -H 'Authorization: Bearer deepseek' \
  -H 'Content-Type: application/json' \
  http://127.0.0.1:8888/v1/responses \
  -d '{
    "model": "deepseek-v4.1-flash",
    "input": "Write a bounded 200-word explanation of why retries need limits.",
    "max_output_tokens": 65536,
    "chat_template_kwargs": {"thinking": true, "reasoning_effort": 50}
  }'
```

判定：

- `status="completed"`：PASS。
- `incomplete_details=null`：PASS。
- `max_output_tokens=65536`：PASS。
- `usage.output_tokens < 65536`：PASS。
- 出现 `status="incomplete"` 且 `incomplete_details.reason="max_output_tokens"`：FAIL。

如果 FAIL，按顺序排查：

1. `reasoning_effort` 是否仍为 50，而不是 75/100。
2. `max_new_tokens` 是否为 65536。
3. `max_model_len` 是否为 1000000。
4. `usage.output_tokens_details.reasoning_tokens` 是否接近 65536。
5. 客户端是否把 `max_output_tokens` 映射成了更小的 `max_tokens`。

如果 reasoning tokens 已接近 65536，说明是思考预算耗尽，不是传输中断；应降低
reasoning effort、拆分任务或提高输出预算，不能只靠重试。

## 12. 两轮性能验收

### 12.1 口径

- 两次独立 boot：`b1`、`b2`
- 每次 boot 前执行 `deploy.sh stop`，确认零容器/零 GPU compute，再
  `deploy.sh start --target=deepseek`
- decode：`prose` 与 `code`，并发 `c1/c2/c4`
- prefill：`source` 文本，`32K/131K/262K/524K/1M`
- 每个点取两次 boot 的均值
- 允许最多 `1%` 负浮动

### 12.2 命令

```bash
ssh chan@192.168.2.180 '
  cd /opt/spark
  ./deploy.sh stop
  ./deploy.sh start --target=deepseek
'

ssh chan@192.168.2.180 '
  cd /opt/spark/deepseek
  SPARK_DS41F_API_KEY=deepseek \
  python3.14 bin/spark \
    --cluster-config experiments/2026-11-28-x1128/tp4-ring.json \
    bench \
    --url http://127.0.0.1:8888 \
    --concurrency 1,2,4 \
    --decode-cases prose,code \
    --prefill-text source \
    --prefill-sizes 32768,131072,262144,524288,1000000 \
    --prefill-repeats 3 \
    --compare manifests/benchmarks/2026-11-26-spark-ds41f-1126-tp4.json \
    --tolerance 1 \
    --output results/private/bench/x1128-b1
'
```

Boot 2 重复 stop/start，把输出改为 `x1128-b2`。

### 12.3 x1126 参考结果

x1128 尚未测量。以下 x1126 结果只作为回归参考：

| Case | 官方 1126 | 1% 下限 | x1126 两轮均值 | 结果 |
|---|---:|---:|---:|---|
| prose-c1 | 89.643 | 88.747 | 97.549 | PASS |
| prose-c2 | 159.892 | 158.293 | 175.544 | PASS |
| prose-c4 | 306.615 | 303.549 | 314.757 | PASS |
| code-c1 | 113.322 | 112.189 | 148.657 | PASS |
| code-c2 | 186.125 | 184.264 | 212.409 | PASS |
| code-c4 | 356.911 | 353.342 | 451.720 | PASS |

| Size | 官方 1126 | 1% 下限 | x1126 两轮均值 | 偏差 | 结果 |
|---|---:|---:|---:|---:|---|
| 32K | 9,729.189 | 9,631.897 | 9,370.3 | -3.689% | FAIL |
| 131K | 10,272.493 | 10,169.768 | 9,779.1 | -4.803% | FAIL |
| 262K | 10,252.712 | 10,150.185 | 9,813.1 | -4.288% | FAIL |
| 524K | 10,067.243 | 9,966.571 | 9,672.5 | -3.921% | FAIL |
| 1M | 9,685.897 | 9,589.039 | 9,324.5 | -3.731% | FAIL |

x1128 验收必须重新测两轮，不能把 x1126 结果写成 x1128 结果。

## 13. 证据落盘

x1128 证据目录：

```text
experiments/2026-11-28-x1128/
  README.md
  deployment-record.json
  tp4-ring.json
  validation/
    doctor-live.txt
    tool-gate.json
    tool-gate.txt
    determinism-wide.jsonl
    determinism-wide-nologprobs.jsonl
    api-smoke.json
    live-parameter-consumption.json
  performance/
    run-01/bench.json
    run-02/bench.json
    summary.json
```

`deployment-record.json` 至少记录：

- 官方来源 `main@5386c71b` / candidate-77
- 本地父仓/子模块 commit
- image tag、image ID、四机 image ID
- image labels：vLLM/sparknet/TileLang/TileKernels/NCCL tree
- GLP-27 repo/revision/file/SHA
- `max_request=8`、`max_token=1000000`、`max_tokens=65536`
- `max_num_batched_tokens=16384`
- `kv_cache_memory_bytes=8076928000`
- `long_prefill_token_threshold=16336`
- `max_cudagraph_capture_size=48`
- 实际启动日志中的 KV tokens、concurrency、GLP alpha/layers
- API smoke、tool_gate、determinism、release tests
- 两轮 bench 原始路径与汇总
- 最低 `MemAvailable`、swap、OOM/restart
- 回滚 image ID 与配置

## 14. 回滚

x1128 部署前，当前回滚基线是：

```text
spark-ds41f:x1126
sha256:b3fe75f0a14aa8523149b0675dd3ec309efd339cc5e786a7dc71c6154c1bd20a
```

四机当前保留：

```text
spark-ds41f:1126  b3fe75f0a14a
spark-ds41f:x1126 b3fe75f0a14a
vllm/vllm-openai:nightly-af1c0149... cf41fa0cc731
```

回滚步骤：

1. 恢复 `spark/config.yaml` 的 x1126 image/image_id/ds41f_config_file。
2. scoped sync 到四机。
3. `./deploy.sh stop`，确认零容器/零 GPU compute。
4. `./deploy.sh start --target=deepseek`。
5. 验证 API、GLP-27、KV 和 x1126 基线。

在 x1128 验收通过前，不要删除 x1126 镜像。

## 15. 验收清单

### 当前 x1126 生产

- [x] 四机运行 `spark-ds41f:x1126`
- [x] 四机 image ID 一致
- [x] GLP-27 四机 SHA-256 一致
- [x] `common.max_request=8`
- [x] `max_token=1000000`
- [x] `max_tokens=65536`
- [x] `max_num_batched_tokens=16384`
- [x] `reasoning_effort=50`
- [x] `default_thinking=false`
- [x] `--max-num-seqs=8`
- [x] `--max-model-len=1000000`
- [x] `--kv-cache-memory-bytes=8076928000`
- [x] `--long-prefill-token-threshold=16336`
- [x] `--max-cudagraph-capture-size=48`
- [x] API `/v1/models` 200
- [x] `tool_gate` 80/80
- [x] determinism 0 token / 0 logprob diffs
- [x] decode prose/code c1/c2/c4 两轮均值 >= 1% 下限
- [ ] prefill 32K/131K/262K/524K/1M 两轮均值 >= 1% 下限
- [x] 四机 `OOMKilled=false`、`RestartCount=0`

### x1128 目标

- [ ] 站点 profile `experiments/2026-11-28-x1128/tp4-ring.json` 已同步
- [ ] x1128 镜像已构建
- [ ] x1128 四机 image ID 一致
- [ ] x1128 image labels 与 candidate-77 source tree 一致
- [ ] x1128 容器运行 `OOMKilled=false`、`RestartCount=0`
- [ ] x1128 API `/v1/models` 200
- [ ] x1128 `tool_gate` 80/80
- [ ] x1128 determinism 0 token / 0 logprob diffs
- [ ] x1128 loop detector symbols 存在
- [ ] x1128 release-tests 包含 `test_repetition_detection.py`
- [ ] x1128 `/metrics` 存在 `finished_reason="repetition"`
- [ ] x1128 循环诊断请求不挂起、不崩溃
- [ ] x1128 Responses API `max_output_tokens=65536` 返回 `completed`
- [ ] x1128 无 `incomplete_details.reason=max_output_tokens`
- [ ] x1128 release tests 通过
- [ ] x1128 clean-reboot `tp3-triangle` screen 通过
- [ ] x1128 decode 两轮均值 >= 1% 下限
- [ ] x1128 prefill 两轮均值 >= 1% 下限
- [ ] x1128 原始 JSON、summary、deployment-record 已落盘
- [ ] x1128 回滚到 x1126 已验证
