# 2026-11-19 `spark-ds41f:x1119`

## 范围

- 来源：`offical/main` merge `83fb21de`，合并后保留站点补丁
  `0823`（Responses custom-tool 历史回放）与 `0824`
  （structured-output copy-draft bitmask 容量）。
- vLLM：697 项补丁，patch head
  `9625f7f24876cdae68d35ebabb6914f65e39812e`，tree
  `259cf7edee8961f40e89b48002ae7394ebca49e5`。
- 这是 source/native 输入变化，必须重新构建镜像；不能复用
  `spark-ds41f:x1118`。
- 保持 `spark/config.yaml.common.ring` 为四机 `/30` 双平面环网唯一源，
  共享 `spark-ring-mesh` 不做 target 私有复制。

## 站点 profile

- `tp4-ring.json` 继承 x1118 站点 profile，仅替换镜像 tag 与 vLLM tree
  label；节点拓扑、HCA/GID、docker run 参数和 target 业务参数继续复用。
- `nodes_config` 继续指向
  `experiments/2026-11-18-x1118/nodes-ring4.json`，内容来自同一
  `common.ring` 物理邻接。

## 部署门禁

1. 本地测试、`git diff --check`、release-test classification 通过。
2. `spark/deepseek/bin/spark --cluster-config
   experiments/2026-11-16-c61-decode/image-dgx4.json build prepare`
   复用 tree `259cf7edee89`。
3. 构建 `spark-ds41f:x1119`，记录 image ID、最低 `MemAvailable` 和
   `docker inspect` labels。
4. 按 A→B→C→D 光纤可达顺序 `docker save | docker load`，四机 image ID
   必须一致。
5. `deploy.sh stop` 后确认零容器、零 GPU compute，再
   `deploy.sh start --target=deepseek`。
6. 四个 rank 均为 `running`、`OOMKilled=false`、`RestartCount=0`，
   steady memguard active；live argv/env 与启动日志证明参数被消费。
7. structured-output 并发回归、普通 chat、streaming、`/v1/models` 通过。
8. decode prose/code 的 c1/c2/c4 与 prefill 32K..1M 各跑两轮，均值达到
   官方 README 门限，允许不超过 1% 负浮动。

## 结果

部署和验收完成后在此追加原始证据路径、性能汇总和实际 image ID。
