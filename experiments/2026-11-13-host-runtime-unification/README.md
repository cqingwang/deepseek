# 2026-11-13：四机 headless 宿主运行栈统一

## 结论

四机模型服务依赖的宿主运行栈已收敛到
`spark/docs/host-runtime-profile.json`：

- Ubuntu `24.04.5 LTS`、DGX release `7.6.0`；
- 当前运行内核仍为 `7.0.0-1019-nvidia-64k`，page size `65536`；
- NVIDIA driver `580.178.04`；
- Docker `29.2.1`、containerd `2.2.1`、runc `1.3.4`；
- NVIDIA Container Toolkit `1.20.1`；
- DGX 网络/固件/电源/GRUB 管理包和 Ubuntu 核心编译/网络/存储库版本四机一致；
- `lldpd` 四机 `enabled + active`。

未安装 snap，也未新增桌面应用。`dgx-spark-ota-update-meta` 和
`nvidia-spark-run-apt-upgrade` 保持 absent。后者在 A/B 曾处于 enabled 状态，
下次启动会在 64K 内核与 fallback 模块不匹配时执行 `apt-get full-upgrade`
并 reboot；本轮已精确移除。

## APT 网络

历史 `/etc/apt/apt.conf` 仍指向不可达的 `192.168.2.114:7890`。实际可用代理为
`192.168.2.3:7890`，安装时使用命令级
`-o Acquire::http::Proxy=... -o Acquire::https::Proxy=...` override，
未修改持久 APT 代理配置。

## ConnectX hotplug 与环网启动竞态

安装 `dgx-spark-mlnx-hotplug 26.01-1` 后，`cx7-pcie-hotplug` 会在首次 link-up
后再次重枚举 ConnectX 功能，NetworkManager 随后重建地址。旧的
`wait-fabric.sh` 只采样一次 operstate，one-shot mesh 可能在这轮重置前完成，
随后 `/32` 对角路由被清掉。

修复：

- `wait-fabric.sh` 要求四个 fabric 接口 operstate=up 且 IPv4 地址连续稳定
  20 秒后才放行；
- `/etc/NetworkManager/dispatcher.d/90-spark-ring-mesh` 对 fabric link 事件
  去抖 8 秒后重启 mesh；
- 生成的 `mesh-up` 末尾复核全部 `/32` 路由。

已做 spark-a 重启和 peer reboot 回归，最终 host ring audit `ok=true`。

## 最终身份

- target：`spark-ds41f:x1112`
  `sha256:cbf38e34dab0e2aafa2abea79e669469027d253c70248d4e9a54182878eb2ece`
- 四机 `deepseek-r0..3`：running、`OOMKilled=false`、`RestartCount=0`
- API `/v1/models=200`
- live contract：`ok=true`，86 env；batch `65520`、long-prefill `65424`、
  CUDA connections `64`、NCCL `8/8`、all-gather `8 MiB`、QPS `2`
- container binary audit：`ok=true`，四机 image ID、labels、关键二进制 hash
  和 NCCL version code `23007` 一致
- host ring audit：`ok=true`

## prose c1/c2/c4

口径与此前短窗口 A/B 相同：官方 208 prose prompts，10 s warmup + 30 s，
3 windows，thinking=false，每个并发两轮。该口径不是官方长窗口 gate。

| 并发 | 统一前 verified | 统一后两轮均值 | 变化 |
| ---: | ---: | ---: | ---: |
| c1 | 69.650 | 71.683 | +2.92% |
| c2 | 104.067 | 107.217 | +3.03% |
| c4 | 142.167 | 146.900 | +3.33% |

全部轮次 `failed=0`、`foreign_requests=0`。本次没有重新宣称官方长窗口
prose `77.8 tok/s` 已通过；此前记录的 target 长窗口差距仍需后续底层
collective/平台归因。

## 证据

- `final-stack.json`：四机 facts、精确版本、剩余 dpkg inventory 差异、性能汇总
- `snapshots/final/`：四机最终 dpkg/hold/facts 快照
- `apt-plan/`：统一前四机只读 apt 事务
- `logs/live-contract-after-host-unify.txt`
- `logs/container-binary-audit-final.json`
- `logs/ring-audit-live-final.json`
- `logs/prose-c1-c2-c4-two-runs.log`
- `prose/summary.json`

回滚备份位于各机
`/var/backups/spark-host-runtime/<UTC timestamp>`，包含 `dpkg-query`、holds、
GRUB、APT sources/preferences 和 fstab。

## 剩余边界

本 profile 统一的是服务运行栈，不把 A/B 的桌面/开发/娱乐包复制到 C/D。
因此完整 `dpkg-query` 清单仍有非运行栈差异，例如 desktop、GNOME、X11、
Go、Fortran、OpenMPI、BPF 工具和开发头文件。按用户要求没有安装 snap，
也没有为“清单完全相同”向 C/D 安装桌面应用。服务路径依赖的内核、驱动、
容器运行时、DGX 网络/固件管理、环网、镜像、env/argv 和关键二进制均已一致。
