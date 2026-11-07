# Runtime prefill tuning on the site's TP4 ring

Owner request, 2026-11-07: raise the site's cold-prefill rate without reducing
c1-c4 decode beyond boot noise, using local source synchronized to `spark-a`
and at most ten `deploy.sh stop` / `deploy.sh start --target=deepseek`
deployments. Runtime-only arms do not rebuild or redistribute the image.

## Protocol

- Image: `spark-ds41f:1030`,
  `sha256:84a8b73d0993200044b5eceb0892342d34a787416b7ae91e4b38f42fffc63e41`.
- Shape: `tp4-ring`, four ranks, 1,048,576-token model limit, 16 sequences,
  10.5 GiB KV pool.
- Client: `servebench` on `spark-a` against `http://127.0.0.1:8888`, model
  `deepseek-v4.1-flash`, `temperature=0`, `thinking=false`.
- Decode: closed loop at concurrency 1 and 4, 60-second measured windows in six
  batches, 128 forced output tokens.
- Prefill: cold source-text prompts at 32K and 131K, three repeats each.
- Every deployment first removes all four ranks with `deploy.sh stop`, then
  starts them with `deploy.sh start --target=deepseek`. The launcher's startup
  and steady memory guards remain enabled.
- Acceptance: prefill improvement beyond the arm's own variability; c1/c4
  within about 3%; no OOM, restart, Xid, failed guard, or silent fallback.

## Baseline, run 00

| Measure | Value |
| --- | ---: |
| 32K cold prefill, tok/s | 6,657.20 (n=3; 95% interval 0.31%) |
| 131K cold prefill, tok/s | 6,718.18 (n=3; 95% interval 0.20%) |
| c1 decode, output tok/s | 71.37 |
| c4 decode, output tok/s | 143.92 |
| failed requests | 0 |

Raw client results and container inputs are in
[`runs/run-00-baseline/`](runs/run-00-baseline/).

## Deployment ledger

| Run | Runtime change | 32K tok/s | 131K tok/s | c1 tok/s | c4 tok/s | Verdict |
| ---: | --- | ---: | ---: | ---: | ---: | --- |
| 00 | production: 16,384 batch / 16,288 threshold | 6,657.20 | 6,718.18 | 71.37 | 143.92 | baseline |
| 01 | 32,768 batch / 32,672 threshold | 6,869.41 | 6,923.80 | 71.10 | 143.93 | positive: +3.19% / +3.06%; decode unchanged |
| 02 | 49,152 batch / 49,056 threshold | 6,926.41 | 6,907.60 | 70.68 | 143.62 | positive: +4.04% / +2.82%; 32K best so far |
| 03 | 65,536 batch / 65,440 threshold | — | — | — | — | failed before readiness: TileLang SwiGLU grid y=65,536 exceeds CUDA's 65,535 limit; launcher rolled back |
| 04 | 65,024 batch / 64,928 threshold | 6,901.66 | 6,860.22 | 71.28 | 142.83 | positive vs baseline, but below 48K; 48K retained |
| 05 | 48K + `NCCL_IB_QPS_PER_CONNECTION=4` | 6,827.89 | 6,825.63 | 70.98 | 143.10 | negative vs 48K; rejected |
| 06 | 48K + `SPARKNET_ROCE_ALLGATHER_MAX_BYTES=8388608` | 6,940.28 | 6,924.39 | 71.62 | 143.23 | small positive: +0.20% / +0.24%; retained |
| 07 | run 06 + `--enable-bf16x3-router-gemm` | 6,971.88 | 6,939.59 | 71.37 | 142.62 | +0.46% / +0.22%, below the decision floor; matched-setting outputs 6/6 identical, not retained |
| 08 | run 06 + `NCCL_BUFFSIZE=8388608` | 6,861.70 | 6,803.24 | 71.28 | 142.75 | negative: -1.13% / -1.75%; rejected |
| 09 | final: 48K + 8 MiB all-gather, 4 MiB NCCL buffer | 6,920.20 | 6,880.32 | 71.33 | 142.63 | final confirmation; 32K repeat +3.95%, 131K +2.41%, 1M +2.36% |

Each positive arm's exact reproducible startup parameters are recorded under
[`arms/`](arms/). Unsuccessful arms remain in this table and do not replace the
retained best.

