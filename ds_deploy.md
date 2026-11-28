# spark/deepseek x1128 部署与验收记录

> 日期：2026-11-28
> 最终运行态：`spark-ds41f:x1128`，采用已测最好的一次官方 tp4-ring 参数配方：
> 32 并发、1,048,576 上下文、16.5 GiB KV、192-row CUDA graph、
> `max_new_tokens=65536`、`default_thinking=false`、`reasoning_effort=50`。
> 功能、循环检测和 Responses 输出预算门禁通过；官方 suite 性能复验仍低于
> 99% 下限，结果已如实记录，不把性能写成 PASS。GPU 频率已恢复
> `2400,3003` 锁定。x1128 来源为官方 `main@5386c71b` / candidate-77。

## 1. 当前结论

### 1.1 当前生产

- 父仓当前提交：`b88a54e0 Update DeepSeek deployment gates pointer`
- 子模块当前提交：`0f705503 Add loop and output-budget deployment gates`
- 当前生产镜像：`spark-ds41f:x1128`
- 当前 image ID：
  `sha256:001305bdec0a5133b9112827ae0b730fc407cbdb13e6abc1b8cfc8c7c7c26e0e`
- 四机 `deepseek-r0..3` 均运行 `spark-ds41f:x1128`
- 生产 profile：`experiments/2026-11-28-x1128/tp4-ring.json`
- `VLLM_SERVER_DEV_MODE=0`
- 当前 `OOMKilled=false`、`RestartCount=0`

x1128 镜像锁：

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

- x1128 功能：API、tool_gate 80/80、determinism 0 diff、loop detector、
  Responses `max_output_tokens=65536` 全部 PASS。
- 完整 `174` 文件 release tests：`11,796 passed / 641 skipped / 1 failed`。
  唯一失败是 TP4 不使用的 display-carveout 的 RM scanout 分配；x1126
  生产镜像在四机复跑结果相同，属于站点共同基线条件。
- 官方 suite 最好完整 run 为 boot-a：single-stream `c1=86.137`、
  release `c8=207.383` / `c16=273.290` / `poisson=180.273`、
  sampled `c16/c8/c1=267.633/203.947/84.690`，
  prefill `9,451.657/9,971.343/9,947.720/9,774.149/9,403.394 tok/s`
  （32K/131K/262K/524K/1M）。
- 除 Poisson 持平外，官方同参数性能点仍低约 `2.9%–4.3%`；因此性能验收
  状态保持 HOLD，不宣称 PASS。原始 JSON 和逐点差值见
  `performance/summary-final.json`。
- 站点基线对照：x1126 的官方 decode diagnostic `prose-c1=82.175`，低于
  x1128 的 `86.137`；x1126 source-text prefill 为
  `9,341/9,934/9,937/9,760/9,407 tok/s`，与 x1128 基本同级。因此差距
  不是 x1128 引入的回归。
- clean-reboot `tp3-triangle` 对本站点为 N/A：物理拓扑是
  spark-a→b→c→d→a 的 TP4 环，没有三节点三角所需的第三条直达链路。
- GPU 频率已重新锁定 `2400,3003`。

## 2. 官方最新代码与生产边界

官方最新 `main` 已包含：

- `candidate-75`：DFly2 standalone drafter，未 promotion；GB10 上明显慢于 DSpark。
- `candidate-76`：DSpark truncated-depth CUDA graph replay + 2 MiB hugepage defrag。
- `candidate-77`：TileLang `0.1.16` rebase，x1128 的来源。

这些 candidate 已进入本地源码，但官方 `config/cluster.json` 仍是 1126 生产配置。
x1128 是站点 candidate 部署，不是官方 production promotion。

当前站点 launcher 已对齐官方新能力：

- 当前 x1128 没有启用 standalone `drafter`，生产继续使用 tp4-ring 固定
  DSpark argv。
- profile 选择官方 `drafter` / `drafters` 时，launcher 使用 `recipe.resolve`
  生成的 `serve_args`，并补挂 `/hub` 或 `/drafter-*`。
- 启动前默认调用官方 `spark_ds41f.cluster.defragment_buddy_memory`。
- 如需临时关闭 defrag：

```bash
export DS41F_DEFRAGMENT_BEFORE_START=0
```

## 3. 配置口径

### 3.1 当前生产配置

当前 `spark/config.yaml` 为：

