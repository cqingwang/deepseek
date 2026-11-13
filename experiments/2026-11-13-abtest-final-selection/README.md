# 2026-11-13：A/B 最终参数选择

## 最终保留

本记录是 `spark/deepseek` target 的最终选择。生产配置以以下文件为唯一
真相源：

- 镜像：`spark-ds41f:x1112`
  `sha256:cbf38e34dab0e2aafa2abea79e669469027d253c70248d4e9a54182878eb2ece`
- runtime profile：
  `experiments/2026-11-12-official-main-x1112/verified-runtime-profile.json`
- 固定环境模板：`.env.deepseek.example`，其中 `OMP_NUM_THREADS=8`
- 环网：`/30`、`spark-ring-mesh`、GID 3、MTU 9000 / RoCE MTU 4096
- GPU clock limit：sparkDash 原始 `0,2200 MHz`

runtime profile：

```json
{
  "environment": {
    "CUDA_DEVICE_MAX_CONNECTIONS": "64",
    "NCCL_MAX_NCHANNELS": "8",
    "NCCL_MIN_NCHANNELS": "8",
    "SPARKNET_ROCE_ALLGATHER_MAX_BYTES": "8388608"
  },
  "serve_options": {
    "--max-num-batched-tokens": "65520",
    "--long-prefill-token-threshold": "65424"
  }
}
```

站点仍注入 `NCCL_IB_QPS_PER_CONNECTION=2`。四机 86-env live contract
证明上述值实际进入容器，启动日志证明 L2/prefill、V2 runner 和 GLP
配置均实际消费。

## 为什么是它

- prefill 两轮均值全部达到官方 README 允许范围：
  `8565.7 / 8712.7 / 8330.4 / 7507.1 / 5988.6 tok/s`。
- decode 两轮 prose 均值为 c1/c2/c4 `69.65 / 104.07 / 142.17 tok/s`；
  这是已测稳定 arm 中最佳组合。官方 long-window prose 两轮均值
  `72.65 tok/s`，仍未达到官方 `77.8`，但没有更优且可复现的 target
  arm 可以替代。
- 2593/3003 MHz、L2 caps `15/12/12`、CUDA32 + NCCL `4/4`、
  `/24` direct、QPS=1、OMP=1 均被拒绝。原因分别是跨 boot 不可复现、
  prose 回归、decode 回归、无收益或 prefill 回归。
- official 性能树在同一宿主上也只达到 overall `78.29` / prose `73.8`，
  说明剩余 decode 差距主要是平台/collective/EC 边界，不是 target
  漏参或环网地址方案。

## 固化身份

```text
config.yaml                         e8fbd88fb17eda7c2fff77a0a364183f676ce5dea73929a2fec0021e03748932
.env.deepseek.example               d69b1af8e91a6039c2aacf3401c6b17cf7082d977a4eb6394c150742fa324e2f
verified-runtime-profile.json       6b42052c23a7053e9699fbcb37535d9ad00b12d1e55848937bd3a8e82563a417
verify_live_container_contract.py   60963078317e61a05d3880e69987464681ffe67645c5884e550c83d47c7be3e6
```

## A/B 清场范围

保留 target 镜像、target 容器、复用测试输入和本地实验证据。清理：

- 四机 `spark-ds41f:x1112-original`；
- 四机 `/opt/abtest` 原版、性能臂和临时 worktree；
- head 的 `/opt/abtest-results`；
- 不再需要的临时目录和缓存。

不执行 `docker system prune`，不删除 target `spark-ds41f:x1112`。

清理已完成，机器清单见 `cleanup-20261113.json`。四机
`spark-ds41f:x1112-original`、`/opt/abtest`、`/opt/abtest-inputs`、
`/opt/abtest-results` 和空的原版 cache 目录均已删除。最终只剩四机
`spark-ds41f:x1112` target 容器的统一镜像；spark-a 保留官方 vLLM
base image 作为后续构建前置，不作为服务 target。
