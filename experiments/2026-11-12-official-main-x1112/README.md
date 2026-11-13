# 官方 latest-main x1112 对齐、参数消费与验收收尾

## 最终身份

- 父仓工作树：父仓 `main`，保留本地修改；子模块 `spark/deepseek`
  `HEAD=a563669f755c200514b630951400bd515f450ae5`，未提交 merge
  `MERGE_HEAD=bbfe2618efbd3e2143363eb9262d1c7fb89e5460`。
- 官方基线：`offical/main=bbfe2618efbd3e2143363eb9262d1c7fb89e5460`。
- 镜像：`spark-ds41f:x1112`，
  `sha256:cbf38e34dab0e2aafa2abea79e669469027d253c70248d4e9a54182878eb2ece`。
- 四机 `deepseek-r0..3` 使用同一 image ID，`running`、`OOMKilled=false`、
  `RestartCount=0`；认证 `/v1/models=200`。
- 模型：`/opt/models/deepseek-ai/DeepSeek-V4.1-Flash-1017`，容器内
  `/models/DeepSeek-V4.1-Flash-1017:ro`；本轮没有复制权重。

## 部署门禁

每次正式验证都先执行不带 target 的 `./deploy.sh stop`，确认四机 target
容器、GPU compute 为 0、memguard 进程退出后，再执行
`./deploy.sh start --target=deepseek`。start 入口自身也会先停止目标容器。
最终 steady `memguard.sh` 进程在四机均存活。

`x1112` 已从四机删除旧 `spark-ds41f:x1108`；保留 spark-a 的
`vllm/vllm-openai:nightly-af1c01499b289be555c475669ba50a88e96d846e`
构建基座。build cache 暂留，供后续原版 abtest 镜像构建使用。

## 参数与消费审计

完整矩阵：`remote/parameter-consumption-matrix.json`。

- launcher 实际向每个容器注入 86 个键；82 个有源码或二进制消费证据，
  4 个是无消费者遗留键，0 个 unresolved。
- 遗留键：`SAFETENSORS_FAST_GPU`、
  `SPARK_DS41F_DSPARK_MAIN_PROJ_TP`、
  `SPARK_DS41F_DSPARK_VOCAB_PARALLEL`、`VLLM_CACHE_DIR`。
  DSpark 的 main-proj 已在 TP>1 时自动分片，vocab-parallel 固定启用；
  vLLM 实际使用 `VLLM_CACHE_ROOT`。
- 官方 recipe 81 键全部存在；另 5 键来自站点：QPS=2 与 4 个逐 rank
  拓扑变量。站点覆盖仅 `GLOO/TP/NCCL_SOCKET_IFNAME`、`/30` 前缀和
  GID index。
- 官方与运行 argv 同为 85 个 token、77 个 flag，flag 位置完全一致；
  仅 `--port 8000 -> 8888`、batch `16384 -> 65520`、long-prefill
  `16288 -> 65424` 三处值不同。无漏参、重复或错位。
- `verify_live_container_contract.py` 对四机 `docker inspect` 比较 86 个
  env（含逐 rank 值）及完整 argv，结果 `ok=true`。
- 启动日志实际证明：L2 预取 `grid=2 block=128 chunk=4096`、
  `Using V2 Model Runner`、GLP `/glp/vector.gguf` 39 directions。
- 红绿回归：`spark/tests/test_deepseek_parameter_consumption.py`
  由 artifact 缺失/缺运行态证据先红，补齐矩阵后 `2 passed`。

## 镜像、基础镜像与共享环网版本

- 官方 1107 source identity 的基础镜像 digest：
  `sha256:3cf14c52765a0f0c2f09035e05fa79e1e4b2f9788b64afa90fa41c1ff12d29cf`。
  spark-a 上 base tag 的 `RepoDigest` 与 x1112 的
  `org.opencontainers.image.base.digest` 均与它一致。
- 六个 source-tree labels 四机一致并与官方 1107 基线一致：vLLM
  `ed1bdb7a`、B12X `1a647a31`、NCCL `6af10aa7`、TileLang
  `e2b00182`、TileKernels `cb2fa41c`、sparknet `8cc28a5c`。
