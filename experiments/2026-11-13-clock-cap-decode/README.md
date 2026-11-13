# 2026-11-13：官方源码 A/B、`/30` mesh 与运行时参数拒绝记录

## 结论

- 用 official 渠道 `8b193039` 重建的 1107 性能臂
  （vLLM tree `5e888b15`、patch head `4652b632`、image
  `spark-ds41f:x1113-1107perf2`）在当前四机、官方参数下，默认
  256 prompts 长窗口 c1 两轮为 `78.28 / 78.30 tok/s`，均值
  `78.29`；官方 README 记录为 `82.62`，差 `-5.25%`。
- 该重建镜像的 image labels 与官方 suite 的 vLLM/TileLang/
  sparknet/NCCL/tile_kernels/base digest 完全一致，四机 image ID
  同为 `sha256:7b8657f0...25eb4`，live contract 85 env、完整 argv、
  OOM/restart 全通过。当前 decode 缺口不能归因于
  `spark/deepseek` merge 或漏参。
- 同协议 collective probe 四机全部通过，但当前环网 24 个 shape 的
  几何平均比官方 1107 probe 慢约 `15.4%`；恢复 `/30 +
  spark-ring-mesh` 后复测仍约 `15%`，差距集中在 all-reduce/
  all-gather 的小消息与聚合延迟。engine step 与官方约
  `33.2 vs 34.7`，`tokens/step` 约 `2.37 vs 2.38`，prompt
  acceptance 不是主因。
- GPU clock 试验全部拒绝：直接 `-lgc 0,2593` 曾得到短窗口 c1
  `71.33`，但正常 stop/start 后变为 `70.43/70.57`；最终长窗口
  prose `73.4`，还低于 2200 的 `73.6`。最终恢复 sparkDash 原始
  `0,2200 MHz`，不保留 drop-in。
- target 当前 8/8 channels、8 MiB、CUDA 64 也优于官方 4/4
  channels、2 MiB、CUDA 32 的短窗口 c1：`70.08` 对 `70.63`；
  该 4/4 profile 已拒绝。
- 另把 `NCCL_IB_QPS_PER_CONNECTION` 从 2 改为 1，c1 两轮
  `69.67/69.97`（均值 69.82）；与最终 QPS=2 的 `69.73` 只差
  0.12%，且不是同一 boot 的 ABBA，不能认定为收益，因此恢复 QPS=2。
- 最终 2200 MHz、8/8 profile 的当前 boot 两轮 208-prompt prose
  为 c1/c2/c4 `69.53/103.57/142.17 tok/s`。官方同口径长窗口
  c1 overall `76.97`，`_tps_by_style()` prose `72.6`、code
  `105.7`；相对官方 prose `77.8` 低 `6.68%`。boot-to-boot
  仍有约 0.5–1% 波动，最终结论采用当前最后 boot。
- 最终 2200 MHz 下 prefill 两轮均值仍达标：
  `8565.7 / 8712.7 / 8330.4 / 7507.1 / 5988.6 tok/s`，相对官方
  `+0.68% / +0.64% / +0.84% / +0.63% / -0.39%`。

## 宿主与版本

- 四机 ConnectX-7 firmware 原为 A/B `28.50.1002`、C/D
  `28.45.4028`；C/D 已用官方 `fw-ConnectX7.bin`
  `sha256:5e0ed955...ffa9b6` 升级并 verify 为 `28.50.1002`。
  固件统一本身没有降低 collective 差距，不能把它写成性能修复。
- 四机 `vm.watermark_boost_factor=0`、
  `nvme_core.apst_primary_timeout_ms=2000`、
  `apst_secondary_timeout_ms=60000`、NVMe coalescing `00000000`、
  NVMe service masked 均在最终检查中成立。
- 官方 main 已从 `bbfe2618` 更新到 `2b9a6fb6`；新增内容主要是
  candidate-55 记录、GLP Spec v3 候选 patch 和
  `spark_ds41f/candidate.py`。没有改动已部署 runtime tree
  （vLLM/TileLang/sparknet/NCCL source lock），所以未触发镜像重建。
- 当前 target 镜像仍为
  `spark-ds41f:x1112` /
  `sha256:cbf38e34dab0e2aafa2abea79e669469027d253c70248d4e9a54182878eb2ece`。

## 最终宿主策略

- `spark-gpu-clock-limit.service` 保留 sparkDash 原始的
  `0,2200 MHz`，四机 marker 均为 `2200 <boot-id>`。
- 2593/3003 的 systemd drop-in 已删除，不保留未证明收益的宿主改动。
- `/30`、mesh service、NVMe APST/coalescing 和 watermark 仍按官方
  要求保持。

## 证据

- 官方性能树 c1、live contract、probe、宿主对比：
  `spark/deepseek/experiments/2026-11-12-spark-ds41f-original-abtest/remote/1107perf2/`
- final target 的 208-prompt 短窗口两轮、默认长 c1、
  `_tps_by_style()` prose、prefill、clock/data 汇总：
  `spark/deepseek/experiments/2026-11-12-spark-ds41f-original-abtest/remote/target-final-20261113/`
- `performance-summary.json` 保存最终数值和比较口径。

## 未闭环

- 官方 prose long-window 仍差 `6.68%`；当前证据只支持“代码/参数/
  镜像身份已对齐，剩余差距由当前站点环网 collective 与平台边界造成”，
  不支持宣称 decode 达标。
- C/D 的 DGX 软件基线仍比 A/B 老；未做 OS 重装或 AC 断电冷启动，
  不做单 rank 硬件掉队判断。
