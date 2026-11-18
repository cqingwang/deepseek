# 2026-11-18 `spark-ds41f:x1118`

## 目标

- 以本地 `spark/` 与 `spark/deepseek` 为源，通过 rsync 同步到四机
  `/opt/spark`，在 spark-a 构建官方 candidate-61 二进制镜像
  `spark-ds41f:x1118`，再按管理可达顺序分发同一 image ID。
- 保持 `spark/config.yaml.common.ring` 为四机 A→B→C→D 双平面 `/30`
  环网的唯一拓扑源；不改共享 `spark-ring-mesh` 或其他 target 的持久资产。
- 每次 live 验证先 `deploy.sh stop`、确认零容器/零 GPU compute，再
  `deploy.sh start --target=deepseek`。

## 源码身份

- vLLM：`19f2c20ed4d64ae22bd009ceacadd85e2a81e354` + candidate-61
  `0811..0822` 系列，tree `70cdcaeb7813998d6f5f8972a4219a5e1e48f7d5`。
- candidate-61 锁：`experiments/2026-11-16-c61-decode/upstreams.lock.json`。
- site profile：`experiments/2026-11-18-x1118/tp4-ring.json`。

## 参数合同

- 固定容器环境：candidate-61 的 86 个上游键；无旧
  `NCCL_IB_QPS_PER_CONNECTION` 站点覆盖。
- 最终容器环境：86 个固定键 + 4 个逐 rank 环网/地址键 = 90。
- serve 参数：上游 77 项 + official main 的
  `--enable-per-request-metrics` = 78 项。
- `common.max_request=16`、`max_token=1,048,576`；
  `engines.deepseek.kv_cache_memory_bytes=17,716,740,096` 显式锁定
  candidate-61 的官方 KV 池，launcher 校验后原值输出。
- 每 rank 必须从 `docker inspect` 与 vLLM 启动日志证明
  `max_num_seqs=16`、`max_model_len=1,048,576`、
  `max_num_batched_tokens=16,384`、`max_new_tokens=65,536`、
  `num_spec_tokens=5`、`master_port=25,000`。

## 验证入口

```bash
# 静态 RED/GREEN
python3 -m pytest -q spark/tests/test_deepseek_env_merge_contract.py
python3 -m pytest -q spark/tests/test_deepseek_x1118_live_evidence.py

# live 参数消费
python3 experiments/2026-11-18-x1118/collect_live.py \
  --output experiments/2026-11-18-x1118/validation/live-parameter-consumption.json

# decode c1/c2/c4 与 source prefill 两轮（在 /opt/spark/deepseek 执行）
export SPARK_DEPLOYMENT_COMMIT=rsync-2f5b4b72ee086fdeb94db5ccbf04ac98
export SPARK_DS41F_API_KEY=deepseek
./bin/spark \
  --cluster-config experiments/2026-11-18-x1118/tp4-ring.json bench \
  --url http://192.168.2.180:8888 \
  --suites quality,decode,prefill \
  --decode-cases prose-nothink,code-nothink \
  --concurrency 1,2,4 --min-samples 3 --max-samples 3 \
  --prefill-text source \
  --prefill-sizes 32768,131072,262144,524288,1000000 \
  --prefill-repeats 2 --compare none --allow-mismatch \
  --output /tmp/x1118-bench-run-NN
```

## 结果

- 构建：`spark-ds41f:x1118` =
  `sha256:4f50c09aeb8d2bbc722e72c174ab3a7ed6591b4412a8a38ebfa8a27a9ebbb7da`，
  621 秒，8 compile jobs，最低 `MemAvailable=77.5 GiB`，GPU import smoke 通过。
- 分发：A→B→C→D 光纤直连 `docker save | docker load`，四机 image ID、56 层
  RootFS 与 candidate-61 vLLM tree 完全一致。
- live：四机 90 个受管注入键，78 个 serve flag；head 启动日志
  `GPU KV cache size: 18,702,259 tokens`（17.84 个 1,048,576-token 上下文），
  worker 均报告 `kv_cache=16.5 GiB`。
- 环网：host ring audit `ok=true`；每次真实推理后四机各 8/8 个 RoCE
  收发方向计数正增长。共享 `spark-ring-mesh` 未改动。
- decode 两次均值（aggregate tok/s）：

| Case | Run 1 | Run 2 | Mean | Official README | Verdict |
| --- | ---: | ---: | ---: | ---: | --- |
| prose c1 | 90.884 | 89.551 | **90.218** | 59.1 | PASS |
| prose c2 | 150.353 | 156.934 | **153.644** | 84.8 | PASS |
| prose c4 | 242.447 | 261.728 | **252.088** | 124.9 | PASS |
| code c1 | 135.286 | 133.097 | **134.192** | 80.6 | PASS |
| code c2 | 193.603 | 190.195 | **191.899** | 118.2 | PASS |
| code c4 | 377.560 | 361.764 | **369.662** | 165.1 | PASS |

- source prefill 两次均值（tok/s）：

| Tokens | Run 1 | Run 2 | Mean | Official README | Verdict |
| ---: | ---: | ---: | ---: | ---: | --- |
| 32K | 9,254.505 | 9,428.979 | **9,341.742** | 8,508 | PASS |
| 131K | 9,769.266 | 9,776.876 | **9,773.071** | 8,657 | PASS |
| 262K | 9,789.505 | 9,784.651 | **9,787.078** | 8,261 | PASS |
| 524K | 9,560.172 | 9,557.569 | **9,558.871** | 7,460 | PASS |
| 1M | 9,172.131 | 9,190.911 | **9,181.521** | 6,012 | PASS |

- 原始结果：`performance/run-01/bench.json`、`performance/run-02/bench.json`；
  汇总：`performance/summary.json`。两个 boot 的 quality gate 均为 5/5。

## 已知官方功能缺口

- API smoke 的普通 chat、默认 thinking-off、文本 image token、function
  call/output、真实图片块均 200；`--enable-per-request-metrics` 的响应
  `metrics` 字段可见。
- candidate-61 官方代码仍对 Responses `custom_tool_call` /
  `custom_tool_call_output` 返回 400 `Unsupported input item type`。旧站点
  x1114 曾带本地兼容补丁；本次按“对齐官方 main”要求未擅自重新引入，
  原始响应保存在 `validation/api-smoke.json`。这是相对旧站点镜像的功能回退。