- 容器内四机 `libnccl.so.2`（version code `23007`）、RoCE proxy、
  `_C_stable_libtorch`、`_moe_C_stable_libtorch` SHA-256 完全一致。
- 官方 target 不内嵌 Fujitsu SparkRing；共享 host mesh 是父级站点模块。
  父级 pin `f16b5f43208c188c018c43820ccf52955006d1a5`，四机安装源码
  SHA-256 均为 `8684a696...12792`，与 pin 一致；生成/安装 mesh 脚本
  hash 一致，probe `.text` 四机一致。
- spark-d 的 probe 整文件 hash 不同，是因为 GCC `13.3.0-6ubuntu2~24.04.1`
  vs `~24.04` 的构建标识差异；实际 `.text` 哈希相同，不冒充二进制字节一致。
- 环网 A-B-C-D-A、MTU 9000、RoCE MTU 4096、GID 3、四机各两条对角
  `/32`、两条 `skip_sw + in_hw_count 1` TC 规则和 active marker 均通过
  `remote/host-ring-audit.json`。本站 `/30` 与官方 `/24` 是有意站点差异。

## 性能

### 冻结的 prefill

`verified-runtime-profile.json` 保持：

```json
{
  "environment": {
    "CUDA_DEVICE_MAX_CONNECTIONS": "64",
    "NCCL_MAX_NCHANNELS": "8",
    "NCCL_MIN_NCHANNELS": "8",
    "SPARKNET_ROCE_ALLGATHER_MAX_BYTES": "8388608"
  },
  "serve_options": {
    "--max-num-batched-tokens": "65520",
    "--long-prefill-token-threshold": "65424"
  }
}
```

`x1112-verified-run-01` 的 cold real-text prefill：

| 32K | 131K | 262K | 524K | 1M |
| ---: | ---: | ---: | ---: | ---: |
| 8,578.886 | 8,736.471 | 8,341.051 | 7,511.461 | 5,993.059 tok/s |

### Prose-only decode

按用户收敛口径，只测 prose-only `c1/c2/c4`：prompt 从官方
`default_prompts()` 中原样筛出自然语言模板，排除 code、SQL、JSON 和列表；
固定 176 条、digest `0af8ddd9...ba4c7`。两轮均为 `10 s warmup +
30 s measured + 3 windows`，全部 `failed=[]`。

| 并发 | run-01 | run-02 | 两轮均值 |
| --- | ---: | ---: | ---: |
| c1 | 72.033 | 71.300 | **71.667 tok/s** |
| c2 | 105.100 | 106.700 | **105.900 tok/s** |
| c4 | 144.600 | 145.867 | **145.233 tok/s** |

- 官方最新 README 的 TP4 c1 **prose** 实测为 `77.8 tok/s`；本轮均值低
  `7.88%`，c1 门未通过。
- 官方最新 README 没有给 TP4 的 c2/c4 权威数值，因此本轮记录 c2/c4
  实测和稳定性，但不伪造官方门禁。c4 不能拿 TP3 的 123.7 或 candidate
  阶段的 mixed-prompt 157.06 冒充同协议基线。
- 先前 mixed/custom-prompt 轮次已作废并从本地和远端删除，不进入均值。
- `OMP_NUM_THREADS=1` 的 c16 A/B 为 `238.20` vs `244.37 tok/s`，
  约 `-2.5%`，明确拒绝；最终运行值保持官方 `8`。

## 未对齐项与下一阶段

- 四机控制面仍在 `wlP9s9` Wi-Fi；最新 doctor 对此给出一条 warning：
  控制面使用无线接口会因尾延迟拖慢 decode。`enP7s7` 物理 link down。
- 尝试把控制面走 ConnectX 环网时，发现 Docker 管理的 nftables
  `FORWARD policy drop` 阻断跨节点 L3 转发；未修改防火墙，未把未验证的
  网络切换写成成功。
- 因此当前结论是：代码、镜像、参数、ring 数据面和二进制身份已对齐；
  prose c1 性能仍未达到官方，c2/c4 缺官方同协议数值。不能宣称最终性能达标。

