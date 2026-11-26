# spark-ds41f x1126 / official 1126 deployment

日期：2026-11-26

## 结论

- 镜像 `spark-ds41f:x1126` 已从本地 1126 源码构建，并在四机以同一
  image ID 运行：`sha256:b3fe75f0a14aa8523149b0675dd3ec309efd339cc5e786a7dc71c6154c1bd20a`。
- 五棵树与官方 1126 对齐：vLLM `f3783b0e`、sparknet `1739407706`、
  TileLang `741806c7`、TileKernels `cb2fa41c`、NCCL `6af10aa7`。
- GLP-27 alpha=2.0 已实际进入容器；启动日志为
  `alpha=2.0 (alpha_default=3.5, source=override)`。
- 8 并发固定 KV 已实际消费：`8,076,928,000` bytes；启动日志
  `GPU KV cache size: 7,991,241 tokens`、`7.99x`、`kv_cache=7.52 GiB`，
  rank0 剩余 `26.83 GiB`。
- Decode 两轮均值全部超过官方 README 门限，PASS。
- Source-text prefill 两轮均值低于官方 README 门限约 `3.7%–4.8%`，FAIL。
  根因不是漏官方参数，也不是 8 并发方案；详见下文。

## 部署路径

1. 本地 `spark/` 与 `spark/deepseek`（HEAD `bbbebe65`，含未提交 1126
   合并）同步到四台 Spark。
2. `spark-a` 执行 `build prepare` / `build check`，五棵树与 lock 一致。
3. 构建 `spark-ds41f:x1126`，耗时 717s，最低 `MemAvailable 78.7 GiB`，
   GPU / TileLang / sparknet import smoke 全通过。
4. 沿光纤 A→B→C→D 级联 `docker save | ssh docker load`，四机 image ID
   一致。
5. 父级 `ds41f_config_file` 指向站点 overlay
   `experiments/2026-11-26-x1126/tp4-ring.json`；该 overlay 只覆盖
   `container.name_pattern=deepseek-r{rank}`、x1126 tag、站点 node map 和
   `host.fan_control=false`。
6. 每轮性能验收前执行 `deploy.sh stop`，确认 deepseek 容器清零后
   再 `deploy.sh start --target=deepseek`。

## 运行参数实际消费

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
--port 8888
```

启动日志同时出现：

```text
GPU KV cache size: 7,991,241 tokens, Maximum concurrency ... 7.99x
Copy drafts widen max_num_batched_tokens from 16384 to 16395
Copy drafts widen the CUDA-graph capture range from 48 to 59 rows
GLP steering on ... alpha=2.0 ... layers=27 [11..37]
```

## 功能门禁

| 项目 | 结果 |
|---|---|
| 四机容器 | `deepseek-r0..3` running，`OOMKilled=false`，`RestartCount=0` |
| API | `/v1/models` 200，返回 `deepseek-v4.1-flash` / `deepseek-v4.1-flash-weightless` |
| `doctor --live` | `configuration OK; live cluster matches`（8 条建议类 warning） |
| `tool_gate` | 80/80 strict PASS，0 derailments |
| determinism | 6 scenarios，0 token diffs，0 logprob diffs，`identical=true` |
| memguard | 四机 steady memguard active（3 GiB / 2s） |
| swap | 两轮 bench 增长 0.0 GiB |

## 两轮性能均值

### Decode（`decode_window_tps`，官方 README 口径）

| 点 | b1 | b2 | 均值 | 官方 | 1% 下限 | 结果 |
|---|---:|---:|---:|---:|---:|---|
| prose-c1 | 97.422 | 97.676 | 97.549 | 89.643 | 88.747 | PASS |
| prose-c2 | 175.569 | 175.520 | 175.544 | 159.892 | 158.293 | PASS |
| prose-c4 | 315.502 | 314.011 | 314.757 | 306.615 | 303.549 | PASS |
| code-c1 | 148.432 | 148.882 | 148.657 | 113.322 | 112.189 | PASS |
| code-c2 | 212.545 | 212.272 | 212.409 | 186.125 | 184.264 | PASS |
| code-c4 | 451.954 | 451.486 | 451.720 | 356.911 | 353.342 | PASS |

bench 同时报告的 `step_ms` 回归不是吞吐回归：本站 `tokens_per_step` 为
2.7（prose）/4.5（code），高于参考的 2.3/3.9，单步产出更多 token。

### Source-text prefill

| 尺寸 | b1 | b2 | 均值 | 官方 | 1% 下限 | 结果 |
|---|---:|---:|---:|---:|---:|---|
| 32K | 9370.7 | 9369.9 | 9370.3 | 9729.189 | 9631.897 | FAIL |
| 131K | 9765.1 | 9793.2 | 9779.1 | 10272.493 | 10169.768 | FAIL |
| 262K | 9811.0 | 9815.2 | 9813.1 | 10252.712 | 10150.185 | FAIL |
| 524K | 9668.5 | 9676.5 | 9672.5 | 10067.243 | 9966.571 | FAIL |
| 1M | 9313.9 | 9335.0 | 9324.5 | 9685.897 | 9589.039 | FAIL |

## Prefill 差距分析

1. **不是漏官方参数。** `recipe.resolve("tp4-ring")` 的 88 个 environment
   键与站点 `.env.deepseek.example` 完全一致，值也一致；五棵树 labels
   与官方 1126 一致。
2. **不是 8 并发方案为主因。** 临时控制实验切到官方 32 并发
   / `kv=17,716,740,096` / `capture=192` / `max_len=1,048,576`，同源
   source prefill 仅约 +0.5%–1%，仍不到官方表。
3. **宿主 source corpus/tokenization 是主要差异。** 本站
   `source_text` 实际 token 数明显低于官方 suite：1M 档约 911k，官方约
   995k；32K 档也常低于目标。`prefill_tps` 的分母和固定开销摊薄因此
   不同。
4. **host-independent filler 对照**：32K `9933` vs 官方 manifest
   `10237`（-3.0%）；131K `10361` vs `10638`（-2.6%）。说明服务器
   本身接近官方，但仍有约 2.6%–3.0% 的站点 /30 无交换环网开销。
5. 官方 suite 的 fan-control/thermal 与本站相近（官方 max power
   64.9–68.2W，本站 filler 62.7–65.8W；thermal slowdown 均为 0）。

因此当前 prefill 未达到计划要求的 1% 负浮动门限。若必须按计划表验收，
需要先统一 source-text 语料/tokenization，或在业务上接受 /30 环网与
宿主语料带来的约 3%–5% 差异。

## 收尾配方

本次收尾保留当前**已验证最佳配方**：

- 官方 1126 environment（88/88 键值一致）+ 站点 overlay +
  `max_request=8` 固定 KV `8,076,928,000` bytes；
- `max_num_batched_tokens=16384`、`capture=48`、GLP-27 alpha=2.0；
- 未叠加 x1030 时代的 8 MiB all-gather / QPS=4 / 8 MiB NCCL buffer：
  旧记录中 all-gather 单独仅 +0.2%，QPS=4 与 8 MiB buffer 为负，且会偏离
  官方 environment；
- 32 并发 / 16.5 GiB KV / capture=192 的控制 boot 只用于诊断，未保留。

最终四机仍是 `deepseek-r0..3` running，`OOMKilled=false`、
`RestartCount=0`，API 200。

## 复现命令

```bash
# 构建与分发
ssh chan@192.168.2.180 '
  cd /opt/spark/deepseek
  python3 bin/spark --cluster-config config/cluster.json build prepare
  python3 bin/spark --cluster-config config/cluster.json build check
  python3 bin/spark --cluster-config config/cluster.json build image --apply --tag spark-ds41f:x1126