```yaml
image: spark-ds41f:x1128
image_version_label: x1128
image_id: sha256:001305bdec0a5133b9112827ae0b730fc407cbdb13e6abc1b8cfc8c7c7c26e0e
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

### 3.2 生产 profile

生产使用 `experiments/2026-11-28-x1128/tp4-ring.json`，不带
`VLLM_SERVER_DEV_MODE`。性能复验使用独立的
`tp4-ring-dev.json` + `tp4-ring-dev.runtime.json` 临时投影，验收后不进入生产。

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
max_request: 32
max_token: 1048576
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

## 4. 32 并发固定 KV

最终采用官方 x1128 tp4-ring 固定池：

```text
max_num_seqs:              32
max_model_len:             1,048,576
max_num_batched_tokens:    16,384
kv_cache_memory_bytes:     17,716,740,096
max_cudagraph_capture_size: 192
long_prefill_token_threshold: 16,192
```

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

- 四机当前运行 x1128，四机 image ID 一致，`VLLM_SERVER_DEV_MODE=0`。
- `/opt/spark/deepseek` 的 x1128 关键源码、profile 和受管环境与本地一致。
- 远端父级 `config.yaml` 已是 x1128 最好参数配方。
- `experiments/2026-11-28-x1128/` 已包含部署、功能门禁、性能 A/B、
  最终运行态和频率证据。

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
  python3 bin/spark \
    --cluster-config experiments/2026-11-28-x1128/tp4-ring.json \
    build prepare
  python3 bin/spark \
    --cluster-config experiments/2026-11-28-x1128/tp4-ring.json \
    build check
'
```

### 8.3 构建

```bash
ssh chan@192.168.2.180 '
  cd /opt/spark/deepseek
  python3 bin/spark \
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
  python3 bin/spark \
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
  python3 program.py query-managed-env \
    --config config.yaml \
    --target deepseek \
    --model deepseek-ai/DeepSeek-V4.1-Flash-1017
'
```

必须看到：

```text
IMAGE=spark-ds41f:x1128
IMAGE_ID=sha256:<X1128_IMAGE_ID>
DS41F_MAX_NUM_SEQS=32
DS41F_MAX_MODEL_LEN=1048576
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
--max-num-seqs 32
--max-model-len 1048576
--max-num-batched-tokens 16384
--kv-cache-memory-bytes 17716740096
--long-prefill-token-threshold 16192
--max-cudagraph-capture-size 192
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
- `max_model_len=1048576`
- `max_num_batched_tokens=16384`
- `kv_cache_memory_bytes=17716740096`
- `long_prefill_token_threshold=16192`
- `max_cudagraph_capture_size=192`
- `GPU KV cache size: 18,702,259 tokens`，`Maximum concurrency ... 17.84x`
- `Copy drafts widen max_num_batched_tokens from 16384 to 16395`
- `Copy drafts widen the CUDA-graph capture range from 192 to 203 rows`
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
  python3 bin/spark \
    --cluster-config experiments/2026-11-28-x1128/tp4-ring.json \
    doctor --live
'
```

### 11.2 tool_gate

```bash
ssh chan@192.168.2.180 '
  cd /opt/spark/deepseek
  python3 scripts/tool_gate.py \
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
  python3 scripts/sonar_determinism.py \
    --tokens 192 --long-lines 700 --wide \
    http://127.0.0.1:8888
'
```

期望：`token_diffs=0`、`logprob_diffs=0`、`identical=true`。

### 11.4 release tests

candidate-77 的 `174` 文件 in-image release tests 已补跑完成：

- `11,796 passed / 641 skipped / 1 failed`
- 唯一失败：`test_display_carveout.py::test_cross_process_isolation_and_sigkill_reclamation`
- 失败为 `NV_ESC_RM_ALLOC status=0x51`；四机以及 x1126 生产镜像复跑结果相同，
  属于站点共同基线条件，不是 x1128 回归
- 目标 shape 的 determinism / tool_gate 已完成
- clean-reboot `tp3-triangle` screen 仍未执行

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
jq -e '.files["tests/v1/core/test_repetition_detection.py"]' \
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

当前 x1128 生产实测已存在：

```text
vllm:request_success_total{...,finished_reason="repetition",...}
```

最终镜像保持同一 metric label 和检测路径；符号、metric、40/40 release
test 与 live probe 均已通过。

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
--max-model-len 1048576
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
3. `max_model_len` 是否为 1048576。
4. `usage.output_tokens_details.reasoning_tokens` 是否接近 65536。
5. 客户端是否把 `max_output_tokens` 映射成了更小的 `max_tokens`。

