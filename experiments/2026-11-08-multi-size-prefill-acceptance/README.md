# Multi-size prefill acceptance, round 2

Owner request, 2026-11-08: continue from the previous runtime-tuning evidence
and spend up to ten additional deployment validations using a multi-size
prefill criterion. The prior round's 32K-only best is not the acceptance rule
here.

## Primary score

Cold prefill is measured at:

`8192, 16384, 32768, 65536, 131072, 262144` requested prompt tokens.

For each arm, the primary score is the arithmetic mean of the six `tok/s`
values. The cumulative sum is also recorded; it is six times the mean, so both
rank arms identically. The target is an increase of about `2,000 tok/s` in this
mean over the restored initial 16K production baseline.

## Gates

- The image remains `spark-ds41f:1030`,
  `sha256:84a8b73d0993200044b5eceb0892342d34a787416b7ae91e4b38f42fffc63e41`.
- Every deployment uses `deploy.sh stop` first and then
  `deploy.sh start --target=deepseek`.
- Runtime-only changes do not rebuild the image or transfer a new image.
- c1 and c4 closed-loop decode must stay within about 3% of the baseline.
- Four ranks must remain running, `OOMKilled=false`, restart count 0, memguard
  active, API HTTP 200, and no Xid/NVRM/OOM/fault kernel evidence.
- The final arm must preserve the baseline model output SHA-256.

## Baseline and deployment ledger

The initial baseline is the original 16,384-token production recipe restored
from the pinned configuration. The ten-deployment ledger and raw results are
filled in as each arm completes.

| Deployment | Runtime change | Six-size mean | Six-size sum | c1/c4 | Verdict |
| ---: | --- | ---: | ---: | --- | --- |
| 01 | initial 16,384 / 16,288, all-gather 2 MiB | 6,466.45 | 38,798.73 | 69.20 / 139.93 | baseline |
| 02 | 49,152 / 49,056, all-gather 8 MiB | 6,660.31 | 39,961.84 | 69.00 / 139.07 | +3.00% mean; retained best |
| 03 | 65,520 / 65,424, all-gather 8 MiB | 6,720.29 | 40,321.73 | 69.07 / 139.87 | +3.93% mean; retained best |
| 04 | 65,535 / 65,439, all-gather 8 MiB | 6,712.80 | 40,276.79 | 68.87 / 139.83 | -0.11% vs 03; not retained |
| 05 | 65,520 + router-x3 GEMM, all-gather 8 MiB | 6,706.50 | 40,238.99 | 69.17 / 139.30 | -0.21% vs 03; not retained |
| 06 | 65,520 + all-gather 16 MiB | 6,649.46 | 39,896.76 | 68.87 / 139.83 | -1.05% vs 03; rejected |
| 07 | 65,520 + 8 NCCL channels + all-gather 8 MiB | 6,735.74 | 40,414.46 | 69.00 / 138.63 | +4.16% vs baseline; retained best |
| 08 | run 07 + `CUDA_DEVICE_MAX_CONNECTIONS=64` | 6,747.40 | 40,484.39 | 69.07 / 139.83 | +4.34% vs baseline; retained best |
| 09 | run 08 + `NCCL_IB_QPS_PER_CONNECTION=2` | 6,788.80 | 40,732.83 | 68.97 / 139.47 | +4.98% vs baseline; retained best |
| 10 | same as 09, final clean boot | 6,776.66 | 40,659.95 | 68.77 / 139.07 | -0.18% vs 09; final config retained |

Raw results: [`runs/`](runs/). Reproducible positive arms: [`arms/`](arms/).
Machine-readable ranking: [`scores.json`](scores.json).

## Final comparison

The highest six-size mean in the round is deployment **09**:
`6,788.80 tok/s`, or **+322.35 tok/s (+4.98%)** over the restored 16K
baseline. Deployment 10 uses the same configuration and confirms
`6,776.66 tok/s`, a `-0.18%` boot-to-boot difference.

| Size | Baseline 16K | Final configuration | Change |
| ---: | ---: | ---: | ---: |
| 8K | 5,792.54 | 5,894.64 | +1.76% |
| 16K | 6,492.55 | 6,634.30 | +2.18% |
| 32K | 6,685.22 | 7,116.42 | +6.45% |
| 64K | 6,760.44 | 7,183.62 | +6.26% |
| 128K | 6,656.44 | 7,096.62 | +6.61% |
| 256K | 6,411.53 | 6,807.22 | +6.17% |
| **Mean** | **6,466.45** | **6,788.80** | **+4.98%** |

The final model suite retained the exact baseline output SHA-256
`6cf26aedc93c0c01bf81ed4dac8a67e77043222719930977e034aba70a9ff99a`,
perplexity `1.1178292269357697`, rank-1 fraction `1.0`, and zero failed
requests. The final 1M cold prefill was `5,183.95 tok/s` with no failure.

The requested `+2,000 tok/s` mean increase was **not reached**. The measured
runtime-parameter ceiling is about `+322 tok/s` on the six-size mean. Further
progress needs an image/kernel change, not another runtime-only sweep.

Final retained runtime:

```text
--max-num-batched-tokens 65520
--long-prefill-token-threshold 65424
SPARKNET_ROCE_ALLGATHER_MAX_BYTES=8388608
NCCL_MAX_NCHANNELS=8
NCCL_MIN_NCHANNELS=8
NCCL_BUFFSIZE=4194304
NCCL_IB_QPS_PER_CONNECTION=2
CUDA_DEVICE_MAX_CONNECTIONS=64
```

The maintenance hold was archived as
`~/spark-hold.deepseek-multisize-prefill-20261108.closed.json`; the active hold
and pending request are absent. The final deployment is the retained D10
runtime state.
