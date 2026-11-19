# 2026-11-19 copy-drafts structured-output bitmask 容量修复

## 现场

- `spark-a` 的 `deepseek-r0` 于 `2026-11-19T09:47:00Z` 以 exit 1 停止。
- 容器状态为 `OOMKilled=false`、`RestartCount=0`；宿主未见 OOM/Xid，
  memguard 无 kill 记录。
- 首发致命错误：

```text
xgrammar.matcher.GrammarMatcher.fill_next_token_bitmask
RuntimeError: [09:46:59] /project/cpp/grammar_matcher.cc:182:
Check failed: (index >= 0 && index < token_bitmask.shape[0]) is false:
The provided index is out of bounds
```

## 根因

candidate-61 的 copy-drafts 把单步 decode 行数扩展到
`vllm_config.max_decode_rows`。当前站点参数下：

```text
max_num_seqs=5
num_speculative_tokens=5
num_verification_tokens=16
uniform_decode_query_len=6
max_decode_rows=5*6+16-5=41
```

`StructuredOutputManager` 仍按旧的
`max_num_seqs * (1 + num_speculative_tokens)=30` 分配 grammar bitmask。
当两路结构化输出请求拿到更宽的 copy-draft 行时，累计行号越过 30，
`xgrammar` 越界并使 EngineCore 致命退出。

## 修复

- candidate-61 补丁序列新增
  `0824-fix-structured-output-copy-draft-bitmask-capacity.patch`。
- grammar bitmask 分配改为 `vllm_config.max_decode_rows`，与 scheduler
  实际允许的 decode 行 envelope 使用同一真实源。
- 当 copy-drafts 不生效时，`max_decode_rows` 仍等于原来的 uniform 宽度，
  不扩大普通请求的行数，也不改变采样语义。

## official main 合并对齐

- 远端 `offical/main` 已更新到 `83fb21de`，其 merge base 为 `d719bd61`；
  合并后没有 `UU`/未合并路径或冲突标记。
- 远端同时更新了 candidate-61 的 0819..0822 补丁与 vLLM 锁。0824 已接在
  更新后的序列末尾，`build prepare --only vllm --lock` 成功：

```text
patch_head=9625f7f24876cdae68d35ebabb6914f65e39812e
tree=259cf7edee8961f40e89b48002ae7394ebca49e5
patchset_sha256=3be9c8f7ce1e53a4531e665f3a9b09d8a8d832ff3f9a10f0eb75d401bed35a74
patch_count=697
```

- source manifest、upstream lock 与所有 shape profile 的 tree label 已同步到
  上述结果；本轮不使用旧 `x1118/bb5439` 作为最终镜像来源。

- 回归脚本在旧 `x1118` 上以 `bitmask index 30 is outside 30 rows`
  RED；把新树中的 `structured_output/__init__.py` 只读覆盖进同一镜像后
  GREEN。见 `validation/bitmask-regression-{red,green}.txt`。

## 回归

最小场景为两路 16-token copy-draft 请求加一路 5-token 普通请求：

```text
rows = (16 + 1) + (16 + 1) + (5 + 1) = 40
```

旧 30 行分配必须在第 31 行失败；新 41 行分配必须通过并返回 40 行。

## 验证入口

```bash
docker run --rm --network none \
  -v "$PWD/tests/v1/structured_output/test_copy_draft_bitmask_capacity.py:/test.py:ro" \
  --entrypoint python3 spark-ds41f:x1118 /test.py

docker run --rm --network none \
  -v "$PWD/tests/v1/structured_output/test_copy_draft_bitmask_capacity.py:/test.py:ro" \
  --entrypoint python3 <含-0824-的新镜像> /test.py
```

RED/GREEN 原始输出、镜像身份、四机容器状态和并发 API 结果保存在
`validation/`。
