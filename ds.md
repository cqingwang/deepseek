# spark/deepseek（spark-ds41f）适配计划与实施记录

> 状态：✅ 完成 / 🚧 进行中 / ⬜ 待办。本文本地记录适配决策和进度，不自动提交。

## Context

- `spark/deepseek` 是 `christopherowen/spark-ds41f` submodule，任务开始时 HEAD 为 `297c0663`。目标运行时是 vLLM karmic-kraken-beta + TileLang 0.1.15 + sparknet 0.5.0 one-shot RoCE。
- 历史验收基线为 `spark-ds41f:1030`，image ID `sha256:7918fd6145b5...`。本维护窗口四机原无该镜像，已在 spark-a 从本地 pinned source 重建并分发当前实际构建体 `sha256:84a8b73d0993200044b5eceb0892342d34a787416b7ae91e4b38f42fffc63e41`；6 个 source-tree labels 与 pinned recipe 完全一致。
- `tp4-ring` 固定配方为 1,048,576 上下文、16 序列、5 drafts、KV 10.5 GiB/rank，对应 96 个 CUDA graph rows。
- 站点四机已运行 64K 内核 `7.0.0-1019-nvidia-64k` 和驱动 580.178.04；上游 `tp4-ring` 还要求 memory saver 0.4.1。实机启动前必须先完成 Part 1。
- 已确认 `engine_id=deepseek`，由自研 `start-tp4.sh` 直接 `docker run`，不走上游 `bin/spark cluster start`。现已补齐 startup/steady memguard、workers→head 协调启动、镜像 label/角色冲突检查、API readiness 检查、失败日志保存与精确回滚。

## 本轮核对结论

原计划存在以下歧义或不合理设计，已按当前源码修正：

1. **固定面不是“不复制”，而是受控快照。**
   - 上游真实源是 `config/cluster.json` + `scripts/recipe.py:resolve(..., "tp4-ring")`。
   - 本地 `.env.deepseek.example` 保存解析后的 81 个固定环境键；`start-tp4.sh` 保存 77 个 serve 参数和 16 个 docker run 参数。
   - 快照必须由测试重新调用上游 resolver 逐 key 比对，不能把本地副本当成第二份可独立修改的配方。

2. **原计划键数错误。**
   - 模板不是 70 个键。
   - 上游固定环境为 81 个键；加入逐 rank 动态项后，容器环境为 85 个键。
   - docker run 固定参数是 16 项，不是 20 项。

3. **宿主 env 与容器 env 必须分离。**
   - `program.py` 只输出 launcher 实际消费的宿主编排参数，以及 config/common 必须覆盖的容器参数。
   - `MODEL_DIR`、`IMAGE_ID`、SSH 目标、端口等宿主键不得整包进入 `docker run -e`。
   - `start-tp4.sh` 以固定模板键集为白名单，只叠加声明的容器覆盖项。

4. **站点覆盖集必须显式。**
   - 模板同名覆盖只有 5 项：`GLOO_SOCKET_IFNAME`、`TP_SOCKET_IFNAME`、`NCCL_SOCKET_IFNAME`、`NCCL_IB_SUBNET_PREFIX_LEN`、`NCCL_IB_GID_INDEX`。
   - 上游 sparknet 渲染的逐 rank 动态项另有 4 项：`NCCL_IB_HCA`、`SPARKNET_ROCE_PEER_HCAS`、`SPARKNET_ROCE_TOPOLOGY`、`VLLM_HOST_IP`。
   - 原计划“逐 rank 仅差 `VLLM_HOST_IP`”不成立。

5. **GLP 双模型不能漏。**
   - `recipe.resolve` 固定环境还包含 `WEIGHTLESS_STEER_PATH`、`WEIGHTLESS_STEER_MODEL`。
   - serve 参数必须保留第二个 served name `deepseek-v4.1-flash-weightless` 和 `--override-generation-config {"temperature":1.0,"top_p":1.0}`。
   - 容器必须只读挂载 pinned GLP 文件；启动前校验 SHA-256 `9c66d477...fbf5cf`。