## 2026-11-13 原版 A/B 后的更新

后续以原版 `spark-ds41f@bbfe2618` 独立镜像完成四机 A/B，并按官方
NVMe doctor 项调整宿主；最终 target 的 frozen prefill 已达到官方
32K..1M 两轮均值（`+0.68% / +0.64% / +0.84% / +0.63% / -0.39%`）。

decode 仍未达标：官方长窗口 c1/prose 为 `82.62 / 77.8`，target 为
`78.38 / 73.9`；但原版源码同宿主为 `78.74 / 74.5`，target 相对原版
仅 `-0.46%` 左右。因此剩余差距不能归因于 target 代码或参数错位。
完整证据、命令和结论见
[`2026-11-12-spark-ds41f-original-abtest/README.md`](../2026-11-12-spark-ds41f-original-abtest/README.md)
及同日工程记忆。

## 回归与证据

- 本地父级测试：`72 passed`。
- 环境合并合同 + 参数消费合同：`13 passed`。
- x1112 镜像内最新 doctor GLP reader：`7 passed`；GLP 文件实测
  `problems=[]`。
- 环境无关 doctor 集合：`111 passed, 9 failed`；9 个失败均是镜像内无
  `git` / 只读 checkout，不是产品断言失败。
- 原始证据：
  - `remote/c1c2c4-prose-daily-run-01/`
  - `remote/c1c2c4-prose-daily-run-02/`
  - `remote/container-binary-audit.json`
  - `remote/host-ring-audit.json`
  - `remote/live-contract-final-verified-profile.json`
  - `remote/parameter-consumption-matrix.json`
  - `remote/verified-profile-c16-short.json`
- 两轮 daily 的 `suite.json` 内嵌 `official_main_commit=1d5065b7`，这是运行前
  远端 metadata 未刷新留下的记录字段；实际运行镜像 ID、source-tree labels 和
  merge 身份均由本目录其他证据锁定为 `bbfe2618` 合并后的 x1112。该原始字段
  保留不改写，避免伪造运行记录。

## 复现

```sh
PYTHONPATH=/tmp/codex-pyyaml.5qao12 SYNC_SCOPE=engines bash spark/sync_to.sh
ssh chan@192.168.2.180 'cd /opt/spark && ./deploy.sh stop'
PYTHONPATH=/tmp/codex-pyyaml.5qao12 \
  bash spark/deploy.sh start --target=deepseek \
  --model deepseek-ai/DeepSeek-V4.1-Flash-1017
ssh chan@192.168.2.180 \
  'cd /opt/spark/deepseek && python3 experiments/2026-11-12-official-main-x1112/verify_live_container_contract.py'
```

prose daily 两轮：

```sh
ssh chan@192.168.2.180 'cd /opt/spark/deepseek && \
  SPARK_DS41F_API_KEY=deepseek python3 -m servebench suite \
  http://127.0.0.1:8888 \
  --suite experiments/2026-11-12-official-main-x1112/acceptance-c1-c2-c4-prose-daily.json \
  --out /opt/runtime_assets/deepseek/results/<new-run> \
  --label <new-label> \
  --metadata experiments/2026-11-12-official-main-x1112/metadata.json'
```

## 2026-11-13 后续同步

- official/main 已前进到 `2b9a6fb6`；新增内容不触达已部署
  vLLM/TileLang/sparknet/NCCL source lock，因此镜像 `x1112` 不重建。
- 官方同源码 1107 性能臂在本机仍为 c1 `78.29 tok/s`（官方 `82.62`），
  同协议 collective 几何平均慢约 `15.4%`，差异归到当前站点环网/
  宿主边界。
- 2593/3003 MHz clock 试验均未稳定复现收益，已恢复 sparkDash
  原始 2200 MHz。最终 208-prompt prose c1/c2/c4 为
  `69.53/103.57/142.17 tok/s`；官方 long-window c1 overall
  `76.97`、prose `72.6`，仍差 `6.68%`；最终 prefill 5 档全部达标。
- 记录与复现：
  `spark/deepseek/experiments/2026-11-13-clock-cap-decode/`。
