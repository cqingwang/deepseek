# x1128 production deployment

Date: 2026-11-28

Status: **deployed with the best tested official tp4-ring recipe;
functional/loop/output-budget gates PASS; official-suite performance remains
below the 99% floor and is recorded as HOLD**

- Source: official `main@5386c71b`, candidate-77.
- Production image: `spark-ds41f:x1128`,
  `sha256:001305bdec0a5133b9112827ae0b730fc407cbdb13e6abc1b8cfc8c7c7c26e0e`.
- Production profile: `experiments/2026-11-28-x1128/tp4-ring.json`.
- Recipe: 32 requests, 1,048,576 context, 16,384 batched tokens,
  17,716,740,096-byte KV pool, 192-row CUDA graphs, `max_new_tokens=65536`,
  `default_thinking=false`, `reasoning_effort=50`.
- Functional gates passed: API, `tool_gate` 80/80, determinism, loop detector,
  loop release test 40/40, and Responses API `max_output_tokens=65536`.
- Full 174-file release tests completed: `11,796 passed / 641 skipped /
  1 failed`. The only failure is the site-wide display-carveout RM scanout
  allocation; x1126 fails the same test on all four nodes.
- The best complete official-suite run is `boot-a`. Poisson is level, but
  single-stream c1, release c8/c16, sampled c1/c8/c16 and prefill are
  `2.9%–4.3%` below the official references.
- The same-site x1126 baseline is not faster: its diagnostic `prose-c1`
  decode is `82.175` versus x1128 `86.137`, and its source-text prefill is
  effectively level. The remaining gap is therefore a site baseline, not an
  x1128 regression.
- The clean-reboot `tp3-triangle` screen is N/A for this site: the physical
  cabling is the four-node a→b→c→d→a ring with no direct three-node triangle.
- Official references are pinned in
  `performance/official-suite-reference.json`; the selected run and every
  floor comparison are in `performance/summary-final.json`.
- GPU clocks are restored to `2400,3003`.

All deployment, performance, loop, output-budget and frequency evidence is
under this directory.