如果 reasoning tokens 已接近 65536，说明是思考预算耗尽，不是传输中断；应降低
reasoning effort、拆分任务或提高输出预算，不能只靠重试。

## 12. 两轮性能验收

### 12.1 验收口径

性能验收对齐官方 `README.md` 的 Performance 描述，并只比较候选与实际部署
都使用相同参数的指标。`bin/spark bench --compare` 的 `step_ms` 回归用于诊断，
不是本站点 x1128 的验收门槛。

- 两次独立 boot：`b1`、`b2`
- 每次 boot 前执行 `deploy.sh stop`，确认零容器/零 GPU compute，再
  `deploy.sh start --target=deepseek`
- 唯一参照是官方 suite run，不是 `manifests/benchmarks/*.json` 的内部矩阵：
  - TP4 `c1`：`manifests/suites/2026-11-26-spark-ds41f-1126-tp4-single-stream/`
  - TP4 `c8/c16/poisson/stall/prefill`：
    `manifests/suites/2026-11-26-spark-ds41f-1126-tp4/`
  - TP4 sampled `c1/c8/c16`：
    `manifests/suites/2026-11-26-spark-ds41f-1126-tp4-sampled/`
- 上述官方点及其 suite digest、prefill prompt tokens 固化在：
  `experiments/2026-11-28-x1128/performance/official-suite-reference.json`
- 官方 TP4 Performance 表没有 `c2/c4`。它们不参与 TP4 验收；TP3 triangle
  的官方表才有 `c4`。
- 每个点取两次独立 boot 的结果，且每个同参数点的两轮合并均值不得低于
  官方 run 的 `99%`
- suite digest、node count、transport 和 kernel backend 必须与参照一致
- 质量标准 `5/5`、零失败请求、零 thermal slowdown、零 swap 增长仍是硬门禁

官方 suite 参数是 thinking off、`max_request=32`、
`max_token=1048576`。性能验收必须使用临时父级配置投影和 dev profile，
对齐这些参数；验收完成后恢复站点正式配置。1M prefill 只有在
`max_model_len=1,048,576` 时才是同参数，否则必须判为 SKIP/FAIL。

#### 12.1.1 官方 prefill 参照

prefill 的唯一参照是 release suite（digest
`2c1698722fafa68c534a2197673c418fa9b465c0dbaa70f53d9105a4271c4d87`）
的 `prefill` step。它与内部 `manifests/benchmarks/*.json` matrix，以及单独
运行、参数不同的 `sonar prefill` 都不等价。

官方 workload 固定为：

- source-text 冷预填，`max_tokens=1`、`temperature=0`，使用唯一 cache salt；
- 标称尺寸依次为 `32K / 131K / 262K / 524K / 1M`，每个尺寸 `repeats=3`；
- 每个样本的指标是 `prompt_tokens / ttft_s`，其中 `ttft_s` 是请求发出到首个
  streamed token 的时间；
- 每个尺寸的 `tps` 是三个成功样本 tok/s 的算术平均，不是标称尺寸除以总请求
  时间，也不是 output tok/s。

官方同参数值如下：

| 标称尺寸 | 首个样本 prompt tokens | 三个样本 prompt tokens | 官方 tok/s | 99% 下限 |
| ---: | ---: | --- | ---: | ---: |
| 32K | 32,761 | 32,761 / 33,374 / 33,705 | 9,729.189 | 9,631.897 |
| 131K | 131,900 | 131,900 / 130,839 / 134,836 | 10,272.493 | 10,169.768 |
| 262K | 262,554 | 262,554 / 262,080 / 262,198 | 10,252.712 | 10,150.185 |
| 524K | 522,774 | 522,774 / 522,103 / 523,855 | 10,067.243 | 9,966.571 |
| 1M | 994,697 | 994,697 / 998,835 / 1,004,733 | 9,685.897 | 9,589.039 |

验收时必须在候选 `release-a/prefill.json` 和 `release-b/prefill.json` 中逐项
核对：suite digest 一致、五个标称尺寸齐全、每尺寸三个样本均成功、实际
prompt token 序列与官方一致、两轮共六个样本的合并 tok/s 均值不低于上表
99% 下限。若 prompt tokens 出现差异，`sonar compare` 会给出
`<reference> vs <candidate> prompt tokens` 警告；此时不得直接把 tok/s
差异归因于镜像性能，必须先排除 tokenizer、model 路径、suite 参数或输入
生成差异。

### 12.2 GPU 频率口径

验收阶段允许解除 GPU 频率限制，完成后必须重新上锁生产频率。

