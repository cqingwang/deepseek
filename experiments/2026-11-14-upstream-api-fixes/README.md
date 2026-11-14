# 2026-11-14 上游 main 同步、Responses API 修复与 x1114

## 结论

- 同步 `offical/main` 到 `455d652c`，本地合并提交 `c5caed2d`。
- candidate-55 build 9 加上 Responses/图像占位符修复，构建为
  `spark-ds41f:x1114`
  `sha256:44c3157b64ee83aec0e15318ba33faec2f2b9b0b90738a5482c460abc79cd3cc`。
- 四机运行同一 image ID，API 200，`OOMKilled=false`、`RestartCount=0`，
  steady memguard active。
- 最终基础性能配方为官方 candidate-55 环境、无旧站点 runtime profile、
  GPU 时钟上限 `3003 MHz`，并把 `config.yaml/common` 的业务键正确映射到
  vLLM 原生参数。c1/c2/c4 prose 两轮均值为
  `78.267 / 111.483 / 151.683 tok/s`；c1 比官方 prose `77.8` 高 `0.60%`。
- 旧 `x1112` 镜像、26.28 GiB BuildKit cache、手工临时源码与结果副本已清理；
  保留 `x1114` 和 `spark-a` 上的 vLLM 基础镜像。

## API 修复

### 文本中的图像特殊 token

根因是 tokenizer 在任何字符串中看到 `<｜deepseek_image｜>` 就拒绝请求，
并且控制 token 保护逻辑刻意排除了图像占位符。修复后：

- 请求文本中的占位符按普通文本编码，不再产生 image special token ID。
- 只有结构化 `image_url`/`input_image`/`image_pil` 内容块会生成 image
  special token。
- string content、text content block、tool message、reasoning 和 assistant
  message 都走同一保护路径。

### Responses custom tools

请求 schema 原本能接受 `custom_tool_call`，400 发生在转换为 Chat 历史的
`_construct_message_from_response_item`：它只认识 `function_call_output`。
修复后：

- `custom_tool_call` 映射为 assistant tool call，free-form `input` 保留为
  arguments。
- `custom_tool_call_output` 映射为带同一 `call_id` 的 tool message。
- 既有 function call/output 行为未变。

### 红绿证据

- RED：`red-source.txt` 复现
  `Unsupported input item type: custom_tool_call` 和
  `Message content contains image special token`。
- GREEN：`green-source.txt` 中同一输入全部通过。
- 镜像内 focused tests：`172 passed`。
- vLLM 直连：普通 message、图像 token 文本、function call/output、
  custom tool call/output、真实 image content block 全部 **200**；
  原始记录见 `validation/live-api.jsonl`。
- Magpie `focused`：custom tool call/output **200**，见
  `validation/magpie-focused.jsonl`。
- 最终 stop/start 后复跑入口：
  `run_api_regressions.py --direct-base ... --direct-key ... --magpie-base
  ... --magpie-key ... --out-dir ...`；最终原始记录为
  `validation/live-api-after-mapping.jsonl` 与
  `validation/magpie-focused-after-mapping.jsonl`。

补丁：
`../2026-10-26-spark-ds41f-candidate/candidate-55/vllm/0811-fix-dsv41-treat-typed-image-placeholders-as-text-and-repl.patch`

## 构建与部署

- 构建：`build/receipt.json`，6 compile jobs，675 秒，最低
  `MemAvailable=82.2 GiB`。
- vLLM patch head：`ebd8fcb65f73413eb795f2f10366310f7ef46188`
- vLLM tree：`5ce5f718f24f5842ea389095c599e278db2ccf76`
- CUDA：`13.5.37`；NCCL：`2.30.7-1`；TileLang：
  `741806c7357102130a149cdcae2a5f22678eab67`；sparknet：
  `e06f6a0675cb66638231d2c68948a2b315b69b07`。
- 镜像只从 spark-a 构建一次，使用 `docker save | ssh docker load`
  沿物理高速环 `A→B→C→D` 分发，每跳核对 image ID。
- 每次启动前执行 `deploy.sh stop`，确认四机 target 容器和 GPU compute
  均为 0；启动后核对四机 image ID、运行状态、OOM、restart、memguard、
  API 和 119 个容器 env。

## 参数消费

`validation/parameter-consumption.json` 是 live 容器 inspect 的直接记录：

- `common.max_request=9 -> --max-num-seqs 9`
- `common.max_token=1000000 -> --max-model-len 1000000`
- `common.max_tokens=65536 -> --override-generation-config.max_new_tokens 65536`
- `common.max_num_batched_tokens=16384 -> --max-num-batched-tokens 16384`
- `engines.deepseek.num_speculative_tokens=5 -> speculative
  num_speculative_tokens=5`
  （官方 main `drafts=5`，README 明确为 five draft tokens；不是 anchor+drafts
  的总 token 数 6。该值属于 target，不放在 common）
