# spark-ds41f 原版独立 A/B 与 target 最终验收

## 结论

- 原版源码 `spark/docs/spark-ds41f@bbfe2618` 在四机 `/24` 环网、独立镜像
  `spark-ds41f:x1112-original` 上完成构建、分发、启动和性能复测。
- prefill 的最终 target 已达标：frozen prefill profile + NVMe 官方调优后，
  32K / 131K / 262K / 524K / 1M 为
  `8552.5 / 8689.0 / 8319.6 / 7502.8 / 5984.1 tok/s`，相对官方
  `8508 / 8657 / 8261 / 7460 / 6012` 为
  `+0.52% / +0.37% / +0.71% / +0.57% / -0.46%`。
- decode 未达到官方 README 的 `c1 82.6` / prose `77.8 tok/s`。原版源码
  在同一宿主上的最佳 c1 为 `78.74`、prose `74.5`；target 最终为
  `78.38`、prose `73.9`。target 相对原版基线仅 `-0.46%`，说明当前剩余
  约 4% 的差距来自宿主/参考运行边界，不是 `spark/deepseek` 的代码差异。
- 不做“单 rank 硬件掉队”判断；四机 image、layers、二进制、argv、env
  和 ring 数据面均一致。

## 身份与证据

| 项 | 值 |
| --- | --- |
| 原版源码 | `bbfe2618efbd3e2143363eb9262d1c7fb89e5460` |
| 原版镜像 | `spark-ds41f:x1112-original` |
| 原版 image ID | `sha256:132322fe63e099c87d44be0ab0742bcc8204b06250720de77ab4650fd909dadb` |
| 原版层数 / 大小 | `57` / `23943304656` bytes |
| base digest | `sha256:3cf14c52765a0f0c2f09035e05fa79e1e4b2f9788b64afa90fa41c1ff12d29cf` |
| target 镜像 | `spark-ds41f:x1112` |
| target image ID | `sha256:cbf38e34dab0e2aafa2abea79e669469027d253c70248d4e9a54182878eb2ece` |
| target source | `spark/deepseek@7f61d7a0`，merge 了 `offical/main@bbfe2618` |

四机原版镜像按高速环网 `a -> b`、`a -> d`、`b -> c` 分发；四机 image ID
和 57 层 RootFS 完全一致。原版 live contract 比较了四机实际 `docker
inspect` 与 `bin/spark render`：85 个环境键、serve argv、mounts、
`running / OOM=false / restart=0` 全通过。target live contract 比较
86 个环境键和完整 argv，也全通过。

原版启动日志证明：

- `Using V2 Model Runner`
- L2 prefetch `grid=2 block=128 chunk=4096`
- GLP `/glp/vector.gguf` 39 directions
- KV cache `11,547,894` tokens，1M 并发 `11.01x`

## 测试口径修正

官方 README 的 prose `77.8 tok/s` 不是此前 176 条子集，而是官方
`render_docs._tps_by_style()` 对默认 256 条 request table 中
“除 Python、SQL、JSON 之外的 13 类 prose”的聚合值。独立复核得到
官方 c1 原始记录为 `prose 77.8 / code 113.6`。

- 176 条子集仅作辅助记录，不作为官方 prose gate。
- 官方 prose 分类为 208 条；短窗口使用
  `/opt/abtest-inputs/prose-prompts-official.jsonl`，digest
  `193000ee804f4485b62e01e234dc701a9fbfd6eb72fa8ab448d8ede161331515`。
- 与官方 `77.8` 最接近的同口径测试为默认 256 条、60 s warmup +
  300 s、10 windows、thinking=false，再用官方
  `_tps_by_style()` 抽取 prose。
- 日常 c1/c2/c4 仍按用户要求使用 10 s warmup + 30 s + 3 windows，
  两轮均值。

## 原版基线

独立原版镜像先用 WiFi 控制面、官方 runtime 参数启动：

