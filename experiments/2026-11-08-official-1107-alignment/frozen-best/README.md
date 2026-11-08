# 官方 1107 decode/prefill 对齐

## 目标

以 `offical/main` 的 `spark-ds41f:1107` 正式基线为对照：

- decode 达到或超过官方 README 的 c1 `82.623`、c8 `207.447`、
  c16 `272.863` output tok/s；
- c1-c4 相对本站官方 1107 基线不出现超过约 3% 的稳定回退；
- prefill 主排名使用 cold source-text `8K/16K/32K/64K/128K/256K`
  六档 tok/s 的算术平均；
- prefill `32K/131K/262K/524K/1M` 不低于本站官方 1107 基线；
- 优先运行参数；只有运行参数无法达标时才进入镜像内源码/二进制改动。

## 身份

| 项 | 值 |
| --- | --- |
| remote | `git@github.com:christopherowen/spark-ds41f.git` |
| branch | `main` |
| 正式 release commit | `59f523c543cb61e5a2d0df616cb4efa459677b35` |
| 正式 image | `spark-ds41f:1107` |
| 正式 image ID | `sha256:3f32a962ce04b7c8d85337cd427a8946b8b6bcaadd97cc8d8a8cd14e8d68a6ce` |
| source labels | vLLM `ed1bdb7a` / TileLang `e2b00182` / sparknet `8cc28a5c` / NCCL `6af10aa7` / TileKernels `cb2fa41c` / B12X `1a647a31` |

`offical/main` 的后续 commit `cc20ed18` 未改变上述 runtime tree，但不能把在
`cc20ed18` 重建出的新 image ID 写成官方 `3f32...`。因此正式对照先在 detached
`59f523c` 构建；main 后续内容仅作源码和文档参考。

## 站点差异

- 官方 recipe 以聚合 `/24` 记录 RoCE 网络；本站实际逐边 `/30`，父级拓扑
  派生 `NCCL_IB_SUBNET_PREFIX_LEN=30`。这是站点适配，不改变官方 runtime tree。
- 官方站点管理接口为 `enP7s7`；本站使用 `common.spark_nodes` 的实际管理接口。
- 父级容器名保持 `deepseek-r0..r3`，API 端口保持 `8888`；官方只作性能标准，
  不把其 `spark-ds41f` 容器名和 `8000` 端口引入本站生命周期。
- 每次部署仍严格执行 `spark/deploy.sh stop`，确认四机 target 容器为 0，再
  `spark/deploy.sh start --target=deepseek`。

## 运行 profile

参数实验使用 `profiles/run-NN-*.json`。profile 只能覆盖已注册容器环境键和
基线 argv 中恰好出现一次的单值 serve flag；不得新增容器环境键，不得删除站点
拓扑覆盖键。实验 start 命令由引擎配置的 `runtime_profile` 选择。

## 环网审计

- 四机 image ID、57 层 RootFS 与六个 source-tree labels 一致；容器内
  `libnccl.so.2`、sparknet 源码树和 `roce_proxy-256c711c72bc15fd.so` 哈希一致。
- `mlx5_core`、`mlx5_ib`、`ib_core`、`ib_uverbs`、`rdma_cm`、`rdma_ucm`
  模块文件 SHA-256 与 vermagic 四机一致。
- `mlx5-rdma-tx-rewrite-probe` 在 spark-d 的整文件 hash 因本机较旧
  GCC/binutils/glibc 构建标识不同；`.text/.rodata/.eh_frame/.data` 四机
  完全相同，不是运行代码差异。后续若重装 mesh，先对齐工具链。
- `spark-a`/`spark-c` 曾在链路重载后丢失 mesh-up 的两条对角 `/32` 路由。
  全量 stop 后执行既有 `mesh-up.sh` 恢复，四机恢复为各两条路由、两条
  `skip_sw + in_hw_count 1` 规则和两个 active marker。相同官方参数复测
  六档均值仅变化 `+0.43%`，说明缺路由是部署漂移风险，但不是 1107 性能
  差距的主要原因。

## 部署记录

用户要求使用当前最佳方案收尾，因此停止了继续扫参；每次均先
`deploy.sh stop`，确认 target 容器和 GPU compute 为 0 后再 start。

| Run | 参数 | 六档均值 tok/s | 结果 |
| ---: | --- | ---: | --- |
| 01 | 官方 `16K/16,288`、AG 2 MiB、4 channels、CUDA 32 | 7,424.27 | 本站官方基线 |
| 02 | 同 01，恢复四机缺失对角路由 | 7,456.04 | `+0.43%`，噪声内 |
| 03 | `65,520/65,424`、AG 8 MiB、8 channels、CUDA 64、QPS 2 | **7,870.02** | **最高，`+6.01%`** |
| 04 | 03 改 batch `49,152/49,056` | 7,788.68 | `-1.03%` |
| 05 | 03 改 batch `65,535/65,439` | — | 启动失败，grid y=65,546 超 CUDA 上限 |
| 06 | 03 同配置 clean boot + 官方长测 | 7,807.38 | clean confirmation |
| 07 | 06 显式删除 3 个无消费者的旧 binary-prefill env | **7,875.85** | **最终 clean profile，`+6.08%`** |

六档最高方案的实际参数：

```json
{
  "environment": {
    "CUDA_DEVICE_MAX_CONNECTIONS": "64",
    "NCCL_IB_QPS_PER_CONNECTION": "2",
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

最终 run-07 clean profile：

- 六档 `6682.42 / 7772.61 / 8317.21 / 8368.32 / 8272.85 / 7841.71 tok/s`；
- 524K `7010.69 tok/s`，1M `5629.71 tok/s`，相对本地官方基线
  `+6.43% / +4.76%`（run-06 同有效参数测得；run-07 仅删除无消费者的
  旧 binary-prefill env）；
- c1/c8/c16 官方长测（run-06）`77.66 / 194.62 / 242.25 tok/s`，相对本地基线
  `-1.16% / -1.08% / -1.79%`；
- decode 全长测未达到 README 的 `82.623 / 207.447 / 272.863 tok/s`；
  负载中 SM 2184–2190 MHz、52–54 C、无 throttle flag，未发现降频。

## 结论

- prefill 运行参数路径已确认正收益：最佳六档均值 `7875.85 tok/s`，
  相对本地官方 1107 基线提升 `+451.59 tok/s（+6.08%）`；最终运行态
  与该 profile 完全一致。
- decode 未达标，不能把本轮写成 decode 对齐完成。运行时参数只能保持
  本地基线附近，下一阶段按用户新方向检查镜像内 binary/build provenance。
- 可复现冻结资产在 `frozen-best/`，包含 profile、父级配置、launcher、
  env template、官方 cluster profile、镜像身份、指标和逐文件 SHA-256。
- 正式 1107 的已知质量变化必须单独记录，不能与性能门禁混淆。
