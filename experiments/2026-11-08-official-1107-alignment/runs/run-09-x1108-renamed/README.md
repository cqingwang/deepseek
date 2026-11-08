# Run 09: 独立镜像 tag `spark-ds41f:x1108`

## 命名

- 最终本地 tag：`spark-ds41f:x1108`
- image ID：
  `sha256:7525b09690aeb666919f7a48071111a185c40edf85583c8d483b08ccb3bdf5d4`
- 镜像内容来自官方 1107 release source，但不是官方作者的
  `spark-ds41f:1107` / `sha256:3f32a962...` 二进制。
- 使用独立 `x1108` tag，避免与官方 release tag 同名。

## 重启与验证

- 正式执行 `deploy.sh stop` / `deploy.sh start --target=deepseek`。
- 四机 `deepseek-r0..3` 实际 `Config.Image=spark-ds41f:x1108`，
  full image ID 均为 `7525...`。
- 四机容器 running、`OOMKilled=false`、restart count 0、memguard active。
- API `/v1/models=200`，短 chat smoke 返回 `OK`。
- 32K cold source-text prefill smoke：`8139.50 tok/s`，无失败。
- 四机仍为 2 条对角 `/32` 路由、2 条硬件 TC 规则、2 个 active marker。

## 清理

- 四机删除旧 `spark-ds41f:1107-tree-exact` 和中间
  `spark-ds41f:1107` alias。
- `spark-ds41f:binary-prefill-20261108-03` 已在此前确认无引用后删除。
- 用户随后确认不需要回退，`spark-ds41f:1030` 也已删除；当前只保留
  `spark-ds41f:x1108`，上游 vLLM 基础镜像继续作为后续构建输入。
