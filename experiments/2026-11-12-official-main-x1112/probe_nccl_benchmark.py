#!/usr/bin/env python3
"""Benchmark vLLM TP collectives with the sparknet one-shot path disabled."""

from __future__ import annotations

import argparse
import json
from datetime import timedelta

import torch
import torch.distributed as dist
from vllm.config import VllmConfig, set_current_vllm_config
from vllm.distributed.parallel_state import (
    destroy_distributed_environment,
    destroy_model_parallel,
    get_tp_group,
    graph_capture,
    init_distributed_environment,
    initialize_model_parallel,
    set_custom_all_reduce,
)


def packed_shape(elements: int, itemsize: int) -> tuple[int, int]:
    width = 5120 if elements % 5120 == 0 else 16 // itemsize
    return elements // width, width


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rank", type=int, required=True)
    parser.add_argument("--world-size", type=int, default=4)
    parser.add_argument("--master-addr", required=True)
    parser.add_argument("--master-port", type=int, required=True)
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--counter-samples", action="store_true")
    parser.add_argument("--numerics", action="store_true")
    parser.add_argument("--lengths", type=int, nargs="+", default=[5120, 30720, 245760, 1048576])
    args = parser.parse_args()

    torch.cuda.set_device(0)
    set_custom_all_reduce(False)
    init_distributed_environment(
        world_size=args.world_size,
        rank=args.rank,
        local_rank=0,
        distributed_init_method=f"tcp://{args.master_addr}:{args.master_port}",
        timeout=timedelta(seconds=90),
    )
    with set_current_vllm_config(VllmConfig()):
        initialize_model_parallel(tensor_model_parallel_size=args.world_size)
    group = get_tp_group()
    if group.device_communicator.b12x_ar_comm is not None:
        raise RuntimeError("NCCL benchmark unexpectedly initialized the one-shot communicator")

    timings = []
    for dtype in (torch.bfloat16, torch.float32):
        for length in args.lengths:
            local = torch.arange(length, device="cuda", dtype=dtype).reshape(
                packed_shape(length, dtype.itemsize)
            )
            scattered = torch.cat([local + 8 * rank for rank in range(args.world_size)])
            for name, operation in {
                "all_reduce": lambda: group.all_reduce(local),
                "all_gather": lambda: group.all_gather(local, dim=0),
                "reduce_scatter": lambda: group.reduce_scatter(scattered, dim=0),
            }.items():
                operation()
                torch.cuda.synchronize()
                graph = torch.cuda.CUDAGraph()
                with graph_capture(device=torch.device("cuda:0")) as context:
                    with torch.cuda.graph(graph, stream=context.stream):
                        outputs = [operation() for _ in range(16)]
                for _ in range(4):
                    graph.replay()
                torch.cuda.synchronize()
                samples = []
                for _ in range(5):
                    dist.barrier(group=group.cpu_group)
                    start = torch.cuda.Event(enable_timing=True)
                    end = torch.cuda.Event(enable_timing=True)
                    start.record()
                    for _ in range(16):
                        graph.replay()
                    end.record()
                    end.synchronize()
                    samples.append(start.elapsed_time(end) * 1000 / 256)
                timings.append(
                    {
                        "dtype": str(dtype),
                        "elements_per_rank": length,
                        "operation": name,
                        "microseconds_per_call": samples,
                        "expected_backend": "nccl",
                    }
                )
                print(
                    json.dumps(
                        {
                            "rank": args.rank,
                            "timing": timings[-1],
                        }
                    ),
                    flush=True,
                )
                del graph, outputs
            del local, scattered

    print(
        json.dumps(
            {
                "rank": args.rank,
                "world_size": args.world_size,
                "transport": "nccl",
                "proxy": None,
                "passed": True,
                "checks": [],
                "numerical_checks": [],
                "timings": timings,
            }
        ),
        flush=True,
    )
    destroy_model_parallel()
    destroy_distributed_environment()


if __name__ == "__main__":
    main()