| 测试 | 结果 |
| --- | ---: |
| c1/c2/c4 176 条短窗口均值 | `72.33 / 106.60 / 147.13 tok/s` |
| 默认 256 条长窗口 c1 | `78.41 tok/s` |
| 默认长窗口 prose | `74.2 tok/s` |
| prefill 32K / 131K / 262K / 524K / 1M | `7901 / 8126 / 7768 / 7050 / 5706 tok/s` |

这说明原版源码本身也低于官方 decode 约 4%，而 prefill 低约 5–7%。

## 宿主 A/B

### WiFi 控制面的尾延迟

`ping` 四机 WiFi 控制面出现 `80–100 ms` 尾延迟。为验证控制面是否为
decode 主因，临时在物理环网上建立 GRE 全互通控制 overlay：

- `ctrl0` overlay 地址 `10.200.0.1..4/32`
- `GLOO/TP/NCCL_SOCKET_IFNAME=ctrl0`
- 不改变 HCA、GID index、RoCE MTU、NCCL_IB_HCA 或 `oneshot-ring4` 数据面
- overlay RTT 稳定在约 `0.5–3 ms`

结果：长窗口 c1 `78.41 -> 78.72`、prose `74.2 -> 74.5`，约 `+0.4%`。
控制面确实是官方 doctor 的非阻塞差异，但不是剩余 ~4% decode 差距的主因。
测试后已删除 overlay，恢复 target 的 WiFi 控制面和 `/30` 共享 mesh。

### NVMe 官方调优

四机应用官方 doctor 的宿主调优：

- `nvidia-nvme-interrupt-coalescing.service`：`masked / inactive`
- NVMe interrupt coalescing feature `0x08 = 00000000`
- `nvme_core.apst_primary_timeout_ms=2000`
- `nvme_core.apst_secondary_timeout_ms=60000`
- 重写各控制器 `power/pm_qos_latency_tolerance_us` 以重新 arm APST
- `/etc/modprobe.d/nvme-apst.conf` 已写入四机并执行
  `update-initramfs -u`；initramfs 仅报告既有的缺失 Plymouth 主题警告

32K prefill：`7901 -> 8153 tok/s`（`+3.2%`）。decode 几乎不变，
最终原版长窗口 c1 `78.74`、prose `74.5`。

另用 `nvidia-smi -lgc 2593,3003` 把负载 SM 锁到约 `2574 MHz` 复测
prose 短窗口 c1，step 仅从约 `34.30` 到 `34.20 steps/s`，吞吐
`70.37 tok/s`，仍在噪声内；随后执行 `-rgc` 恢复默认 boost。

## Target 最终验收

每次正式启动前都执行 `deploy.sh stop`，确认四机容器/GPU compute 为 0，
再 `deploy.sh start --target=deepseek`。

### Prefill

| 大小 | 上一轮 tok/s | 最终轮 tok/s | 两轮均值 | 官方 | 均值 delta |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 32K | 8,578.9 | 8,552.5 | 8,565.7 | 8,508 | +0.68% |
| 131K | 8,736.5 | 8,689.0 | 8,712.7 | 8,657 | +0.64% |
| 262K | 8,341.1 | 8,319.6 | 8,330.4 | 8,261 | +0.84% |
| 524K | 7,511.5 | 7,502.8 | 7,507.1 | 7,460 | +0.63% |
| 1M | 5,993.1 | 5,984.1 | 5,988.6 | 6,012 | -0.39% |

5 档两轮均值均在允许的微小负浮动内，prefill gate 通过；每轮
3 repeats 全部 `failed=0`。

### Decode