6. **允许的站点偏差限定为：**
   - 模型路径 `/models/DeepSeek-V4.1-Flash-1017`；
   - `--host`、`--port`；
   - 非空 `--api-key`；
   - `/opt/models` 直接只读挂载、runtime cache 挂载和 GLP 文件挂载。
   其他固定环境、serve 参数和 docker run 参数不得自然漂移。

7. **上游生命周期安全门已补齐并实测。**
   - 自研 launcher 现有 5 GiB/0.25s startup guard、3 GiB/2s steady guard、workers→head 协调启动、镜像 label、冲突容器/端口检查、失败日志保存和精确回滚。
   - 四 rank 实测通过；“容器 Up”之外还完成了 API、RDMA 和真实推理验收。

8. **父级 `spark-ring-mesh` 不自动纳入该 target。**
   - 父级 mesh 是研究态 hairpin/opposite-node 硬件转发，上游 sparknet `oneshot-ring4` 的 peer-HCA map 只选择直接邻居端口；两者不是同一传输合同。
   - 当前 `deploy.sh start --target=deepseek` 不刷新父级 mesh。若现网 mesh 已 active，必须先做受控对比，证明其规则对 sparknet ring4 无副作用，再决定保留还是隔离。

9. **同步边界。**
   - 父级 `sync_to.sh` 会把子模块内未忽略的源码一起镜像到远端；这允许本地未提交适配文件参与部署，但不满足上游“clean commit + cluster sync”纪律。
   - 本任务不自动提交。任何真实部署前必须记录当前文件哈希和 `git status`，并由用户决定是否先提交。

## 受管环境契约

### 层 1：`config.yaml`

- 只登记 target 路由、镜像身份、模型、TP/rank、端口/API key、runtime asset、模型容器路径和 ring 派生开关。
- 不登记上游固定 serving/NCCL 参数、`max_token`、`max_request`、chunk、CUDA graph、speculative 或 GLP 定值。
- `common.ring` / `common.spark_nodes` 是管理地址、rank、HCA、GID、子网前缀和邻居映射的唯一来源。

### 层 2：`.env.deepseek.example`

- 固定面 = 上游 `recipe.resolve(..., "tp4-ring")["environment"]` 的 81 个键，逐字保留原始值。
- 模板中的 `.env.*` 点前缀文件由 submodule `.gitignore` 豁免一行 `!.env.deepseek.example` 才能进入同步清单。
- 生成的 `.env.deepseek` 仍被忽略，不承载第二份固定配方。

### 层 3：`program.py query-managed-env`

只输出 launcher 消费的宿主编排键和站点容器键；不复制完整上游环境。

- 宿主键：`TARGET_ENGINE_ID`、container prefix、runtime assets/cache、model 路径、image/tag、API host/port、head/worker SSH 与 rank、NNODES/TP、API key。
- 容器覆盖键：上述 5 个模板同名覆盖项，加上 `NCCL_IB_HCA` 与逐 rank peer-HCA JSON。
- fail-closed：四机 rank 0..3、TP4、统一 management interface、统一 GID/HCA 集、image tag+ID 组合、绝对模型容器路径、合法前缀、无换行/制表符。

### 层 4：`start-tp4.sh`

- 读取 `.env.deepseek` 和固定模板，按模板键集过滤容器环境。
- 只在 4 个点渲染 serve 参数：模型路径、`--host`、`--port`、`--api-key`。
- 逐 rank 注入 4 个动态容器项；宿主键零泄漏。
- 容器命名 `deepseek-r0..3`；rank 0 为 API head，rank 1..3 带 `--headless`。
- `print-env` 是离线合同入口；`serve` 才执行 SSH/Docker。

### 层 5：回归测试

`spark/tests/test_deepseek_env_merge_contract.py` 直接调用上游 resolver 现算改造前基线，验证：

