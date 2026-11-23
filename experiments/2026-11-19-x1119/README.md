# 2026-11-19 `spark-ds41f:x1119`

## 范围

- 来源：`offical/main` merge `83fb21de`，合并后保留站点补丁
  `0823`（Responses custom-tool 历史回放）与 `0824`
  （structured-output copy-draft bitmask 容量）。
- vLLM：697 项补丁，patch head
  `9625f7f24876cdae68d35ebabb6914f65e39812e`，tree
  `259cf7edee8961f40e89b48002ae7394ebca49e5`。
- 站点 overlay：`experiments/2026-11-19-x1119/vllm/series`，对应锁
  `experiments/2026-11-19-x1119/upstreams.lock.json`；共享
  `experiments/2026-11-16-c61-decode/vllm/series` 保持官方
  `0001..0822`。
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
   experiments/2026-11-19-x1119/tp4-ring.json build prepare`
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

### 镜像与部署

- 镜像：`spark-ds41f:x1119` =
  `sha256:4f0efd618b15afcb07579c5118675726da73eb3e5fa967c38062a49ed909ea3b`。
- 构建：`644s`，8 个编译 job，最低 `MemAvailable=81.8 GiB`；
  GPU import、TileLang import 和 sparknet proxy ABI smoke 均通过。
- 分发：A→B→C→D 光纤相邻跳，四机 image ID、vLLM tree 和 patch head
  完全一致。
- 部署：正式 `deploy.sh stop` 后零容器/零 GPU compute，再
  `deploy.sh start --target=deepseek`；四机 running、OOMKilled=false、
  RestartCount=0、steady memguard active。
- live contract：90 个受管容器 env，78 个 serve 参数；
  `max_num_seqs=5`、1,000,000 上下文、5,374,973,440-byte KV、
  67 图、5 drafts、per-request metrics 均被容器和启动日志实际消费。

### API 与回归

- 0823 Responses custom-tool 回归：7/7 为 200。
- 0824 结构化输出并发：3 路 × 8 波，共 24/24 成功，96.4 秒；
  service 无 `EngineCore encountered a fatal error`，无 xgrammar 越界。
- 旧镜像 RED：`bitmask index 30 is outside 30 rows`；新镜像同一测试
  GREEN。

### 性能（两轮均值）

| Suite | Point | Run 1 | Run 2 | Mean | Official | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| decode | prose c1 | 90.0 | 89.7 | **89.8** | 59.1 | PASS |
| decode | prose c2 | 159.5 | 157.2 | **158.4** | 84.8 | PASS |
| decode | prose c4 | 281.5 | 288.5 | **285.0** | 124.9 | PASS |
| decode | code c1 | 134.6 | 136.3 | **135.5** | 80.6 | PASS |
| decode | code c2 | 190.7 | 188.6 | **189.7** | 118.2 | PASS |
| decode | code c4 | 361.8 | 382.7 | **372.2** | 165.1 | PASS |
| prefill | 32K | 9305.3 | 9460.7 | **9383.0** | 8508.0 | PASS |
| prefill | 131K | 9829.2 | 9772.6 | **9800.9** | 8657.0 | PASS |
| prefill | 262K | 9819.4 | 9770.3 | **9794.9** | 8261.0 | PASS |
| prefill | 524K | 9591.7 | 9567.1 | **9579.4** | 7460.0 | PASS |
| prefill | 1M | 9213.0 | 9165.6 | **9189.3** | 6012.0 | PASS |

- 两轮 quality gate 均为 5/5；汇总 `performance/summary.json` 为
  `ok=true`，失败项 0。
- 原始记录：`performance/run-01/bench.json`、`performance/run-02/bench.json`。
- 部署记录：`deployment-record.json`。

### 已知告警

- 五条持有内核包的实际版本与 pin 完全一致；`dpkg` 状态是
  `hold ok installed`，旧解析器误报为包漂移，现已修复并四机 PASS。
- fan-floor 控制属于 bench-only 能力，`v0.1.4` 与当前 EC
  `0x03000d07` 不兼容。x1119 显式设置 `host.fan_control=false`，
  serving 不受影响，benchmark 自动跳过不可用的 fan curve。
- 性能测试中 thermal slowdown 均为 0 ms，GPU 最高温度低于限制。

### 清理收尾

- 四机删除已无容器引用的 `spark-ds41f:x1118`；当前只保留服务镜像
  `spark-ds41f:x1119`，spark-a 另保留唯一 vLLM 构建基座。
- spark-a Docker Build Cache 从 `9.96 GiB` 清理到 `0`；根分区实际释放
  `6.092 GiB`，spark-b/c/d 分别释放约 `1 MiB`、`1 MiB`、`4.906 GiB`。
- 删除 spark-a 的 `1.4 GiB` 合并检查临时树、x1119 bench/test 中间目录、
  一次性补丁和 gate 文件、失效 build lock，以及四机旧临时探针和
  `/var/tmp` 快照。
- 本地删除 5 份大体积 sync transcript、已失败的 `build-image.txt` 和
  一次性分发脚本；性能、API、host 检查、构建重试与回归证据全部保留。
- 清理后四容器仍为 `running`、`OOMKilled=false`、`RestartCount=0`，
  GPU 上四个 `VLLM::Worker_TP0..3` 正常；`/v1/models` 与 chat completion
  均为 200，per-request metrics 可见。
- 证据与删除清单：`validation/cleanup-receipt.json`、
  `validation/cleanup-before.txt`、`validation/cleanup-delete.txt`、
  `validation/cleanup-after.txt`、`validation/cleanup-local.txt`。
