# 2026-11-13：官方 TP4 L2 prefetch caps A/B 与部署参数消费复核

## 结论

- 官方 `offical/main` 已从 `2b9a6fb6` 前进到 `d8a24bf9`；新增提交只包含
  candidate-55 的 grid/prefill attribution 记录，没有修改 vLLM、TileLang、
  sparknet、NCCL 或其他已部署 runtime tree，因此 `spark-ds41f:x1112`
  不需要重建。
- 本地未提交 merge 仍保留在 `2b9a6fb6`；`d8a24bf9` 的纯记录 delta 已
  fetch 并逐文件核对，但没有为了记录文件去 abort/重做现有 dirty merge。
  这不影响部署源码树或镜像身份。
- 官方最新记录建议 TP4 把 L2 prefetch 窗口从代码默认 `20/16/20 MB`
  限制到 `15/12/12 MB`。本机 A/B 证明参数确实进入四机容器并被
  `vllm.models.deepseek_v4_1.l2_prefetch` 消费，但没有带来 prose 收益：
  c1/c2/c4 两轮均值全部下降，官方长窗口 c1 prose 也从 `72.6` 降到
  `72.2 tok/s`。因此拒绝 caps，生产模板保持默认预算。
- 同镜像、同 profile 的 `/24` 直连与 `/30 + mesh` 可逆 A/B 也没有差异：
  c1/c2/c4 两轮均值仅变化 `-0.12% / -0.64% / -0.06%`。因此当前短窗口
  decode 缺口不能归因于 `/30` 地址方案或 host mesh 本身。
- 复核时发现远端 `/opt/spark/config.yaml` 曾残留已拒绝的
  `OMP_NUM_THREADS=1` profile，而本地 SSOT 已恢复官方 `OMP=8`。本轮已
  重新同步并清理远端 `.env.deepseek` 的临时 caps 行；最终四机 live
  contract 为 86 env，`OMP_NUM_THREADS=8`，candidate/window 参数均为
  已验证值。

## 身份与部署

- 镜像：`spark-ds41f:x1112`
  `sha256:cbf38e34dab0e2aafa2abea79e669469027d253c70248d4e9a54182878eb2ece`
- 四机：`deepseek-r0..3`，`running`、`OOMKilled=false`、
  `RestartCount=0`；steady memguard 存活。
- 每次切换都先 `deploy.sh stop`，确认四机 target 容器和 GPU compute
  均为 0，再 `deploy.sh start --target=deepseek`。
- 最终 live contract：四机 86 env、serve argv、batch `65520`、
  long-prefill `65424`、CUDA connections `64`、NCCL channels `8/8`、
  all-gather `8 MiB`、QPS `2` 全部通过。
- 启动期曾出现一次四机在 `world_size` 后 10 分钟无 RoCE 流量的
  CUDA/NCCL 同步停滞；采集了四机日志、inspect、perf/NCCL 线程栈和
  host ring audit（`ok=true`）后 stop。随即做一次干净重试，正常启动。
  该现象是启动路径的间歇性同步停滞，不是性能参数收益，也没有据此做
  单 rank 判断。

## L2 caps 实消费

实验 arm 位于 `arms/`：

| arm | WO / FFN / NEXT |
| --- | --- |
| `l2-default-runtime-profile.json` | `20/16/20 MB` |
| `l2-cap-15-12-12-runtime-profile.json` | `15/12/12 MB` |

caps boot 的四机容器内模块常量实测为：

```text
WO=15000000  FFN=12000000  NEXT=12000000 bytes
```

启动日志实际打印：

```text
layer 0 WO 3 segs 15.0 MB | FFN 7 segs 12.0 MB | NEXT 6 segs 12.0 MB
```

恢复默认后四机模块上限为 `20/16/20 MB`，实际启动计划填充到
`18.9/14.8/14.8 MB`。这证明不是只改了 docker inspect，而是参数进入了
运行时 plan。

## 性能 A/B

口径：官方 prose prompt 208 条；`10 s warmup + 30 s + 3 windows`，
thinking=false；每个并发两轮取均值。

| 并发 | 默认 20/16/20 | caps 15/12/12 | caps 相对变化 |
| ---: | ---: | ---: | ---: |
| c1 | 69.650 | 69.083 | -0.81% |
| c2 | 104.067 | 102.683 | -1.33% |
| c4 | 142.167 | 140.017 | -1.51% |

