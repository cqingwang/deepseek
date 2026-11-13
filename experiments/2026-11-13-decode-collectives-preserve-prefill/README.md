# 2026-11-13：官方 collectives 参数与 8 MiB prefill all-gather 隔离 A/B

## 目的

官方 1107 runtime profile 使用 `CUDA_DEVICE_MAX_CONNECTIONS=32` 和
NCCL `4/4` channels；target 为了冷 prefill 保留了
`SPARKNET_ROCE_ALLGATHER_MAX_BYTES=8388608`、CUDA `64`、NCCL `8/8`。

本实验只把 CUDA connections 和 NCCL channels 切到官方值，保留已经验证
prefill 的 8 MiB all-gather、batch `65520` 和 long-prefill `65424`，
用于隔离 decode：

- arm：`arms/official-coll-ag8m-runtime-profile.json`
- `CUDA_DEVICE_MAX_CONNECTIONS=32`
- `NCCL_MAX_NCHANNELS=4`
- `NCCL_MIN_NCHANNELS=4`
- `SPARKNET_ROCE_ALLGATHER_MAX_BYTES=8388608`
- 站点仍注入 `NCCL_IB_QPS_PER_CONNECTION=2`

## 结果

官方 208 prose prompts，`10 s warmup + 30 s + 3 windows`，每个并发两轮：

| 并发 | verified 8/8 + CUDA64 | arm 4/4 + CUDA32 | 变化 |
| ---: | ---: | ---: | ---: |
| c1 | 69.650 | 68.700 | -1.36% |
| c2 | 104.067 | 103.250 | -0.78% |
| c4 | 142.167 | 141.667 | -0.35% |

全部 `failed=0`、`foreign_requests=0`。arm 的 c1 第二轮出现
`67.87 tok/s`，但即使忽略该轮也没有超过 verified 的 c1 第一轮
`69.80`；c2/c4 同样没有收益。该 arm 拒绝。

四机 live contract 在 arm 参数下为 `ok=true`：86 env、argv、
CUDA32、NCCL 4/4、all-gather 8 MiB、QPS2、batch `65520`、
long-prefill `65424` 均实际进入容器。

另外用 live target policy 跑 collective probe，runtime proxy 实际为
`max_gather_bytes=8388608`、`slot_bytes=8388608`，证明 8 MiB 不只是
env 字符串。该 policy 的 24-shape 几何平均仍比官方 1107 probe 慢
`16.41%`，与官方 2 MiB policy 的 `16.44%` 基本相同，说明 collective
缺口不是 2M/8M 配置本身。

## 判定规则

- 每次部署先 stop、确认四机容器/GPU compute=0，再 start。
- 先验证四机容器实际 env、argv 和启动日志，再测试。
- prose c1/c2/c4 各两轮；若没有超过 boot 噪声的收益则拒绝。
- 若 decode 有收益，再检查 prefill 是否仍保持冻结参数下的达标结果。

## 结论

`CUDA_DEVICE_MAX_CONNECTIONS=32` + NCCL `4/4` 在保留 8 MiB
all-gather 时没有 decode 收益。生产 runtime profile 应恢复
`2026-11-12-official-main-x1112/verified-runtime-profile.json`
的 CUDA64 / NCCL 8/8 / all-gather 8 MiB / QPS2。

测试后已执行 stop/start 恢复生产 profile。最终四机 86-env live
contract 为 `ok=true`，CUDA64、NCCL 8/8、QPS2、batch `65520`、
long-prefill `65424` 均实际进入容器；API 200，四机 running、
OOM=false、restart=0、memguard active。
