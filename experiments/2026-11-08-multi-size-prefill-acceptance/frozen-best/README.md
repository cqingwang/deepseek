# 最佳运行参数部署冻结快照

本目录冻结第二轮多档 prefill 验证的最佳运行态，供后续镜像内 binary/kernel
实验回滚和复现。权威源码仍是工作树中的
`spark/deepseek/start-tp4.sh`、`spark/deepseek/.env.deepseek.example` 与
`spark/config.yaml`；这里保存的是它们在冻结时的逐字副本、运行时哈希和来源
补丁。

## 冻结对象

- 最佳测量：`run-09-65520-ch8-cudaconn64-qps2`
- 最终同参数复核：`run-10-final-best`
- 六档 cold prefill 均值：D09 `6,788.80 tok/s`，D10 `6,776.66 tok/s`
- 相对 D01 baseline `6,466.45 tok/s`：D09 `+322.35 tok/s（+4.98%）`
- 目标均值 `8,466.45 tok/s` 尚未达到
- 镜像：`spark-ds41f:1030`
- image ID：
  `sha256:84a8b73d0993200044b5eceb0892342d34a787416b7ae91e4b38f42fffc63e41`

## 文件

- `start-tp4.sh`：冻结时的启动器，包含 `65,520/65,424` chunk 与阈值。
- `.env.deepseek.example`：冻结时的固定环境模板，包含 8 MiB one-shot
  all-gather、8 NCCL channels、CUDA connections 64 与每连接 2 QP。
- `runtime-source.patch`：相对上游 submodule HEAD 的运行参数补丁。
- `manifest.json`：部署身份、测量值、质量门禁、源文件 SHA-256。
- `sha256.txt`：冻结文件的摘要清单。
- `tmp-evidence/`：清理 spark-a `/tmp` 前留存的小型运行证据。

## 恢复命令

先确认活动维护锁归属，再恢复这两个文件并执行正式生命周期：

```sh
cp frozen-best/start-tp4.sh spark/deepseek/start-tp4.sh
cp frozen-best/.env.deepseek.example spark/deepseek/.env.deepseek.example
./spark/deploy.sh stop
./spark/deploy.sh start --target=deepseek
```

恢复后应核对四机镜像 ID、容器 argv/env、memguard、API 和
`manifest.json` 中的质量摘要。不得用本快照覆盖模型权重、共享环网配置或其他
target。
