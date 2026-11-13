#!/usr/bin/env python3
"""Compare launcher intent with the four live containers' consumed argv/env."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path


ROOT = Path("/opt/spark/deepseek")
ENV_FILE = ROOT / ".env.deepseek"
LAUNCHER = ROOT / "start-tp4.sh"
IMAGE_TAG = "spark-ds41f:x1112"
IMAGE_ID = "sha256:cbf38e34dab0e2aafa2abea79e669469027d253c70248d4e9a54182878eb2ece"

HOST_ONLY_KEYS = {
    "TARGET_ENGINE_ID",
    "TARGET_CONTAINER_PREFIX",
    "RUNTIME_ASSETS",
    "CACHE_DIR",
    "MODEL_DIR",
    "MODEL_CONTAINER_PATH",
    "IMAGE",
    "IMAGE_ID",
    "API_HOST",
    "PORT",
    "HEAD_MANAGEMENT_IP",
    "HEAD_SSH_TARGET",
    "WORKER_MANAGEMENT_IPS",
    "WORKER_SSH_TARGETS",
    "WORKER_RANKS",
    "NNODES",
    "TP_SIZE",
    "API_KEY",
    "DS41F_ALLOW_UNVERIFIED_IMAGE",
    "DS41F_CONFIG_FILE",
    "DS41F_RUN_PROFILE",
    "SPARKNET_ROCE_PEER_HCAS_BY_RANK",
}


def run(command: list[str], *, host: str | None = None) -> str:
    if host:
        command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", f"chan@{host}", *command]
    return subprocess.check_output(command, text=True)


def expected_commands() -> dict[int, list[str]]:
    output = run(
        ["bash", str(LAUNCHER), "print-env"],
        host=None,
    )
    parts = re.split(r"^== rank ([0-3]) ==$", output, flags=re.MULTILINE)
    if len(parts) != 9:
        raise SystemExit(f"unexpected print-env output:\n{output}")
    return {
        int(parts[index]): shlex.split(parts[index + 1].strip())
        for index in range(1, len(parts), 2)
    }


def split_command(tokens: list[str]) -> tuple[str, dict[str, str], list[str]]:
    if not (tokens[:3] == ["docker", "run", "-d"] and tokens[3] == "--name"):
        raise SystemExit(f"unexpected docker command: {tokens[:6]}")
    name = tokens[4]
    env: dict[str, str] = {}
    index = 5
    while index < len(tokens):
        token = tokens[index]
        if token == "--env":
            pair = tokens[index + 1]
            index += 2
        elif token.startswith("--env="):
            pair = token.split("=", 1)[1]
            index += 1
        elif token in {"-v", "--volume"}:
            index += 2
            continue
        elif token == IMAGE_TAG:
            return name, env, tokens[index + 1 :]
        else:
            index += 1
            continue
        key, separator, value = pair.partition("=")
        if not separator or not key:
            raise SystemExit(f"invalid env pair: {pair!r}")
        if key in env:
            raise SystemExit(f"duplicate expected env key: {key}")
        env[key] = value
    raise SystemExit("image token not found in docker command")


def inspect_rank(rank: int, host: str | None, name: str) -> dict:
    payload = run(["docker", "inspect", name], host=host)
    return json.loads(payload)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cuda-connections", default="64")
    parser.add_argument("--nccl-channels", default="8")
    parser.add_argument("--allgather-max-bytes", default="8388608")
    parser.add_argument("--nccl-ib-qps-per-connection", default="2")
    args = parser.parse_args()

    commands = expected_commands()
    summaries = []
    failures: list[str] = []
    for rank in range(4):
        name, expected_env, expected_serve = split_command(commands[rank])
        host = None if rank == 0 else expected_env["VLLM_HOST_IP"]
        live = inspect_rank(rank, host, name)
        actual_env = dict(item.split("=", 1) for item in live["Config"]["Env"])
        actual_serve = live["Config"].get("Cmd") or []
        actual_image = live["Image"]

        if actual_image != IMAGE_ID:
            failures.append(f"rank {rank}: image {actual_image} != {IMAGE_ID}")
        if live["State"].get("Running") is not True:
            failures.append(f"rank {rank}: container is not running")
        if live["State"].get("OOMKilled") is not False:
            failures.append(f"rank {rank}: OOMKilled is not false")
        if live.get("RestartCount") != 0:
            failures.append(f"rank {rank}: RestartCount={live.get('RestartCount')}")
        for key, value in expected_env.items():
            if actual_env.get(key) != value:
                failures.append(f"rank {rank}: env {key}={actual_env.get(key)!r}, expected {value!r}")
        leaked = sorted(HOST_ONLY_KEYS & set(actual_env))
        if leaked:
            failures.append(f"rank {rank}: host keys leaked into container: {leaked}")
        if actual_serve != expected_serve:
            failures.append(f"rank {rank}: serve argv differs from launcher")

        def serve_value(flag: str) -> str:
            return actual_serve[actual_serve.index(flag) + 1]

        if serve_value("--max-num-batched-tokens") != "65520":
            failures.append(f"rank {rank}: batch is not verified profile 65520")
        if serve_value("--long-prefill-token-threshold") != "65424":
            failures.append(f"rank {rank}: threshold is not verified profile 65424")
        if actual_env.get("CUDA_DEVICE_MAX_CONNECTIONS") != args.cuda_connections:
            failures.append(
                f"rank {rank}: CUDA connections is not {args.cuda_connections}"
            )
        if (
            actual_env.get("NCCL_MAX_NCHANNELS") != args.nccl_channels
            or actual_env.get("NCCL_MIN_NCHANNELS") != args.nccl_channels
        ):
            failures.append(
                f"rank {rank}: NCCL channels are not {args.nccl_channels}/{args.nccl_channels}"
            )
        if actual_env.get("SPARKNET_ROCE_ALLGATHER_MAX_BYTES") != args.allgather_max_bytes:
            failures.append(
                f"rank {rank}: all-gather max is not {args.allgather_max_bytes}"
            )
        if actual_env.get("NCCL_IB_QPS_PER_CONNECTION") != args.nccl_ib_qps_per_connection:
            failures.append(
                f"rank {rank}: QPS per connection is not {args.nccl_ib_qps_per_connection}"
            )

        summaries.append(
            {
                "rank": rank,
                "host": host or "192.168.2.180",
                "container": name,
                "image": actual_image,
                "env_keys_matched": len(expected_env),
                "serve_argv_sha256": hashlib.sha256(
                    json.dumps(actual_serve, separators=(",", ":")).encode()
                ).hexdigest(),
                "max_num_batched_tokens": serve_value("--max-num-batched-tokens"),
                "long_prefill_token_threshold": serve_value("--long-prefill-token-threshold"),
                "cuda_device_max_connections": actual_env["CUDA_DEVICE_MAX_CONNECTIONS"],
                "nccl_channels": actual_env["NCCL_MAX_NCHANNELS"],
                "allgather_max_bytes": actual_env["SPARKNET_ROCE_ALLGATHER_MAX_BYTES"],
                "nccl_ib_qps_per_connection": actual_env["NCCL_IB_QPS_PER_CONNECTION"],
            }
        )

    result = {"ok": not failures, "failures": failures, "ranks": summaries}
    print(json.dumps(result, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