- 模板 81 键与上游固定环境完全相等；
- 合并后只有 5 个受管同名覆盖键，其他固定键逐字保留；
- `program.py` 输出不夹带 `max_*` 等固定 serving 参数；
- 4 个 rank 的容器环境均为 85 键，宿主键零泄漏；
- 16 个 docker run 参数、77 个 serve 参数逐项一致，除已声明的站点偏差；
- `VLLM_PLUGINS=` 空值、GLP 环境与挂载、模型/cache/GLP 只读或读写模式正确；
- 修改 `common.ring.roce_gid_index`、`common.api_url` 和 `management_if` 后，受管值跟随配置变化。

静态合同只证明“生成与传递面正确”，不证明容器进程已消费。实机仍需检查实际 `docker inspect`、NCCL/RDMA 日志和真实请求。

## Part 1：系统准备（✅）

已落盘：

- [`spark/docs/ds41f-host-prep.md`](../docs/ds41f-host-prep.md)：逐阶段 runbook、回退和证据门禁；
- [`spark/docs/ds41f-host-prep.sh`](../docs/ds41f-host-prep.sh)：默认只读 `check`，变更命令必须带 `--node`、`--yes`，`trial-boot` 另需 `--allow-reboot`；
- 版本 pin 从 `config/kernel-trial.json` 读取；脚本不自动修改 OS、不自动 reboot。

2026-11-07 四机只读基线：

| 项 | 现状 | 目标 |
|---|---|---|
| DGX OS meta | a/b 缺失，c/d `25.06.1`；不安装桌面/OTA meta | 终端目标化包升级，不使用 desktop snap |
| 内核 | 四机已重启到 `7.0.0-1019-nvidia-64k` | 保持；4K 回退仍在 |
| 驱动 | 四机运行 `580.178.04` | 已验证 |
| memory saver | 四机已安装/loaded | 0.4.1 / rev `ec1b17a8`；packing `Y` |
| swap/内存策略 | `/swap-64k.img` active；THP never、`vm.min_free_kbytes=45166` | 保持 |
| 基线 | host-recovery pass、`kho=off`、`modeset=Y`、headless、Secure Boot disabled | memory saver；fan control optional |

执行顺序：停净全部 LLM → 终端目标化 64K kernel/driver/firmware（`--no-install-recommends`）→ host baseline → modeset → swap/memory saver → 首节点 manual reboot/verify → 其余三机 → 四机验证 → 现有 SGLang target 回归 → 再接入 `deepseek`。Secure Boot 已关闭，不执行 MOK enrollment；fan-floor 是 benchmark pre-cooling 的可选件，不阻断 serving 的主机门禁。

每节点门禁：`PAGESIZE=65536`、UVM leaf packing=`Y`、`/swap-64k.img` active、`kho=off`、`doctor --live` 无差异。

四机已完成 64K 重启；内核、driver、`kho=off`、modeset、host-recovery、
64K swap/THP/min_free、memory saver 和 GRUB pin 均已通过。当前 boot 的四机
`spark/docs/ds41f-host-prep.sh check` 全 PASS；fan-control 从硬门禁改为 optional/warn。

本轮已开始主机准备：

- 四机 NVIDIA/DGX/CUDA 相关 hold 已解除并留档：a/b 各 126 个，c/d 各 102 个；
  非 NVIDIA 的 Avahi/RDMA/Python/PDF 等 hold 保留。
- 四机 `apt-get update` 已通过；旧 apt proxy 通过命令级 override 绕过，
  未改 `/etc/apt/apt.conf`。
- 四机 `host-recovery apply` 已完成，`spark-management-health.timer` active；
  重启后 host-recovery 均通过。
- 四机已完成目标化升级：64K kernel、driver 580.178.04、NVIDIA firmware、
  `nvidia-spark-grub-kho`、MLNX firmware manager 和 container toolkit；
  四机均已重启并运行在 64K 内核。
- 四机 `GRUB` 已 pin 到 64K；`modeset=Y`，`kho=off` 已在运行 cmdline。
- 四机 `/swap-64k.img` 已 active；`spark-ds41f-kernel-memory.service`
  active/enabled；THP=`never`、`vm.min_free_kbytes=45166`。
