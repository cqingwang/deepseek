# 2026-11-23 `spark-ds41f:x1123` / official 1122

## 结论

> 2026-11-23 用户决定以当前最佳配方收尾。最终状态为
> `closed-current-best-prefill-hold`：decode 全部达标，source-text prefill
> 仍保留 `1.27%–4.79%` 已知负偏差；不再继续调时钟、batch/chunk 或控制面。

- 本地站点已合入 `offical/main@945d95d0`（`spark-ds41f-1122`），保留 Bearer
  API 鉴权、`name_pattern`、非 Git 部署身份和 SSH 管理地址派生等站点修复。
- 镜像内的 vLLM 为 730 项 patch、patch head
  `eccd5c1065a8b73d20ce537e57899062719c8818`、tree
  `8e0683a9276389957e49b7a26bd2bd062d788558`；CUDA 13.5.37。
- 最终配置只把 `common.max_request` 从 6 改为 8；固定 KV
  `8,076,928,000` bytes，启动日志为 `7,991,241` tokens / `7.52 GiB` /
  `7.99x`。其余业务参数未变。
- 四机镜像 ID 同为
  `sha256:5c50e8419ae6a6746ce7f78249ddd7c675c90a11a99f37003c1aba5f32228e33`；
  旧 x1123 镜像保留为 `spark-ds41f:x1123-1120`。
- 参数合同 RED 5 项后 GREEN 17 项；最终 live contract 91 个固定环境键、
  78 项 serve 参数、8 并发、1M、16,384 batch、67 图、5 drafts 均由
  `docker inspect` 和 vLLM 启动日志证明。
- API smoke 7/7，通过；并发结构化输出 24/24，通过。

## 硬件与宿主对齐

| 项 | 最终值 |
| --- | --- |
| Kernel | `7.0.0-1019-nvidia-64k` |
| Page size | 65,536 bytes |
| Driver | `580.178.04` |
| Container CUDA | `13.5.37` |
| Memory saver | `dgx-spark-memory-saver/0.4.1` |
| UVM packed leaf tables | `Y` |
| CPU cpuset governor | `performance` |
| NVMe APST / coalescing | `2000 ms` / `00000000` |
| GPU clock cap service | 已 stop + disable，备份于 `/var/tmp` |
| 负载 GPU clock / power | 约 `2470–2554 MHz` / `64–75 W` |

重启前四机最大空闲内存块占比仅 `1.9%–11.8%`；重启后恢复到 `99%+`。
同时移除 SparkDash 遗留的 `spark-gpu-clock-limit.service`，其原配置把
GPU 锁在 `0,2200 MHz`。

## 性能

验收口径为当前 64 KiB/1120：decode c1 使用 README，c2/c4 使用同代
1120 manifest；prefill 使用 README 的 source-text 口径，公差 1%。

两轮 decode 均值：

| 指标 | 两轮均值 | 当前参考 | 结果 |
| --- | ---: | ---: | --- |
| prose c1 | 91.993 | 89.1 | PASS |
| prose c2 | 162.925 | 150.394 | PASS |
| prose c4 | 292.220 | 266.942 | PASS |
| code c1 | 135.261 | 105.117 | PASS |
| code c2 | 193.488 | 186.001 | PASS |
| code c4 | 398.786 | 329.017 | PASS |

两轮 source-text prefill 均值：

| 长度 | 两轮均值 | 当前 README | 偏差 | 结果 |
| ---: | ---: | ---: | ---: | --- |
| 32K | 9,154.149 | 9,615 | -4.79% | HOLD |
| 131K | 9,680.065 | 10,108 | -4.23% | HOLD |
| 262K | 9,621.975 | 10,056 | -4.32% | HOLD |
| 524K | 9,597.212 | 9,833 | -2.40% | HOLD |
| 1M | 9,277.523 | 9,397 | -1.27% | HOLD |

时钟帽移除后，prefill 从
`7,820.7 / 9,049.8 / 9,181.4 / 9,027.3 / 8,744.6` 提升到
`9,154.1 / 9,680.1 / 9,622.0 / 9,597.2 / 9,277.5 tok/s`。
将资源面临时改为官方 `32/16.5 GiB` 没有改善，说明剩余差距不是 8 并发
或固定 KV 申请导致的。

最新 1122 manifest 本身使用 filler prefill；同一解锁时钟状态下的 filler
诊断为 `9,583 / 10,144 / 10,131 / 9,979 / 9,575 tok/s`，仍低于该
manifest 约 `3%–6%`。因此本轮不能把 source-text 或 filler 任一口径写成
完整达标。

## 证据

- `deployment-record.json`
- `build/prepare-check.txt`、`build/image.txt`
- `validation/contract-red.txt`、`validation/contract-green.txt`
- `validation/archive-old-image.txt`
- `validation/distribute-a-b.txt`、`validation/distribute-b-c.txt`、`validation/distribute-c-d.txt`
- `validation/live-parameter-consumption-final.json`
- `validation/final-health.json`
- `validation/api-smoke-final.json`、`validation/concurrent-structured-smoke.json`
- `validation/post-reboot-host-state.txt`、`validation/clock-cap-removed.txt`
- `validation/clock-power-clock-cap-removed.txt`
- `performance/summary-final-clock.json`
- `performance/run-01-final-clock-bench.json`、`performance/run-02-final-clock-bench.json`
- `performance/resource-test-post-clock-prefill-bench.json`
- `performance/filler-clock-final-bench.json`

## 未闭环

- source-text prefill 仍有 `1.27%–4.79%` 负偏差，按 1% 门禁未通过。
- 容器参数、镜像 tree、CUDA/kernel/page-size、时钟帽和宿主调优均已对齐，
  且官方资源面 A/B 未改善；剩余差距只能继续在 host clock lock、
  prefill chunk/batch 或控制面 A/B 中寻找，不能靠改业务参数或放宽门禁。
- 用户已明确要求以当前最佳配方收尾，因此上述三项保留为后续实验入口，
  本轮不继续实施。
