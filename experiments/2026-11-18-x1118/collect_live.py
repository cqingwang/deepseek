#!/usr/bin/env python3
"""Collect and verify the x1118 live container contract on the four Sparks."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


NODES = (
    ("spark-a", "192.168.2.180", 0),
    ("spark-b", "192.168.2.161", 1),
    ("spark-c", "192.168.2.102", 2),
    ("spark-d", "192.168.2.238", 3),
)
IMAGE = "spark-ds41f:x1118"
EXPECTED_ENV_COUNT = 90
EXPECTED_IMAGE_LABELS = {
    "local.spark-ds41f.vllm.tree": "bb5439cabc793d79b50829c1fe10429389f0eed0",
    "local.spark-ds41f.nccl.tree": "6af10aa730a64fcee8781d62340a9c9184e79469",
    "local.spark-ds41f.tilelang.tree": "741806c7357102130a149cdcae2a5f22678eab67",
    "local.spark-ds41f.tile_kernels.tree": "cb2fa41c8af290204c81c547ed678ebf957cca53",
    "local.spark-ds41f.sparknet.tree": "e06f6a0675cb66638231d2c68948a2b315b69b07",
}
EXPECTED_ARGS = {
    "--max-model-len": "1000000",
    "--max-num-seqs": "5",
    "--max-num-batched-tokens": "16384",
    "--kv-cache-memory-bytes": "5374973440",
    "--long-prefill-token-threshold": "16354",
    "--max-cudagraph-capture-size": "30",
    "--limit-mm-per-prompt": '{"image":67}',
    "--master-port": "25000",
    "--enable-per-request-metrics": 1,
    "num_speculative_tokens": 5,
    "max_new_tokens": 65536,
}
EXPECTED_STARTUP_HEAD = {
    "max_num_seqs": "5",
    "max_model_len": "1000000",
    "max_num_batched_tokens": "16384",
    "max_new_tokens": "65536",
    "master_port": "25000",
    "num_spec_tokens": "5",
}
EXPECTED_STARTUP_WORKER = {"kv_cache_gib": "5.01"}
HOST_ONLY = {
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
    "DS41F_MAX_NUM_SEQS",
    "DS41F_MAX_MODEL_LEN",
    "DS41F_MAX_NEW_TOKENS",
    "DS41F_MAX_NUM_BATCHED_TOKENS",
    "DS41F_NUM_SPECULATIVE_TOKENS",
    "DS41F_MAX_IMAGES_PER_REQUEST",
    "DS41F_DEFAULT_ENABLE_THINKING",
    "DS41F_REASONING_EFFORT",
    "DS41F_MASTER_PORT",
}


def ssh(ip: str, command: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            f"chan@{ip}",
            command,
        ],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def flag_value(args: list[str], flag: str) -> str | None:
    if flag not in args:
        return None
    index = args.index(flag)
    if index + 1 >= len(args) or args[index + 1].startswith("--"):
        return ""
    return args[index + 1]


def flag_count(args: list[str], flag: str) -> int:
    return args.count(flag)


def first_match(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(1) if match else None


def assigned_count(env_map: dict[str, str], template: Path) -> int:
    keys = []
    for raw in template.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.append(line.split("=", 1)[0])
    dynamic = {
        "NCCL_IB_HCA",
        "SPARKNET_ROCE_PEER_HCAS",
        "SPARKNET_ROCE_TOPOLOGY",
        "VLLM_HOST_IP",
    }
    return sum(key in env_map for key in keys) + len(dynamic & set(env_map))


def collect_node(name: str, ip: str, rank: int) -> dict:
    container = f"deepseek-r{rank}"
    inspect = json.loads(
        ssh(ip, f"docker inspect {container}").stdout
    )[0]
    config = inspect["Config"]
    args = list(config.get("Cmd") or [])
    env = config.get("Env") or []
    env_map = dict(item.split("=", 1) for item in env if "=" in item)
    template = Path(__file__).resolve().parents[2] / ".env.deepseek.example"
    labels = (config.get("Labels") or {})
    logs = ssh(
        ip,
        (
            f"docker logs --timestamps {container} 2>&1 | "
            "grep -E 'non-default args|Initializing a V1 LLM engine|"
            "GPU KV cache size|Maximum concurrency|memory_breakdown|"
            "Copy drafts widen' | tail -200"
        ),
    ).stdout
    memory_kib = ssh(ip, "awk '/MemAvailable/ {print $2}' /proc/meminfo").stdout.strip()
    memguard = ssh(
        ip, f"systemctl is-active --quiet {container}-memguard.service && echo active || echo inactive",
        check=False,
    ).stdout.strip()
    state = inspect["State"]
    selected = {
        "--max-model-len": flag_value(args, "--max-model-len"),
        "--max-num-seqs": flag_value(args, "--max-num-seqs"),
        "--max-num-batched-tokens": flag_value(args, "--max-num-batched-tokens"),
        "--kv-cache-memory-bytes": flag_value(args, "--kv-cache-memory-bytes"),
        "--long-prefill-token-threshold": flag_value(args, "--long-prefill-token-threshold"),
        "--max-cudagraph-capture-size": flag_value(args, "--max-cudagraph-capture-size"),
        "--limit-mm-per-prompt": flag_value(args, "--limit-mm-per-prompt"),
        "--master-port": flag_value(args, "--master-port"),
        "--enable-per-request-metrics": flag_count(args, "--enable-per-request-metrics"),
    }
    speculative = flag_value(args, "--speculative-config")
    if speculative:
        selected["num_speculative_tokens"] = json.loads(speculative).get(
            "num_speculative_tokens"
        )
    generation = flag_value(args, "--override-generation-config")
    if generation:
        selected["max_new_tokens"] = json.loads(generation).get("max_new_tokens")
    startup = {
        "max_num_seqs": first_match(r"'max_num_seqs':\s*(\d+)", logs),
        "max_model_len": first_match(r"'max_model_len':\s*(\d+)", logs),
        "max_num_batched_tokens": first_match(
            r"'max_num_batched_tokens':\s*(\d+)", logs
        ),
        "max_new_tokens": first_match(r"'max_new_tokens':\s*(\d+)", logs),
        "num_spec_tokens": first_match(r"\bnum_spec_tokens=(\d+)", logs),
        "master_port": first_match(r"'master_port':\s*(\d+)", logs),
        "kv_cache_tokens": first_match(r"GPU KV cache size:\s*([\d,]+)\s*tokens", logs),
        "kv_max_concurrency": first_match(
            r"Maximum concurrency for [\d,]+ tokens per request:\s*([\d.]+)x", logs
        ),
        "kv_cache_gib": first_match(r"\bkv_cache=([\d.]+) GiB", logs),
        "worker_rank": first_match(r"\bmemory_breakdown\] rank=(\d+)", logs),
    }
    return {
        "name": name,
        "ip": ip,
        "rank": rank,
        "container": container,
        "running": state.get("Running"),
        "exit_code": state.get("ExitCode"),
        "oom_killed": state.get("OOMKilled"),
        "restart_count": inspect.get("RestartCount"),
        "image_id": inspect.get("Image"),
        "env_count": assigned_count(env_map, template),
        "container_env_total": len(env),
        "business_env_leaks": sorted(HOST_ONLY & set(env_map)),
        "labels": labels,
        "args": selected,
        "startup": startup,
        "memavailable_kib": int(memory_kib) if memory_kib.isdigit() else None,
        "memguard": memguard,
        "logs_tail": logs[-12000:],
        "inspect": inspect,
    }


def verify(evidence: dict) -> list[str]:
    problems: list[str] = []
    image_ids = set()
    for node in evidence["nodes"]:
        prefix = f"{node['name']}:"
        image_ids.add(node["image_id"])
        if node["running"] is not True:
            problems.append(f"{prefix} container not running")
        if node["oom_killed"] is not False:
            problems.append(f"{prefix} OOMKilled={node['oom_killed']}")
        if node["restart_count"] != 0:
            problems.append(f"{prefix} RestartCount={node['restart_count']}")
        if node["memguard"] != "active":
            problems.append(f"{prefix} memguard={node['memguard']}")
        if node["env_count"] != EXPECTED_ENV_COUNT:
            problems.append(f"{prefix} container env count={node['env_count']}")
        if node["business_env_leaks"]:
            problems.append(f"{prefix} business env leaks={node['business_env_leaks']}")
        for key, value in EXPECTED_IMAGE_LABELS.items():
            if node["labels"].get(key) != value:
                problems.append(f"{prefix} label {key}={node['labels'].get(key)!r}")
        for key, value in EXPECTED_ARGS.items():
            if node["args"].get(key) != value:
                problems.append(f"{prefix} argv {key}={node['args'].get(key)!r}")
        if node["rank"] == 0:
            expected_startup = EXPECTED_STARTUP_HEAD
        else:
            expected_startup = {
                **EXPECTED_STARTUP_WORKER,
                "worker_rank": str(node["rank"]),
            }
        for key, value in expected_startup.items():
            if node["startup"].get(key) != value:
                problems.append(f"{prefix} startup {key}={node['startup'].get(key)!r}")
    if len(image_ids) != 1:
        problems.append(f"image IDs differ: {sorted(image_ids)}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    nodes = [collect_node(*node) for node in NODES]
    evidence = {
        "schema_version": 1,
        "image": IMAGE,
        "expected_env_count": EXPECTED_ENV_COUNT,
        "expected_image_labels": EXPECTED_IMAGE_LABELS,
        "nodes": nodes,
    }
    evidence["problems"] = verify(evidence)
    evidence["ok"] = not evidence["problems"]
    Path(args.output).write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if evidence["problems"]:
        for problem in evidence["problems"]:
            print(f"ERROR: {problem}")
        return 1
    print(f"live contract OK: {len(nodes)} ranks, image {nodes[0]['image_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