所有轮次均 `failed=0`、`foreign_requests=0`。

默认 256 prompts、`60 s warmup + 300 s + 10 windows` 的官方长窗口：

| arm | overall c1 | prose | code | steps/s | tokens/step |
| --- | ---: | ---: | ---: | ---: | ---: |
| 已验证默认（此前同镜像/同参数 boot） | 76.97 | 72.6 | 105.7 | 32.80 | 2.361 |
| caps 15/12/12 | 76.60 | 72.2 | 106.1 | 32.40 | 2.367 |

caps 没有改善 engine steps/s；prose 相对默认下降 `0.55%`。官方 README
的 TP4 prose 门仍是 `77.8 tok/s`，因此该实验不解决 decode 缺口。

### `/24` 直连 vs `/30 + mesh`

使用同一 `x1112` 镜像、同一 verified runtime profile，仅临时停止 mesh、
把双平面改为官方风格连续的 `10.40.0.0/22` / `10.41.0.0/22` 内
四个 `/24` 链路，再以 `NCCL_IB_SUBNET_PREFIX_LEN=24` 启动。四机 direct
neighbor ping、86-env live contract 均通过。

| 并发 | `/30 + mesh` | `/24` direct | 变化 |
| ---: | ---: | ---: | ---: |
| c1 | 69.650 | 69.567 | -0.12% |
| c2 | 104.067 | 103.400 | -0.64% |
| c4 | 142.167 | 142.083 | -0.06% |

测试后已恢复 `/24 -> /30` 地址、重新启动 mesh；最终四机 service active、
每机 2 个 marker、container env 为 `NCCL_IB_SUBNET_PREFIX_LEN=30`，
host ring audit `ok=true`。生产配置始终保持本地 `/30` SSOT。

### 最终生产 boot

恢复 `/30 + mesh` 后，官方 256-prompt 长窗口 c1 为 overall `77.19`、
prose `72.7`、code `106.6`。与此前同镜像同参数 boot 的 prose `72.6`
合计两轮均值 `72.65`，相对官方 `77.8` 仍低 `6.62%`；decode gate
未通过。prefill 冻结参数与此前两轮达标结果保持不变。

## 回归

为 cap A/B 曾让模板白名单测试先红后绿；性能拒绝后已撤回生产模板中的
caps，仅保留实验 arms。最终本地回归：

```text
spark/tests/test_deepseek_l2_prefetch_caps.py
spark/tests/test_deepseek_env_merge_contract.py
12 passed
```

## 剩余边界

- 代码、镜像、基础 digest、sparknet/NCCL/TileLang 二进制、容器 argv/env、
  `/30` 环网和 host mesh 均已对齐/验证。
- 宿主内核 `7.0.0-1019-nvidia-64k`、64 KiB page、CPU `performance`、
  THP `never`、`vm.min_free_kbytes=45166`、16 GiB swap、memory-saver
  0.4.1、200 Gb/s 四口链路、MTU 9000、RoCE MTU 4096、GID 3 全部通过；
  细节见 `host-baseline-final.json`。
- 仍有两个官方 doctor 已知差异：控制面仍在 Wi-Fi `wlP9s9`；fan-control
  DKMS/module/service 未安装。后者官方标注为 bench-only warning，不作为
  serving 必需项。
- fan-control v0.1.4 已在 spark-a 做受控安装验证：DKMS 构建和加载成功，
  但 EC/FF-A packet service 返回 `-EBADMSG`，驱动拒绝创建
  `dgx_ec_fan_floor` cooling device。四机 BIOS 均为
  `5.36_0ACUM028 / 2026-09-04`、板卡均为 `P4242`。已执行官方卸载流程，
  恢复模块未加载、DKMS/service/source 均不存在的原状态。该 fan floor
  条件当前受 EC 契约阻断，不能用于官方 bench 复现。
- decode 仍未达到官方 prose `77.8 tok/s`；L2 caps 与 `/24` direct 两个
  主要候选均已用同镜像 A/B 排除。剩余证据指向当前四机平台的
  collective/engine-step 边界，而不是漏参、地址方案或 target 源码。
  若继续追官方数值，需要更底层的集体通信/硬件平台归因，不能再用短窗口
  参数猜测。