先记录并解除四机频率限制：

```bash
for host in 192.168.2.180 192.168.2.161 192.168.2.102 192.168.2.238; do
  echo "===== $host ====="
  ssh chan@"$host" '
    nvidia-smi --query-gpu=clocks.sm,clocks.max.sm,clocks.gr,power.draw,temperature.gpu \
      --format=csv,noheader
    sudo -n nvidia-smi -rgc
    nvidia-smi --query-gpu=clocks.sm,clocks.max.sm,clocks.gr,power.draw,temperature.gpu \
      --format=csv,noheader
  '
done
```

把前后输出分别落到：

```text
experiments/2026-11-28-x1128/validation/clock-state-before.txt
experiments/2026-11-28-x1128/validation/clock-state-unlocked.txt
```

验收结束后，重新上锁生产频率并再次取证：

```bash
for host in 192.168.2.180 192.168.2.161 192.168.2.102 192.168.2.238; do
  ssh chan@"$host" 'sudo -n nvidia-smi -lgc 2400,3003'
done
```

锁定后的输出落到：

```text
experiments/2026-11-28-x1128/validation/clock-state-locked.txt
```

任一解除或上锁命令失败时，该轮性能证据无效，不得把结果写成 PASS。

### 12.3 命令

第一轮 boot：

```bash
ssh chan@192.168.2.180 '
  cd /opt/spark
  ./deploy.sh stop
  python3 - <<"PY"
from pathlib import Path
import yaml

source = Path("config.yaml")
target = Path("config.acceptance.yaml")
config = yaml.safe_load(source.read_text(encoding="utf-8"))
config["common"]["default_thinking"] = False
config["common"]["max_request"] = 32
config["common"]["max_token"] = 1048576
engine = config["engines"]["deepseek"]
engine["image"] = "spark-ds41f:x1128"
engine["image_version_label"] = "x1128"
engine["image_id"] = "sha256:001305bdec0a5133b9112827ae0b730fc407cbdb13e6abc1b8cfc8c7c7c26e0e"
engine["ds41f_config_file"] = "experiments/2026-11-28-x1128/tp4-ring-dev.json"
engine["runtime_profile"] = "experiments/2026-11-28-x1128/tp4-ring-dev.runtime.json"
engine["expected_image"] = {
    "source": "experiments/2026-10-26-spark-ds41f-candidate/candidate-77/upstreams.lock.json",
    "vllm_tree": "58f9b11080cf8ebeccd80c0ce1d9bb7a6ff4ae58",
}
target.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
PY
  python3 program.py query-managed-env \
    --config config.acceptance.yaml \
    --target deepseek \
    --model deepseek-ai/DeepSeek-V4.1-Flash-1017 |
    grep -E "^(IMAGE|DS41F_DEFAULT_ENABLE_THINKING|DS41F_MAX_NUM_SEQS|DS41F_MAX_MODEL_LEN|DS41F_MAX_NEW_TOKENS|DS41F_MAX_NUM_BATCHED_TOKENS|DS41F_REASONING_EFFORT)\b" |
    sort
  CONFIG_FILE=/opt/spark/config.acceptance.yaml \
    ./deploy.sh start --target=deepseek
'
```

必须解析出：

```text
IMAGE=spark-ds41f:x1128
DS41F_DEFAULT_ENABLE_THINKING=false
DS41F_MAX_NUM_SEQS=32
DS41F_MAX_MODEL_LEN=1048576
DS41F_MAX_NEW_TOKENS=65536
DS41F_MAX_NUM_BATCHED_TOKENS=16384
DS41F_REASONING_EFFORT=50
```

确认容器 argv：

```text
--max-num-seqs 32
--max-model-len 1048576
--kv-cache-memory-bytes 17716740096
--max-cudagraph-capture-size 192
--default-chat-template-kwargs {"enable_thinking":false,"reasoning_effort":50}
```

dev profile 还必须在容器环境中看到：

```text
VLLM_SERVER_DEV_MODE=1
```

第一轮单流 `c1`：

```bash
ssh chan@192.168.2.180 '
  cd /opt/spark/deepseek
  SPARK_DEPLOYMENT_COMMIT=rsync-600b67b51396e29610d5617c7d6c63a5 \
  SPARK_DS41F_API_KEY=deepseek \
  python3 bin/spark \
    --cluster-config experiments/2026-11-28-x1128/tp4-ring-dev.json \
    bench \
    --suite single-stream \
    --url http://127.0.0.1:8888 \
    --compare manifests/suites/2026-11-26-spark-ds41f-1126-tp4-single-stream \
    --tolerance 1 \
    --output results/private/suites/x1128-a-single-stream
'
```

