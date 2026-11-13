# CX7 hotplug 环网修复维护窗口

日期：2026-11-13

## 结论

- host ring 修复已安装到 spark-a/b/c/d，四机 host ring audit
  `ok=true`。
- spark-a 已执行两次真实 reboot 回归。第二次在补丁后只启动一次
  `spark-ring-mesh.service`，无自重启；hairpin 四口从 1024 恢复到
  8192，随后等待 20 秒并 `mesh-up OK`。
- `deepseek-r0..3` 已重新启动，四机 image ID 相同、
  `OOMKilled=false`、`RestartCount=0`；health/models 均 200，真实
  chat smoke 返回 `OK`。
- live contract `ok=true`，四机 86 个受管 env 与 launcher argv 一致；
  binary audit `ok=true`。
- 真实 smoke 前后，四机全部 4 个 RoCE HCA 的
  `port_xmit_data/port_rcv_data` 均为正增量。
- 用户指定的短窗口 prose c1/c2/c4 两轮均值为
  `72.067 / 107.083 / 145.833 tok/s`，所有轮次
  `failed=0 / foreign_requests=0`。相对上一稳定基线
  `71.683 / 107.217 / 146.900` 为 `+0.53% / -0.12% / -0.73%`。
- 官方 300 秒 long-window decode gate 仍未通过：最近同镜像同参数
  prose 两轮均值为 `72.65`，官方 README 为 `77.8`，差 `-6.62%`。
  本轮没有把短窗口 c1 数字冒充官方 gate。
- prefill 本轮未重跑，维持冻结基线；target image、runtime 参数和
  prefill 参数均未变化。

## 首次 reboot 发现并修复的问题

首次安装后 reboot 时，`hairpin.sh` 的 `driver_reinit` 自己产生 fabric
link event，dispatcher 在原 service 仍为 `activating` 时安排
`systemctl restart`，导致同一启动过程被多次 SIGTERM：

```text
hairpin.sh ... 1024 -> 8192
spark-ring-mesh.service: Control process exited, code=killed, status=15/TERM
Failed with result 'signal'
```

修复：

- dispatcher 读取 `ActiveState`，service 为 `activating` 时忽略自己
  hairpin 产生的 link event；
- service 增加 `Restart=on-failure` / `RestartSec=8`，若 startup 后的
  final route/TC 校验失败仍可重试；
- 回归用例：
  `spark/tests/test_ring_hotplug_resilience.py::test_dispatcher_does_not_restart_its_own_activating_hairpin_preflight`

## 最终身份

- image：`spark-ds41f:x1112`
  `sha256:cbf38e34dab0e2aafa2abea79e669469027d253c70248d4e9a54182878eb2ece`
- model：`deepseek-v4.1-flash`
- ring：物理 A→B→C→D→A，双 RoCE rail，`/30 + spark-ring-mesh`
- MTU 9000、RoCE MTU 4096、GID index 3、hairpin queue 8192
- dispatcher SHA-256：
  `a0b6c1f9dfc2a8356ab6412d224dc32f5978c2768fd66b03dbcebf3c4c7781ba`
- 第二次 reboot boot id：
  `590db9bc-3dc1-4e22-b7e1-3fd92516a826`

## 证据

- `deployment-record.json`
- `performance-summary.json`
- `remote/host-ring-generated/`
- `remote/host-ring-audit-after-dispatcher.json`
- `remote/host-ring-audit-after-reboot.json`
- `remote/host-ring-audit-final.json`
- `remote/container-binary-audit.json`
- `logs/live-contract.json`
- `logs/inference-smoke.txt`
- `logs/rdma-counter-delta.json`
- `logs/nccl-and-runtime-evidence.txt`
- `logs/prose-c1-c2-c4-two-runs.log`
- `logs/spark-a-second-reboot-journal.txt`
- `logs/spark-a-final-boot-id.txt`
- `prose/cx7-hotplug-deploy-20261113-r01/`
- `prose/cx7-hotplug-deploy-20261113-r02/`
- `SHA256SUMS`

## 复现命令

```sh
PYTHON_BIN=/Users/chan/miniforge3/bin/python3 bash spark/deploy.sh stop
HTTPS_PROXY=http://192.168.2.3:7890 \
  /Users/chan/miniforge3/bin/python3 spark/ring/mesh/install.py --apply
PYTHON_BIN=/Users/chan/miniforge3/bin/python3 \
  bash spark/deploy.sh start --target=deepseek
```

验证入口：

```sh
/Users/chan/miniforge3/bin/python3 \
  spark/deepseek/experiments/2026-11-12-official-main-x1112/audit_host_ring.py \
  --generated spark/deepseek/experiments/2026-11-13-cx7-hotplug-deploy/remote/host-ring-generated \
  --out spark/deepseek/experiments/2026-11-13-cx7-hotplug-deploy/remote/host-ring-audit-final.json
```