- 上游 `scripts/install-fan-control` 调用不存在的 `install.sh`，已修正为
  `prepare-key | install` 两阶段。四机 Secure Boot 已关闭，因此未完成的 MOK
  request 不再阻塞 memory saver。
- 四机 EC firmware 已由 LVFS 从 `0x03000508` 升到 `0x03000d07`。spark-a 的
  fan-control v0.1.4 probe 证据：capability 读取成功，实际字段
  `revision=1 unit_mode=0 fan0=975..4350 fan1=975..4350`，与驱动硬编码的旧
  EC 3.5.8 `1260..9000 / 1890..13500` 不符，返回 `-EBADMSG` 且 cooling
  device=0。上游 `main == v0.1.4`，无新版本；不得绕过校验，也不扩散到 b/c/d。
- 按 upstream `docs/setup.md`，fan-floor 为 recommended、serving 不依赖；
  memory saver 在 Secure Boot disabled 时也不需要 fan-control 的 MOK key。
  fan-control 因此只保留 WARN，`uvm_pack_sysmem_leaf_tables=Y` 仍是硬门禁。
- 四机已安装 memory-saver 0.4.1；loaded/disk srcversion 均为
  `37A4D127AE60F1F3AE60A02`，`uvm_pack_sysmem_leaf_tables=Y`，
  `scripts/status --json` 为 `state=packing-enabled` 且 issues 为空。
- 四机已用 `vllm/vllm-openai:nightly-af1c01499b289be555c475669ba50a88e96d846e`
  在 20 GiB、无网络、`--gpus all` 隔离容器中跑 memory-saver 的
  `allocation_stress.py` 与 `cuda_smoke.py`：3,584 次分配全量回读、4.5 GiB
  双向拷贝、BF16 matmul 和 CUDA graph 均通过；四机请求期内核日志无
  UVM/Xid/OOM/assert/fault。
- 镜像 `spark-ds41f:1030` 只在 spark-a 构建一次，image ID
  `sha256:84a8b73d0993200044b5eceb0892342d34a787416b7ae91e4b38f42fffc63e41`；
  6 个 source-tree labels 在四机全部匹配。构建耗时 2298s，8 compile jobs、
  4 NCCL jobs，最低 MemAvailable 79.8 GiB；GPU/TileLang/sparknet import smoke
  全通过。
- 镜像沿 fabric a(`10.251.20.2`)→b(`10.251.20.6`)→c(`10.251.20.10`)→d
  逐跳 `docker save | docker load`，三跳 status 均为 0，四机 image ID 完全一致。
- `install-memory-saver` 补了三处回归：显式安装 `dkms/build-essential/headers`
  且不执行无关 `apt-get update`；pinned commit 已存在时不联网 fetch；旧 UVM
  在 DKMS install/depmod/initramfs 完成后再卸载/重载，避免 udev/CDI 的
  `nvidia-smi` 抢先载回 stock UVM。
- spark-c 的 `spark-ring-mesh.service` 在开机时因 fabric 地址尚未就绪报
  `Nexthop has invalid gateway`；网络稳定后重启该现有单元即 `mesh-up OK`。
  四机 mesh 现均 active，但该启动竞态仍是待修风险，不能在本 target 中静默
  刷新 mesh 来规避。
- 不安装 `dgx-spark-ota-update-meta`：其桌面 snap payload 与本站
  headless/no-snap 策略冲突；不删除 snapd pin，也不做无边界 `dist-upgrade`。

## Part 2：三件套接入与 launcher（✅）

### 2.1 `config.yaml`（✅）

已新增 `engines.deepseek`：

- `engine_dir/env_dir=deepseek`；
- `start: start-tp4.sh serve`、`stop: stop.sh`；
- `remote_head: true`；
- `.env.deepseek` / `.env.deepseek.example`；
- image tag + image ID 组合身份；
- `skip_build: true`；
- `nnodes: 4`、`tensor_parallel_size: 4`；
- 直接模型容器路径；
- `nccl_ib_subnet_prefix_len_from_ring: true`；
- 不重复登记 ring 地址、HCA、GID、peer map 或 serving 定值。