| 口径 | target | 官方/原版基线 | 结论 |
| --- | ---: | ---: | --- |
| 默认 256 条长窗口 c1 | 78.38 | 官方 82.62（-5.14%） | 未达官方 |
| 默认长窗口 prose | 73.9 | 官方 77.8（-5.01%） | 未达官方 |
| 原版同宿主最佳 c1 | 78.74 | target -0.46% | 无 target 专属回退 |
| c1/c2/c4 官方 prose 短窗口两轮均值 | 70.83 / 105.38 / 144.50 | 原版 70.78 / 104.63 / 144.60 | 与原版一致 |

target 与独立原版在相同 host、相同模型、相同长度下的
`tokens/step` 均约 `2.37`；差异集中在 engine steps/s（约 `33.1–33.3`
对官方 `34.72`）。这更像该四机宿主的 collective/engine step 边界，
而不是 prompt acceptance、参数漏传或 target 代码差异。

## 最终运行态

- 四机 `deepseek-r0..3`：`running`、`OOMKilled=false`、`RestartCount=0`
- steady `memguard.sh`：四机存活
- 认证 API `/v1/models`：200
- 四机 image ID / layers / 六个 source labels / 四个关键二进制 hash 一致
- NCCL version code `23007`
- `/30` 环网、9000 MTU、RoCE MTU 4096、GID 3、active mesh marker、
  两条硬件 offload TC 规则、probe `.text` hash：`audit_host_ring.py ok=true`
- GPU 负载下 SM 约 `2411–2593 MHz`，1,048 个负载采样均值 `2531.5 MHz`，
  最高 77 C，util 最高 96%

## 复现入口

- `prepare_abtest_profile.py --control wifi|overlay`
- `control_overlay.py apply|ping|delete`
- `run_original_acceptance.sh`
- `verify_original_live_contract.py`
- `audit_original_container_binaries.py`
- `build_official_prose_prompts.py`

原始数据在 `remote/abtest-pre/`、`remote/abtest-results/` 和
`remote/target-final/`。`image-transfer/identity.txt` 记录四机 57 层
逐层 digest。

## 未闭环项

- decode 官方 gate 仍未通过，且原版源码同样未通过；不能把它归因于
  `spark/deepseek` 改造。
- 官方 doctor 的 WiFi 控制面 warning 仍存在；临时 overlay 仅带来约
  0.4% 收益，最终 target 未保留 overlay。
- 官方 doctor 的 fan-control DKMS、UEFI boot order、kernel package
  版本等非 serving 阻塞项未在本次改动中处理。
- 下一步若继续追 decode，应针对 engine step/collective 延迟做官方
  reference image 同宿主 A/B 或更细的 ring data-path 计时；不再做
  单 rank 硬件掉队猜测。

## 2026-11-13 官方性能树与 clock cap 后续

- 已按 official suite 的 vLLM tree `5e888b15` / patch head
  `4652b632` 独立重建性能臂，image labels 与 base digest 全一致；
  同协议默认长窗口 c1 两轮 `78.28/78.30`（均值 `78.29`），仍未
  复现官方 `82.62`。
- 同协议 collective probe 四机通过；当前环网 24 shapes 几何平均
  比官方慢约 `15.4%`，证明剩余 decode 缺口是宿主/环网边界，不是
  target 源码或参数错位。
- 四机 firmware 已统一到 `28.50.1002`；固件统一本身无性能收益。
  2593/3003 MHz clock 试验均未复现收益，已删除 drop-in 并恢复
  sparkDash 原始 2200 MHz。最终 target 208-prompt prose
  c1/c2/c4 两轮均值为 `69.53/103.57/142.17`；官方 long-window
  c1 overall `76.97`、prose `72.6`，低于 `77.8` 约 `6.68%`。
- 最终 2200 MHz 下 prefill 两轮均值
  `8565.7/8712.7/8330.4/7507.1/5988.6 tok/s`，全部达标。
- 完整记录与持久化 drop-in：
  [`2026-11-13-clock-cap-decode`](../2026-11-13-clock-cap-decode/)；
  final target 原始数据：
  `remote/target-final-20261113/`。