第一轮 release suite（`c8/c16/poisson/stall/prefill`）：

```bash
ssh chan@192.168.2.180 '
  cd /opt/spark/deepseek
  SPARK_DEPLOYMENT_COMMIT=rsync-600b67b51396e29610d5617c7d6c63a5 \
  SPARK_DS41F_API_KEY=deepseek \
  python3 bin/spark \
    --cluster-config experiments/2026-11-28-x1128/tp4-ring-dev.json \
    bench \
    --suite release \
    --url http://127.0.0.1:8888 \
    --compare manifests/suites/2026-11-26-spark-ds41f-1126-tp4 \
    --tolerance 1 \
    --output results/private/suites/x1128-a-release
'
```

第一轮 sampled suite：

```bash
ssh chan@192.168.2.180 '
  cd /opt/spark/deepseek
  SPARK_DEPLOYMENT_COMMIT=rsync-600b67b51396e29610d5617c7d6c63a5 \
  SPARK_DS41F_API_KEY=deepseek \
  python3 bin/spark \
    --cluster-config experiments/2026-11-28-x1128/tp4-ring-dev.json \
    bench \
    --suite sampled \
    --url http://127.0.0.1:8888 \
    --compare manifests/suites/2026-11-26-spark-ds41f-1126-tp4-sampled \
    --tolerance 1 \
    --output results/private/suites/x1128-a-sampled
'
```

prefill 不再单独运行 `sonar prefill`：它已经是 release suite 的 `prefill`
step，必须与官方 `prefill.json` 的实际 prompt tokens 和 tok/s 一起比较。

第二轮重复上述临时配置的 stop/start 和三个 suite，把输出前缀改为
`x1128-b-*`。每个 suite 的 `sonar compare` 输出必须满足：

```text
TP4 single-stream: c1 output tok/s >= 89.543 * 0.99
TP4 release:       c8/c16 output tok/s、poisson、stall、prefill >= 官方 * 0.99
TP4 sampled:       sampled c1/c8/c16 output tok/s >= 官方 * 0.99
```

`decode step geometric mean` 与 `step_ms` 继续作为官方 suite 的诊断项保留，
但不得拿内部 benchmark matrix 的 `c2/c4` 替代官方 suite 的 `c1/c8/c16`。

验收结束后，用正式 `config.yaml` 重新执行
`CONFIG_FILE=/opt/spark/config.yaml ./deploy.sh start --target=deepseek`，
再按 12.2 重新上锁 GPU 频率。

### 12.4 本轮正式结果

boot-a 完成三套官方 suite，是最终选用的完整最好一次 run；boot-b 完成
single-stream 和 release 的 decode/poisson/stall 后，在 prefill 阶段按收尾
要求停止，完成的 decode 点复现了同一负偏差。

| 指标 | 官方 | boot-a | 差值 | 99% 门禁 |
| --- | ---: | ---: | ---: | --- |
| single-stream c1 | 89.543 | 86.137 | -3.80% | FAIL |
| release c16 | 283.327 | 273.290 | -3.54% | FAIL |
| release c8 | 215.327 | 207.383 | -3.69% | FAIL |
| release poisson | 180.130 | 180.273 | +0.08% | PASS |
| sampled c16 | 277.143 | 267.633 | -3.43% | FAIL |
| sampled c8 | 211.250 | 203.947 | -3.46% | FAIL |
| sampled c1 | 87.667 | 84.690 | -3.40% | FAIL |

| prefill | 官方 tok/s | boot-a tok/s | 差值 | 99% 门禁 |
| ---: | ---: | ---: | ---: | --- |
| 32K | 9,729.189 | 9,451.657 | -2.85% | FAIL |
| 131K | 10,272.493 | 9,971.343 | -2.93% | FAIL |
| 262K | 10,252.712 | 9,947.720 | -2.97% | FAIL |
| 524K | 10,067.243 | 9,774.149 | -2.91% | FAIL |
| 1M | 9,685.897 | 9,403.394 | -2.92% | FAIL |

boot-b 的 c1、c16、c8 分别为 `86.033`、`272.333`、`205.970`，确认不是
单次波动。最终选择 boot-a 的完整参数配方进入生产，是因为它是两次性能复验中
各项更快的一次，不表示官方性能门槛已经通过。机器可读结果见
`performance/summary-final.json`。