### 2.2 `deploy.sh`（✅）

- 新增薄 `deepseek` 分支，只调用 `program.py query-managed-env` 和 `write-managed-env`。
- 不新增 deploy 动词。
- 不隐式构建镜像；要求既有 image tag+ID。
- `remove_engine_containers` 按 `deepseek-r<N>` 精确清理。
- 暂不把 `deepseek` 加入父级 `refresh_ring_mesh` 白名单，等待 sparknet/mesh A/B。

### 2.3 `program.py`（✅）

- deepseek 分支只输出 schema 中声明且 launcher 有消费者的键。
- `NCCL_IB_HCA` 为站点 HCA 的精确排序列表；peer-HCA 由 `common.ring` 逐 rank 派生。
- flash/switchless 分支保持不变。

### 2.4 launcher / stop（✅ 运行态）

- `start-tp4.sh`：固定模板 + 受管覆盖 + 4 个逐 rank 动态项；GLP 文件摘要门；`print-env` 无副作用；启动前检查 image ID、6 个 source-tree labels、模型/GLP、冲突容器和端口。
- startup guard 在 `docker run` 前预置，workers→head 启动后持续检查四 rank 容器与 guard；API 200 后切换 steady guard。
- 失败路径保存四 rank inspect/log 并精确删除本次容器和 guard。
- `stop.sh`：按 `deepseek-r0..3` 精确删除，验证四机无残留和 head API 不再应答。
- 已完成真实 Docker/SSH 启动与运行态验收。

### 2.5 `sync_to.sh` / 同步合同（✅）

- `SUBMODULES` 新增 `deepseek`。
- 排除生成物 `.env.deepseek`。
- `test_sync_to_safety.py` 已覆盖新增源码、排除项、submodule 清单和 target-only `--delete`。

### 2.6 文档与记忆（✅）

- 本文已按实际契约重写。
- `spark/deepseek/.think_plan.md` 记录本轮实施边界。
- `spark/docs/engineering-memory.md` 已记录键数、边界、RED/GREEN、测试命令和未闭合安全门。

### 2.7 运行态验收（✅）

- 四机 `deepseek-r0..3` running、OOM=false，image ID 全部为
  `sha256:84a8b73d...`；6 个 source-tree labels 与 pinned recipe 匹配。
- 每 rank 实际 85 个受管容器键全部值与生成合同一致，另有 26 个基础镜像默认键；
  宿主键零泄漏。77 个 serve 参数和 model/cache/GLP mounts 全部匹配。
- `/v1/models` 返回 `deepseek-v4.1-flash` 与
  `deepseek-v4.1-flash-weightless`，两者 max model len 均为 1,048,576。
- stock 与 steered 的 chat、streaming 均 HTTP 200；同一提示的 greedy 输出不同，
  steered 文本 SHA-256 为 `ee2ea718...`，stock 为 `179673bd...`。
- 1M 上下文实测：1,040,000-token cold prefill 实际 prompt 1,038,722 tokens，
  TTFT 208.76s，4,975.63 tok/s，0 failed。
- 四机请求期 RoCE `port_xmit_data` 均正增长：a/b/c/d 分别约
  `266.46M / 266.16M / 265.86M / 266.17M`。
- 短验收套件结果：model 32/32 通过；c1 71.78，重复 c1 71.30 tok/s；
  c4 145.63 tok/s；c16 229.97 tok/s；32K/128K prefill 6,678/6,703 tok/s；
  全部 0 failed。c16 TTFT p50 0.430s，TPOT p50 67.64ms。
- 四机 steady guard active，MemAvailable 23.5-25.1 GiB；GPU 温度 53-58°C；
  请求后 running/waiting 归零，无 Xid/NVRM/OOM/Traceback，failed units=0。

