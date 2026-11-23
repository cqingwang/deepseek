# 2026-11-23 `spark-ds41f:x1123`

> 验收口径更正：本记录最初的性能判定误用了历史 4 KiB r5l 门限，
> 不能代表当前 64 KiB/1120 或 1122。原始运行数据保留，但性能结论已由
> [`ACCEPTANCE-CORRECTION.json`](ACCEPTANCE-CORRECTION.json) 标记为
> `superseded-invalid-baseline`。后续以
> `2026-11-23-x1123-official-1122` 按当前官方 manifest 重新验收。

## 范围

- 来源：本地 `spark/deepseek`，以 `offical/main@1b4b54f2` 合入后的
  candidate-62 / spark-ds41f-1120 代码面为基线。
- vLLM：`19f2c20e` + `702` 项 patched series，目标 tree
  `8fc99ca7546c5c538f0fe64e7c82954f1d2d7ae0`；官方已覆盖站点
  x1119 的 Responses custom-tool 与 structured-output bitmask 修复，
  本次不重复叠加旧补丁。
- 其他上游：NCCL tree `6af10aa7`、TileLang tree `741806c7`、
  TileKernels tree `cb2fa41c`、SparkNet tree `e06f6a06`，与官方
  1120 一致。
- 这是 vLLM 二进制变化，必须重建镜像；仅修改 `max_request`、
  KV 申请或 launcher 映射时不重建镜像。

## 站点运行面

- `config.yaml` 只改变 `common.max_request: 8`；其余业务参数保持
  `max_token=1000000`、`max_tokens=65536`、
  `max_num_batched_tokens=16384`、67 图、5 drafts、thinking=false、
  reasoning_effort=75、master_port=25000。
- 按官方 1120 的 DS4.1 KV cache 几何，8 个 1M 请求需要
  `8,076,928,000` bytes；上游 16.5 GiB 池仍作为硬上限，但本次只
  固定申请 8 请求所需的块数，剩余部分交还系统。
- 派生 vLLM argv：`--max-num-seqs 8`、
  `--max-model-len 1000000`、
  `--long-prefill-token-threshold 16336`、
  `--max-cudagraph-capture-size 48`，
  capture ladder 到 48 行。
- 四节点 A→B→C→D、双平面 `/30`、HCA/GID/MTU 与共享
  `spark-ring-mesh` 仍然只由父级 `config.yaml.common.ring` 派生。

## 门禁

1. 合同测试先在 x1119/5 配置上 RED，再在本 profile、8 并发与
   新 launcher 上 GREEN。
2. spark-a 构建 x1123 后，四机 image ID、五棵树 labels 一致。
3. 每次 live 验证前 `deploy.sh stop`，确认无 target 容器和 GPU
   compute，再 `deploy.sh start --target=deepseek`。
4. inspect 与启动日志证明 8/1,000,000/16,384/8,076,928,000/16,336/48
   均被实际消费。
5. API、结构化输出、内存守卫与两轮 decode/prefill 达到官方 README
   门限后，才将本记录判定为 ACCEPTED。

## 当前进度

- [x] 计算 8 并发固定 KV 与派生 runtime。
- [x] 建立 x1123 site profile 与合同测试。
- [x] RED/GREEN、镜像构建与四机分发。
- [x] 两次独立 stop/start、live 参数消费、API 与性能验收。
- [x] 部署记录、工程记忆与回滚说明收口。

## 执行结果

- 合同 RED：旧 x1119 配置在 `ds41f_config_file` 断言处失败，保存为
  `validation/contract-red.txt`；修复后父级合同 `17 passed`，target
  source/bench/Sonar/KV `60 tests OK`，x1123 acceptance `5 passed`。
- 镜像：`spark-ds41f:x1123` =
  `sha256:63f2e77085f018df2099f8fbb118beaf87f0e36069f2c64e5687a302f6a0092f`，
  构建 `685s`，8 compile jobs，最低 `MemAvailable=81.7 GiB`。五棵树、
  CUDA 13.5.37 和 vLLM patch head 均与官方 1120 一致。
- 镜像内官方 0823–0829 定向测试：`76 passed, 36 skipped`，含
  structured-output、Responses CustomTool、SSE keep-alive、DSML parser、
  quant ignored-layer 和默认关闭 LSA。旧站点
  `test_copy_draft_bitmask_capacity.py` 因官方接口演进而失真，已由官方
  `test_copy_draft_bitmask_rows.py` 取代并移除，RED 证据保存在
  `validation/site-obsolete-test-red.txt`。
- 分发：按 A→B `10.251.20.2`、B→C `10.251.20.6`、
  C→D `10.251.20.10` 执行 `docker save | ssh ... docker load`，四机
  image ID 一致。
- 两次部署均为 `deploy.sh stop` → 四机零容器/零 GPU compute →
  内存整理 → `deploy.sh start --target=deepseek`。第一次和第二次 boot
  的 live contract 均 `ok=true`；第二轮后最终四容器 running、
  OOMKilled=false、RestartCount=0、memguard active、共享
  `spark-ring-mesh.service` active 且 marker 正常。
- 实际 KV 启动行：`7,991,241 tokens`、`7.52 GiB`、
  `7.99x` 1M 并发。对应固定申请 `8,076,928,000` bytes；相对官方
  1120 的 `17,716,740,096` bytes 上限，每个 rank 向系统交还
  `9,639,812,096` bytes。
- copy-draft 运行时把 graph envelope 从 48 行安全扩到 59 行，
  batched-token envelope 为 16,395；这是 vLLM 的实际消费值，不改变
  config 中的 48/16,384 业务基线。
- API smoke 7/7 为 200；并发结构化输出 3 路 × 8 波共 24/24 成功。
  两轮 bench quality 均 5/5。

| 性能项 | Run 1 | Run 2 | 两轮均值 | 官方门限 |
| --- | ---: | ---: | ---: | ---: |
| prose c1 | 89.233 | 90.321 | **89.777** | 59.1 |
| prose c2 | 157.671 | 158.969 | **158.320** | 84.8 |
| prose c4 | 282.366 | 283.997 | **283.182** | 124.9 |
| code c1 | 134.696 | 133.903 | **134.300** | 80.6 |
| code c2 | 190.202 | 192.387 | **191.295** | 118.2 |
| code c4 | 371.936 | 358.227 | **365.082** | 165.1 |
| prefill 32K | 8844.492 | 8909.710 | **8877.101** | 8508 |
| prefill 131K | 9234.571 | 9259.175 | **9246.873** | 8657 |
| prefill 262K | 9227.545 | 9201.658 | **9214.602** | 8261 |
| prefill 524K | 9042.079 | 9022.977 | **9032.528** | 7460 |
| prefill 1M | 8677.627 | 8660.590 | **8669.109** | 6012 |

## 证据

- `build/`：prepare/check/image 日志、image inspect、labels、构建内存监控。
- `validation/`：合同 RED/GREEN、两轮 stop/start、live contract、
  API、并发结构化输出、最终健康和共享环网证据。
- `performance/run-01/bench.json`、`run-02/bench.json` 与
  `performance/summary.json`：全部原始数据和门禁汇总。
- `deployment-record.json` / `source.json`：可用于复现的配置变化、
  image ID、容量、分发路径、验收与回滚。
- 回滚：`spark-ds41f:x1119` 已在四机保留；回滚只需恢复
  `experiments/2026-11-19-x1119/tp4-ring.json` 与对应 config/image ID，
  不需要重新构建镜像。
