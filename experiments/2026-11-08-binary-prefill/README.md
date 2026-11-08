# DeepSeek TP4 镜像内 prefill 优化（第二轮）

> 后续状态（2026-11-08）：本记录中的
> `spark-ds41f:binary-prefill-20261108-03` 已被官方 1107 重建镜像取代。
> 经四机确认无容器引用后已删除该镜像 tag；本目录和 `frozen-best/` 继续作为
> 源码、构建和实验 provenance 保留，不得把已删除镜像当作当前可启动依赖。

## 结论

本轮以冻结的 D10 运行态为起点，在 spark-a 构建并沿物理环分发薄 layer
candidate 镜像，验证镜像内 Python/TileLang prefill 调度改动。最终保留：

- `SPARK_DS41F_PREFILL_BLOCK_M=160`
- `SPARK_DS41F_MOE_SLICE_TOKENS=4096`
- `SPARK_DS41F_WO_SLICE_TOKENS=2048`
- 运行时由源码根据 `block_M>=128` 自动使用 prefill `num_stages=2`
- 镜像 `spark-ds41f:binary-prefill-20261108-03`
- image ID
  `sha256:af78e1ad16469fd47e898077144b88df094e6caec46818b71a505dd5e5620635`

最佳测量是 `run-09-block160-stages2` 的 first run：
六档均值 `6,954.15 tok/s`，相对 D10 `6,776.66 tok/s` 提升
`+177.49 tok/s（+2.62%）`。最终 clean boot `run-10`：
`6,912.95 tok/s`，相对 D10 `+136.29 tok/s（+2.01%）`，相对
D01 基线 `6,466.45 tok/s` 提升 `+6.90%`。

原先期望的六档平均 `+2,000 tok/s`（目标 `8,466.45 tok/s`）仍未达到；
本轮保留的是当前可复现且质量、decode 和运行态均通过的最佳方案。

## 十次部署账本

| 尝试 | 变量/候选 | 六档均值 tok/s | 结果 |
| ---: | --- | ---: | --- |
| 01 | MoE slice 8192 / WO 4096 | 6,791.17 | 噪声内，未保留 |
| 02 | 首次切换 16384/8192 | — | env 键集未更新，启动前失败 |
| 03 | MoE slice 16384 / WO 8192 | 6,757.22 | 回退，拒绝 |
| 04 | MoE slice 2048 / WO 1024 | 6,797.00 | 噪声内，未保留 |
| 05 | prefill block 128 / stages 3 | — | 动态 shared memory 103,168 bytes 失败 |
| 06 | prefill block 128 / stages 2 | 6,850.05 / 6,830.50 | 有正收益，保留候选 |
| 07 | prefill block 192 / stages 2 | 6,552.59 | 明显回退，拒绝 |
| 08 | prefill block 96 / stages 3 | 6,875.71 / 6,853.70 | 有正收益，保留候选 |
| 09 | prefill block 160 / stages 2 | 6,954.15 / 6,938.96 | 最高，保留 |
| 10 | block 160 clean boot | 6,912.95 | 最终干净启动，保留 |

尝试 02 和 05 是失败启动，但其 rank inspect/log 已保存；没有把失败轮次
伪装成性能结果。原始 JSON 在 `runs/`，最终逐字源码快照在 `frozen-best/`。

## 最终六档

| Size | D10 baseline | run-09 first | run-09 repeat | run-10 final |
| ---: | ---: | ---: | ---: | ---: |
| 8K | 5,776.33 | 5,914.70 | 5,876.67 | 5,792.75 |
| 16K | 6,688.36 | 6,818.51 | 6,823.38 | 6,818.31 |
| 32K | 7,081.74 | 7,303.93 | 7,267.82 | 7,299.17 |
| 64K | 7,208.12 | 7,379.23 | 7,384.67 | 7,316.33 |
| 128K | 7,095.71 | 7,312.20 | 7,299.20 | 7,287.78 |
| 256K | 6,809.69 | 6,996.33 | 6,982.03 | 6,963.38 |
| **Mean** | **6,776.66** | **6,954.15** | **6,938.96** | **6,912.95** |

## Decode、质量与长上下文

- 最终 c1/c4 使用 30 s closed-loop 协议：`71.90 / 138.67 tok/s`；
  c4 相对 D10 `139.07` 为 `-0.29%`，在验收波动内。
- c2/c3 使用与历史相同镜像记录的 60 s 协议：
  `104.28 / 126.25 tok/s`；历史同镜像参考 `104.90 / 126.45`，
  `-0.59% / -0.16%`。
- matched model gate（32 prompts、64 tokens、thinking=false）：
  output SHA-256
  `6cf26aedc93c0c01bf81ed4dac8a67e77043222719930977e034aba70a9ff99a`
  与 D10 完全一致；perplexity `1.1178292269357697`，rank1 `1.0`，
  failed `0`。
- 最终 1,040,000-token cold prefill：`5,290.02 tok/s`，无失败、无 OOM。
- 最终四机 `OOMKilled=false`、restart count `0`、memguard active、
  API `/v1/models=200`、failed units `0`；MemAvailable 约
  `9.4 / 11 / 11 / 11 GiB`。

## 镜像与构建

本轮没有重编译 vLLM/NCCL native extension。candidate 镜像使用
`FROM spark-ds41f:1030` 的薄 Python overlay，只替换
`sp_prefill.py` 和 `tilelang/moe.py`；`native extensions`、NCCL、
权重加载器和模型权重保持基座镜像内容。构建在 spark-a 完成，随后按
`spark-a -> 10.251.20.2 -> 10.251.20.6 -> 10.251.20.10`
逐跳 `docker save | docker load`，四机 image ID 和实验 source label 一致。

block 128 / 3-stage 的资源失败是 GB10 的动态 shared-memory 上限；128 行
改两阶段后成功。160 行两阶段是最终最佳；192 行虽然能启动，但 padding/
occupancy 代价明显，未保留。

## 复现与回滚

最终默认 `spark/config.yaml`、`.env.deepseek.example` 和
`config/binary-prefill.cluster.json` 已指向该方案；回滚镜像
`spark-ds41f:1030` 保留不删。完整命令与源文件哈希见
`frozen-best/manifest.json`。