- `common.default_thinking=false /
  reasoning_effort=75 -> --default-chat-template-kwargs`
- `common.master_port=25000 -> --master-port 25000`
- `common.api_url -> --host 0.0.0.0 --port 8888`，API key 由 `--api-key` 消费
- 由 `max_request x (1 + drafts) = 9 x (1 + 5)` 重算 rows=54：
  `--long-prefill-token-threshold 16330`、
  `--max-cudagraph-capture-size 54`，capture ladder 末尾也是 54
- `--max-parallel-prefills 1`
- `CUDA_DEVICE_MAX_CONNECTIONS=32`
- NCCL `MAX=MIN=4`
- `SPARKNET_ROCE_ALLGATHER_MAX_BYTES=2097152`
- `SPARKNET_ROCE_MAX_COLLECTIVE_BYTES=2097152`
- L2 prefetch `15/12/12 MB`，`17-32=10`
- `OMP_NUM_THREADS=8`
- `NCCL_IB_SUBNET_PREFIX_LEN=30`
- `NCCL_IB_GID_INDEX=3`

`max_request` 等键是 `config.yaml` 的业务配置语义，不是 vLLM 的原生 env 名。
`program.py` 只把它们输出为 `DS41F_*` 宿主编排键，`start-tp4.sh` 再翻译成
vLLM CLI；live 容器 119 个 env 中业务键零泄漏。vLLM 启动日志的
`non-default args` 与实际 config 进一步证明 `max_num_seqs=9`、
`max_model_len=1000000`、`max_new_tokens=65536`、`master_port=25000` 和
chat-template 默认值均被 parser 消费。

启动日志还会显示 copy-drafts 把内部 token-row 预算从 16384 扩到 16395、
CUDA graph capture range 从 54 扩到 65。这是 vLLM 的调度/捕获行预算，
不是请求并发上限；请求并发仍由 `max_num_seqs=9` 限制。

## 最终性能

运行 profile 对比均在同一 `x1114` 上短窗口筛选：

- 旧 CUDA64/NCCL8/8MiB profile 相对官方 candidate 环境没有收益，拒绝。
- GPU cap 2593 和 3003 都优于 2200；3003 的 c16 最好，最终选择硬件上限。
- 复现命令：`./apply-gpu-clock-limit.sh 3003`。
- 正确映射 `max_request=9` 后重新跑两轮正式 c1/c2/c4 prose：

| 并发 | run 01 | run 02 | 两轮均值 | 相对 x1112 |
| ---: | ---: | ---: | ---: | ---: |
| c1 | 78.133 | 78.400 | **78.267** | **+12.37%** |
| c2 | 111.133 | 111.833 | **111.483** | **+7.12%** |
| c4 | 151.533 | 151.833 | **151.683** | **+6.69%** |

官方 prose c1 为 `77.8 tok/s`，本轮高 `0.60%`。正确映射前后 c1/c2/c4
分别变化 `-0.38% / -0.10% / +0.08%`，在短窗口噪声内。汇总见
`performance/summary-after-mapping.json`，两轮原始 JSON 分别在
`performance/after-mapping-run-01/` 和 `performance/after-mapping-run-02/`。

2026-11-15 又把旧业务键 `mtp_num_tokens: 6` 改为 target 原生
`engines.deepseek.num_speculative_tokens: 5`。实际 vLLM 参数仍然是 5，
rows/长 prefill 预算也没有变化，因此没有重跑性能；这是配置归属和命名修正，
不是有效运行参数变化。

先前 2200 MHz 官方默认环境的 c1/c8/c16 和 prefill 原始结果仍保留在
`performance/run-01/`，作为参数筛选的失败/对照证据；不作为最终配方结果。

## 环网与清理

- `validation/host-ring-audit-after-mapping.json`：`ok=true`，共享 `spark-ring-mesh`
  的 route、TC、GID、MTU、service 和脚本 hash 全部匹配。
- `validation/rdma-counter-delta-after-mapping.json`：四机各 8/8 个 RoCE 收发方向
  在一次 chat 后均正增长。
- `validation/deployment-record.json`：最终同步源码哈希、stop 前零残留、start
  后四机运行态、参数映射、API/perf/ring 验收的一站式可复现记录。
- `validation/cleanup.json`：删除四机 `x1112` 和 spark-a BuildKit
  cache `26.28 GiB`；保留 `x1114` 与基础镜像
  `vllm/vllm-openai:nightly-af1c0149...`。

## 边界

- 本窗口运行了 tokenizer/Responses focused tests `172 passed`、三份父级
  部署合同 `21 passed`、镜像 GPU/TileLang/sparknet smoke；没有重跑官方
  10k 级完整 release test suite。
- 图像修复恢复的是文本语义；真正的多图请求仍受官方
  `--limit-mm-per-prompt {"image":16}` 限制。