'

# 每轮 stop -> start
ssh chan@192.168.2.180 'cd /opt/spark && ./deploy.sh stop && ./deploy.sh start --target=deepseek'

# decode + prefill（注意 URL 是 8888，且 prefill 必须显式 --suites）
ssh chan@192.168.2.180 '
  cd /opt/spark/deepseek
  SPARK_DS41F_API_KEY=deepseek SPARK_DEPLOYMENT_COMMIT=bbbebe65+staged-1126 \
  python3 bin/spark --cluster-config experiments/2026-11-26-x1126/tp4-ring.json \
    bench --url http://127.0.0.1:8888 --suites quality,decode,prefill \
    --concurrency 1,2,4 --decode-cases prose,code \
    --prefill-text source \
    --prefill-sizes 32768,131072,262144,524288,1000000 \
    --prefill-repeats 3 \
    --compare manifests/benchmarks/2026-11-26-spark-ds41f-1126-tp4.json \
    --tolerance 1 --output results/private/bench/x1126-b2
'
```

`doctor --live` 和 `bench` 需要 `git ls-files`；源码镜像是无 `.git` 的
同步树，诊断时需临时建立 index/snapshot，诊断后删除。

## 本地回归

- deepseek target 合同：355 tests OK。
- GLP/KV 定向：21 tests OK。
- 父级 deepseek env 合同：2 passed（已把旧测试的 GLP-39 硬编码改为动态读取
  recipe GLP-27，并把 4/4474321920/16360/24 更新为 8/8076928000/16336/48）。
- `sync_to.sh` 安全合同：使用无 inkling 的临时部署配置时 18 passed。
  用户未提交的 `inkling` 是半成品且不可部署；直接在本地 WIP 配置上跑时
  4 个 sync 测试会因“enabled engine 无源码”变红，属于预期边界，不影响
  本次 deepseek 同步。
- `git diff --check` 在父仓与 deepseek 子模块均通过。

## 证据文件

- `deployment-record.json`
- `performance/summary.json`
- `performance/run-01/bench.json`、`performance/run-01/prefill-bench.json`
- `performance/run-02/bench.json`
- `performance/filler-prefill-bench.json`
- `performance/control-c32-prefill-bench.json`
- `validation/doctor-live.txt`
- `validation/tool-gate.json`、`validation/tool-gate.txt`
- `validation/determinism-wide.jsonl`
- `validation/final-runtime-consumption.txt`
- `validation/final-startup-keylines.txt`
