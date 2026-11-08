# Run 08: 规范镜像 tag 并删除未使用镜像

## 变更

- 旧 tag：`spark-ds41f:1107-tree-exact`
- 新 tag：`spark-ds41f:1107`
- image ID 不变：
  `sha256:7525b09690aeb666919f7a48071111a185c40edf85583c8d483b08ccb3bdf5d4`
- 同名官方 release image ID 仍为：
  `sha256:3f32a962ce04b7c8d85337cd427a8946b8b6bcaadd97cc8d8a8cd14e8d68a6ce`。
  因此同名 tag 不等价，运行时验收仍必须核对本站 image ID。

## 部署门禁

- 执行正式 `deploy.sh stop` / `deploy.sh start --target=deepseek`。
- 四机 `Config.Image=spark-ds41f:1107`，full image ID 均为 `7525...`。
- 四机容器 running、`OOMKilled=false`、restart count 0、memguard active。
- API `/v1/models=200`；32K cold source-text smoke prefill
  `8035.89 tok/s`，无失败。
- 四机仍为 2 条对角 `/32` 路由、2 条硬件 TC 规则、2 个 active marker。

## 镜像清理

- 四机均确认
  `spark-ds41f:binary-prefill-20261108-03` 没有容器引用后删除。
- 四机删除旧 alias `spark-ds41f:1107-tree-exact`。
- 保留 `spark-ds41f:1107` 和回滚 `spark-ds41f:1030`。
- binary-prefill 源码与实验快照仍保存在
  `experiments/2026-11-08-binary-prefill/frozen-best/`，仅删除可重建镜像。
