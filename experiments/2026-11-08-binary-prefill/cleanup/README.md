# 2026-11-08 Block160 收尾清理记录

> 后续状态（2026-11-08）：image03 后来被官方 1107 重建镜像取代，并已按用户
> 要求从四机删除。本文“保留”段记录的是当时状态；最终 servable 镜像只有
> `spark-ds41f:x1108`，`spark-ds41f:1030` 继续作为回滚。

## 保留

- 运行镜像 `spark-ds41f:binary-prefill-20261108-03`
  (`sha256:af78e1ad...`) 四机一致，`deepseek-r0..3` 均 running。
- 回滚基座镜像 `spark-ds41f:1030` (`sha256:84a8b73d...`)。
- spark-a 的 vLLM 构建基座、模型权重、GLB/GLP、target cache 和最终
  run-10 证据。

## 已删除

- 四机的中间 candidate：
  `spark-ds41f:binary-prefill-20261108-01` 与
  `spark-ds41f:binary-prefill-20261108-02`。
- spark-a `/tmp/c01.args`、`/tmp/c01.env`。
- spark-a 已归档的 failed-start 目录：
  `/opt/spark/deepseek/results/private/failed-starts/20261108T000249Z-deepseek`。
- spark-a Docker Build Cache 从 `219.9 kB` 清理到 `0`；其余三机原本为 0。
- 本地研究用的 `source-base/` 临时源副本，以及 run-06 的超大
  `model.json.gz`/`model-matched.json.gz` 中间产物；最终 run-10
  matched model 证据保留。

## 清理后门禁

- 四机 image03 ID 和 experiment source label 一致。
- 四机 `OOMKilled=false`、restart count `0`、`deepseek-r*-memguard`
  active、failed units `0`，API `/v1/models=200`。
- 四机无候选 01/02 镜像、无 dangling image、无匿名 volume；
  `/tmp` 无 candidate/deepseek 临时文件。
- 未执行 `docker system prune`、全盘清理、模型或 ring 删除。
- 原始最终审计输出：[`final-audit.txt`](final-audit.txt)。
- 维护锁已原子归档为
  `~/spark-hold.deepseek-binary-prefill-20261108.closed.json`；
  四机活动 hold 和 pending request 均为 absent，关闭后 API 仍为 200。