## 13. 证据落盘

x1128 证据目录：

```text
experiments/2026-11-28-x1128/
  README.md
  deployment-record.json
  tp4-ring.json
  tp4-ring-dev.json
  tp4-ring-dev.runtime.json
  validation/
    doctor-live.txt
    tool-gate.json
    determinism-wide.jsonl
    determinism-wide-nologprobs.jsonl
    api-smoke.json
    live-parameter-consumption.json
    startup-keylines.txt
    final-state.txt
    loop-symbols-final.txt
    loop-metrics-final.txt
    loop-probe-final.json
    loop-release-test.txt
    responses-max-output-tokens-final.json
    clock-state-before.txt
    clock-state-unlocked.txt
    clock-state-locked.txt
    release-tests-full/
  performance/
    official-suite-reference.json
    summary-final.json
    single-stream-a/
    single-stream-b/
    release-a/
    release-b-partial/
    sampled-a/
    diagnostics/
```

`deployment-record.json` 至少记录：

- 官方来源 `main@5386c71b` / candidate-77
- 本地父仓/子模块 commit
- image tag、image ID、四机 image ID
- image labels：vLLM/sparknet/TileLang/TileKernels/NCCL tree
- GLP-27 repo/revision/file/SHA
- `max_request=32`、`max_token=1048576`、`max_tokens=65536`
- `max_num_batched_tokens=16384`
- `kv_cache_memory_bytes=17716740096`
- `long_prefill_token_threshold=16192`
- `max_cudagraph_capture_size=192`
- 实际启动日志中的 KV tokens、concurrency、GLP alpha/layers
- API smoke、tool_gate、determinism、release tests
- 最好完整 run 与第二次 partial run 的原始路径和逐点差值
- 最低 `MemAvailable`、swap、OOM/restart

## 14. 验收清单

### 当前 x1128 生产

- [x] 四机运行 `spark-ds41f:x1128`
- [x] 四机 image ID 一致
- [x] GLP-27 四机 SHA-256 一致
- [x] `common.max_request=32`
- [x] `max_token=1048576`
- [x] `max_tokens=65536`
- [x] `max_num_batched_tokens=16384`
- [x] `reasoning_effort=50`
- [x] `default_thinking=false`
- [x] `--max-num-seqs=32`
- [x] `--max-model-len=1048576`
- [x] `--kv-cache-memory-bytes=17716740096`
- [x] `--long-prefill-token-threshold=16192`
- [x] `--max-cudagraph-capture-size=192`
- [x] API `/v1/models` 200
- [x] `tool_gate` 80/80
- [x] determinism 0 token / 0 logprob diffs
- [x] 官方 `single-stream/release/sampled` suite 完成同 digest 复验
- [ ] 官方同参数性能点达到 99% 下限（当前 c1/c8/c16/sampled/prefill
  低约 2.9%–4.3%；poisson PASS）
- [x] 四机 `OOMKilled=false`、`RestartCount=0`

### 功能与部署门禁

- [x] 站点 profile `experiments/2026-11-28-x1128/tp4-ring.json` 已同步
- [x] x1128 镜像已构建
- [x] x1128 四机 image ID 一致
- [x] x1128 image labels 与 candidate-77 source tree 一致
- [x] x1128 容器运行 `OOMKilled=false`、`RestartCount=0`
- [x] x1128 API `/v1/models` 200
- [x] x1128 `tool_gate` 80/80
- [x] x1128 determinism 0 token / 0 logprob diffs
- [x] x1128 loop detector symbols 存在
- [x] release-tests manifest 的 `.files` 包含
  `tests/v1/core/test_repetition_detection.py`
- [x] 镜像内 `test_repetition_detection.py` 40/40 通过
- [x] x1128 `/metrics` 存在 `finished_reason="repetition"`
- [x] x1128 循环诊断请求不挂起、不崩溃（`finish_reason=stop`）
- [x] x1128 Responses API `max_output_tokens=65536` 返回 `completed`
- [x] x1128 无 `incomplete_details.reason=max_output_tokens`
- [x] 完整 174 文件 release tests 已执行：`11,796 passed / 641 skipped /
  1 failed`；唯一失败为 TP4 不使用的 display-carveout RM scanout，x1126
  同机复跑结果相同
- [x] clean-reboot `tp3-triangle` screen：N/A，本站点为 TP4 环，无 TP3
  三角直达链路
- [x] x1128 原始 JSON、summary-final、deployment-record 已落盘
- [x] GPU 频率已重新锁定 `2400,3003`
