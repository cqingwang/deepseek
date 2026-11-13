# 2026-11-13：四机单一稳定内核收口

## 结论

四机只保留一套稳定可用的 serving 内核和配套栈：

- kernel：`7.0.0-1019-nvidia-64k`
- page size：`65536`
- driver：`580.178.04`
- Ubuntu：`24.04.5 LTS`
- DGX release：`7.6.0`
- image：`spark-ds41f:x1112`

不再保留 `7.0.0-1025-nvidia`、6.17 NVIDIA 模块、crashdump/kdump 链路、旧 initrd、
GRUB `.old` 项或 fallback 配置。

## 卸载集合

`retire-kernel` 每机精确 purge 21 个包，释放约 480 MB：

- `7.0.0-1025-nvidia` image/modules/headers/tools 全套；
- `linux-*-nvidia-hwe-24.04` fallback metas；
- `linux-modules-nvidia-fs-7.0.0-1025-nvidia`；
- `linux-modules-nvidia-580-open-6.17.0-1029-nvidia`；
- `linux-crashdump` / `nvidia-crashdump` / `kdump-tools` / `kexec-tools` /
  `makedumpfile`；
- 仅为满足 fallback 预编译模块的 `nvidia-driver-580-open`、
  `nvidia-system-core` meta。

实际 driver 组件仍保留，并由 runtime profile 校验：
`nvidia-utils-580`、`libnvidia-compute-580`、`nvidia-kernel-common-580` 和当前
64K 预编译模块。

## Sysctl 残留修复

退役后四机暴露出无包归属的
`/etc/sysctl.d/90-watermark-boost.conf`，内容只有非法单行 `0`，导致
`systemd-sysctl.service` failed、系统 degraded。

已改为稳定有效内容：

```text
vm.watermark_boost_factor = 0
```

并加入 `unify-runtime` / `retire-kernel` 的运行时复核。四机最终
`systemctl is-system-running=running`、`systemd-sysctl=active`、
`vm.watermark_boost_factor=0`。

## 最终验证

- 四机 `/boot` 只有 `vmlinuz-7.0.0-1019-nvidia-64k`；
- `vmlinuz.old` / `initrd.img.old` 不存在；
- 七个稳定内核包版本一致；
- GRUB 只使用 `zz-spark-kernel-stable.cfg`；
- host check 四机 PASS；
- runtime profile 四机 PASS；
- target `deepseek-r0..3` running、`OOMKilled=false`、`RestartCount=0`；
- API `/v1/models=200`；
- 86-env live contract PASS；
- container binary audit PASS；
- host ring audit PASS。

## 证据

- `final-stack.json`
- `snapshots/final/`：四机最终 dpkg/facts 快照
- `logs/retire-spark-{a,b,c,d}.txt`：首次精确卸载
- `logs/retire-spark-{a,b,c,d}-idempotent.txt`：幂等复跑
- `logs/host-verify-spark-*-stable-boot.txt`：单内核重启验证
- `logs/host-check-final.txt`
- `logs/runtime-verify-after-start.txt`
- `logs/live-contract.txt`
- `logs/container-binary-audit-final.json`
- `logs/ring-audit-final.json`

每机取证快照保留在：

```text
/var/backups/spark-host-kernel-retire/<UTC timestamp>
```

这些目录只含 dpkg/hold/boot 列表和 GRUB/APT 文本状态，不包含可启动内核或备用
软件包副本，不构成另一个可回退栈。