## 验收顺序

1. 本地静态门禁：

```bash
python3 -m pytest -q \
  spark/tests/test_deepseek_env_merge_contract.py \
  spark/tests/test_sync_to_safety.py \
  spark/tests/test_cluster_config.py
bash -n spark/deepseek/start-tp4.sh
bash -n spark/deepseek/stop.sh
python3 -m py_compile spark/program.py
git diff --check
```

本轮结果：`python3 -m pytest -q spark/tests` 为 `178 passed`；
`spark/deepseek/tests/test_servebench.py` 为 `52 passed, 39 subtests passed`；
shell `bash -n`、Python `py_compile` 与 `git diff --check` 通过。

2. 路由与离线命令：

```bash
bash spark/deploy.sh list
bash spark/deploy.sh start --target=deepseek \
  --model=deepseek-ai/DeepSeek-V4.1-Flash-1017 --dry-run
```

3. 同步前检查：`sync_to.sh --targets`、`SYNC_SCOPE=engines sync_to.sh --list`、`--dry-run`；确认不删除运行配置、模型或其他 target。

4. 维护窗口：先完成 64K 迁移和现有 target 回归；确认 8888 空闲、模型/GLP/image 四机一致、memguard 方案确定后，才允许 `deploy.sh start --target=deepseek`。

5. 运行态验收：
   - 四机实际 `docker inspect` 的 env、argv、mount、image ID；
   - 四 rank `nranks=4`、world size、`NET/IB`、ring/communicator 初始化日志；
   - 请求期四机 RDMA 计数器正增量；
   - 认证 `/v1/models` 同时列出 stock 与 `deepseek-v4.1-flash-weightless`；
   - 真实 chat/流式请求、上下文与输出长度；
   - 固定协议重复性能与质量门。

## 剩余非阻断风险

- 四机主机准备已 PASS，包括安装 memory saver 后再次重启的 boot persistence：
  四机仍为 64K、Secure Boot disabled、memory-saver loaded/packing=`Y`，
  无 failed unit。
- fan-control v0.1.4 与 EC `0x03000d07` capability 不兼容，导致 cooling device
  无法注册；serving 不依赖，但 benchmark pre-cooling 暂不可用。不得删除校验
  或写 raw EC；需等待 upstream 适配或另行评审新驱动。
- GLP 资产已获下载批准，四机缓存文件均为 803,392 bytes，
  SHA-256 `9c66d477...fbf5cf`；双 served-name 运行态验收已通过。
- 父级 hairpin mesh 与 sparknet ring4 的共存尚未做受控 A/B，因此不自动刷新。
- `spark-ring-mesh.service` 的开机地址就绪竞态仍需独立修复；当前四机已 active，
  本次没有修改 mesh 规则。
- 未提交工作树不能按上游 clean-commit 纪律声明可部署；需用户决定何时提交。

## 已落盘变更（本轮，未提交）

- `spark/config.yaml`：新增 `engines.deepseek` 与受管边界注释。
- `spark/program.py`：deepseek 宿主/容器环境分支与 common.ring 派生。
- `spark/deploy.sh`：deepseek 薄受管分支。
- `spark/sync_to.sh`：登记 `deepseek` submodule，排除 `.env.deepseek`。
- `spark/docs/ds41f-host-prep.md`、`spark/docs/ds41f-host-prep.sh`：主机准备 runbook、只读检查和分阶段变更入口。
- `spark/deepseek/.env.deepseek.example`：81 键固定配方快照。
- `spark/deepseek/start-tp4.sh`：受管容器白名单、逐 rank 动态项、GLP mount。
- `spark/deepseek/stop.sh`：精确停止与残留检查。
- `spark/tests/test_deepseek_env_merge_contract.py`：改造前/合并后/容器消费契约。
- `spark/tests/test_sync_to_safety.py`：同步覆盖与 runtime 边界更新。
- `spark/deepseek/ds.md`、`spark/deepseek/.think_plan.md`：计划与实施记录。