The 64K failure is preserved in
[`runs/run-03-b65536-failed/`](runs/run-03-b65536-failed/), including all four
rank logs. It is a kernel launch-geometry limit, not host or GPU memory
exhaustion.

Run 05's first start was rejected before container creation because the
persistent generated `.env.deepseek` predated the new fixed template key. The
generated file was backed up, removed, and regenerated from the authoritative
template; the measured start then carried `NCCL_IB_QPS_PER_CONNECTION=4`.

## Final configuration and verdict

Retained runtime configuration:

- `--max-num-batched-tokens 49152`
- `--long-prefill-token-threshold 49056`
- `SPARKNET_ROCE_ALLGATHER_MAX_BYTES=8388608`
- all other production arguments and environment values unchanged

Final c1/c2/c3/c4 decode throughput was
`71.33 / 104.90 / 126.45 / 142.63 output tok/s`. Against the baseline's c1 and
c4, that is `-0.05%` and `-0.89%`, inside the observed boot noise.

Final prefill:

| Size | Final confirmation | Best same-config run | Baseline | Confirmed change |
| ---: | ---: | ---: | ---: | ---: |
| 32K | 6,920.20 tok/s | 6,940.28 | 6,657.20 | +3.95% |
| 131K | 6,880.32 tok/s | 6,924.39 | 6,718.18 | +2.41% |
| 1M (`1,040,000` requested) | 5,076.45 tok/s | — | 4,959.28 | +2.36% |

The model suite produced the exact baseline `outputs_sha256`
`6cf26aedc93c0c01bf81ed4dac8a67e77043222719930977e034aba70a9ff99a`,
perplexity `1.1178292269357697`, rank-1 fraction `1.0`, and zero failed
requests. Post-run audit: four ranks `running`, `OOMKilled=false`, restart
count 0, one image ID, active memguards, both served names, zero failed units,
and no matching Xid/NVRM/OOM/fault kernel lines.

The requested +2,000 tok/s (for example 6,000 -> 8,000) was **not reached**.
Runtime-only tuning reached about +4% on the 32K point. The measured ceiling is
consistent with the fixed per-chunk cost: 16K -> 32K -> 48K buys progressively
less, 65,024 is slower, and 65,536 fails because the TileLang SwiGLU launch
uses `grid.y=65536`, one above CUDA's limit. Further progress requires a binary
or kernel change (for example a grid-safe activation path, cheaper routed-MoE
weight streaming, or lower per-chunk collective cost), not more container
environment tuning.

Deployment budget: ten validations were used, including one start rejected
before container creation by the generated-env contract. No image build or
`docker save/load` was needed because every retained change is runtime-only.

The shared maintenance hold was archived at
`2026-11-07T16:23:19.741138Z` as
`~/spark-hold.deepseek-prefill-tuning-20261107.closed.json`; the active hold
and pending request are absent. Authentication still returns HTTP 200 and
spark-a reports zero failed units.

## Best-state restoration verification

Owner asked on 2026-11-08 to restore the best recorded deployment. A read-only
four-rank comparison found the running containers already exactly match the
retained best arm: `49,152 / 49,056`, 8 MiB all-gather, 4 MiB NCCL buffer, no
QPS override, no router-x3, and the pinned image ID on every rank. A new
`stop`/`start` was deliberately not performed: it would be an unnecessary
service interruption and an eleventh deployment after the owner's ten-round
cap. All four ranks are `running`, `OOMKilled=false`, restart count 0, with
active memguards; `/v1/models` is HTTP 200 and no hold/request is active.

## Remaining host-clock headroom, not changed

A read-only 32K prefill sample was taken on all four ranks under 96% GPU
utilization. Every rank ran at `2,177-2,190 MHz` while the application clock
was `2,418 MHz` and the reported maximum was `3,003 MHz`; loaded power stayed
at about `37-41 W`, temperature at `39-47 C`, and no clock-event throttle was
reported. This leaves a measurable host-level clock gap that was **not**
changed because the request's retained tuning target is container startup
parameters and the 10-deployment budget is already exhausted. Raw samples and
the summary are in [`runs/run-09-final-best/clock-gap/`](runs/run-09-final-best/clock-gap/).
