#!/usr/bin/env python3
"""Build a source- and binary-backed matrix for every injected container parameter."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shlex
import subprocess
from pathlib import Path


ROOT = Path("/opt/spark/deepseek")
MODEL_CONTAINER_PATH = "/models/DeepSeek-V4.1-Flash-1017"
SITE_DERIVED_KEYS = {
    "NCCL_IB_HCA",
    "SPARKNET_ROCE_PEER_HCAS",
    "SPARKNET_ROCE_TOPOLOGY",
    "VLLM_HOST_IP",
}
SITE_OVERRIDE_KEYS = {
    "GLOO_SOCKET_IFNAME",
    "NCCL_IB_GID_INDEX",
    "NCCL_IB_SUBNET_PREFIX_LEN",
    "NCCL_SOCKET_IFNAME",
    "TP_SOCKET_IFNAME",
}
STALE_KEYS = {
    "SAFETENSORS_FAST_GPU": (
        "No runtime source or installed binary reads this key. The current "
        "direct-IO / fastsafetensors path is selected by --load-format and its "
        "own loader settings; the official recipe retains a historical key."
    ),
    "SPARK_DS41F_DSPARK_MAIN_PROJ_TP": (
        "dspark.py now column-shards main_proj whenever TP>1; the old environment "
        "switch has no reader."
    ),
    "SPARK_DS41F_DSPARK_VOCAB_PARALLEL": (
        "_vocab_parallel_greedy() now returns True unconditionally; the old "
        "environment switch has no reader."
    ),
    "VLLM_CACHE_DIR": (
        "vLLM v1 consumes VLLM_CACHE_ROOT. VLLM_CACHE_DIR has no reader and is "
        "retained only by the official recipe."
    ),
}
LIBRARY_LAYERS = {
    "CUDA_CACHE_MAXSIZE": (
        "cuda_runtime",
        "CUDA JIT cache contract: CUDA_CACHE_MAXSIZE limits the on-disk compute cache.",
    ),
    "CUDA_CACHE_PATH": (
        "cuda_runtime",
        "CUDA JIT cache contract: CUDA_CACHE_PATH selects the compute-cache directory.",
    ),
    "CUDA_DEVICE_MAX_CONNECTIONS": (
        "cuda_runtime",
        "CUDA device context contract: limits hardware work queues per context.",
    ),
    "GLOO_SOCKET_IFNAME": (
        "pytorch_runtime",
        "PyTorch Gloo process-group socket-interface contract.",
    ),
    "MALLOC_ARENA_MAX": (
        "glibc_malloc",
        "glibc malloc contract: caps per-process malloc arenas.",
    ),
    "MALLOC_MMAP_THRESHOLD_": (
        "glibc_malloc",
        "glibc malloc contract: sets the mmap threshold used by malloc.",
    ),
    "OMP_NUM_THREADS": (
        "openmp_runtime",
        "OpenMP runtime contract: default thread count for parallel regions.",
    ),
    "PYTORCH_CUDA_ALLOC_CONF": (
        "pytorch_runtime",
        "PyTorch CUDA caching allocator contract.",
    ),
    "TORCH_NCCL_ASYNC_ERROR_HANDLING": (
        "pytorch_runtime",
        "PyTorch ProcessGroupNCCL asynchronous error-handling contract.",
    ),
    "TP_SOCKET_IFNAME": (
        "pytorch_runtime",
        "PyTorch distributed tensor-pipe socket-interface contract.",
    ),
    "TRITON_CACHE_DIR": (
        "triton_runtime",
        "Triton knobs.py reads TRITON_CACHE_DIR when resolving its cache path.",
    ),
    "TRANSFORMERS_OFFLINE": (
        "hf_runtime",
        "huggingface_hub.constants treats TRANSFORMERS_OFFLINE as an alias of "
        "HF_HUB_OFFLINE.",
    ),
}
RUNTIME_LOG_PATTERNS = {
    "OMP_NUM_THREADS": (
        r"OMP_NUM_THREADS=\S+ is set; leaving Torch threads at \S+ for serving"
    ),
    "VLLM_L2_PREFETCH_GRID": (
        r"\[l2_prefetch\] TileLang kernel ready \(grid=\d+ block=\d+ chunk=\d+\)"
    ),
    "VLLM_L2_PREFETCH_KERNELS": (
        r"\[l2_prefetch\] TileLang kernel ready \(grid=\d+ block=\d+ chunk=\d+\)"
    ),
    "VLLM_USE_V2_MODEL_RUNNER": r"Using V2 Model Runner",
    "WEIGHTLESS_STEER_MODEL": r"GLP steering on: .*requests to 'deepseek-v4\.1-flash-weightless'",
    "WEIGHTLESS_STEER_PATH": r"GLP file /glp/vector\.gguf: \d+ directions",
}
for _key in ("HF_HOME", "HF_HUB_OFFLINE"):
    LIBRARY_LAYERS[_key] = (
        "hf_runtime",
        "huggingface_hub.constants reads this key while initializing cache/offline policy.",
    )
for _key in (
    "NCCL_ALGO",
    "NCCL_BUFFSIZE",
    "NCCL_COLLNET_ENABLE",
    "NCCL_CROSS_NIC",
    "NCCL_CUMEM_ENABLE",
    "NCCL_CUMEM_HOST_ENABLE",
    "NCCL_DEBUG",
    "NCCL_DEBUG_SUBSYS",
    "NCCL_GIN_ENABLE",
    "NCCL_IB_ADDR_FAMILY",
    "NCCL_IB_DISABLE",
    "NCCL_IB_GID_INDEX",
    "NCCL_IB_HCA",
    "NCCL_IB_MERGE_NICS",
    "NCCL_IB_QPS_PER_CONNECTION",
    "NCCL_IB_ROCE_VERSION_NUM",
    "NCCL_IB_SUBNET_AWARE_ROUTING",
    "NCCL_IB_SUBNET_PREFIX_LEN",
    "NCCL_IGNORE_CPU_AFFINITY",
    "NCCL_LL128_BUFFSIZE",
    "NCCL_MAX_NCHANNELS",
    "NCCL_MIN_NCHANNELS",
    "NCCL_MIN_TRAFFIC_PER_CHANNEL",
    "NCCL_MNNVL_ENABLE",
    "NCCL_NET",
    "NCCL_NET_PLUGIN",
    "NCCL_NVLS_ENABLE",
    "NCCL_P2P_DISABLE",
    "NCCL_PAT_ENABLE",
    "NCCL_PROTO",
    "NCCL_RMA_DISABLE",
    "NCCL_RUNTIME_CONNECT",
    "NCCL_SHM_DISABLE",
    "NCCL_SOCKET_IFNAME",
    "NCCL_SWITCHLESS_BIDIRECTIONAL",
    "NCCL_THREAD_THRESHOLDS",
):
    LIBRARY_LAYERS[_key] = (
        "nccl_runtime",
        "libnccl.so.2 environment parser; the exact key is present in the runtime binary.",
    )


CONTAINER_SCANNER = r"""
import json
import pathlib
import re
import sys

keys = sys.argv[1:]
roots = [
    pathlib.Path("/opt/spark-ds41f/candidate/vllm/vllm"),
    pathlib.Path("/opt/spark-ds41f/candidate/b12x/b12x"),
    pathlib.Path("/usr/local/lib/python3.12/dist-packages/vllm"),
    pathlib.Path("/usr/local/lib/python3.12/dist-packages/sparknet"),
    pathlib.Path("/usr/local/lib/python3.12/dist-packages/tilelang"),
    pathlib.Path("/usr/local/lib/python3.12/dist-packages/triton"),
    pathlib.Path("/usr/local/lib/python3.12/dist-packages/transformers"),
    pathlib.Path("/usr/local/lib/python3.12/dist-packages/huggingface_hub"),
    pathlib.Path("/usr/local/lib/python3.12/dist-packages/safetensors"),
    pathlib.Path("/usr/local/lib/python3.12/dist-packages/fastsafetensors"),
    pathlib.Path("/usr/local/lib/python3.12/dist-packages/torch/distributed"),
]
suffixes = {".py", ".c", ".cc", ".cpp", ".h", ".hpp", ".cu", ".cuh", ".rs", ".go"}
skip_parts = ("/tests/", "/test/", "/benchmarks/", "/.buildkite/", "/examples/", "/docs/")
direct_patterns = [
    re.compile(r"(?:os\.environ(?:\.get)?|os\.getenv|getenv|env::var)\s*\(\s*['\"]{key}['\"]"),
    re.compile(r"os\.environ\s*\[\s*['\"]{key}['\"]"),
    re.compile(r"['\"]{key}['\"]\s*:"),
]
patterns_by_key = {
    key: [
        re.compile(pattern.pattern.format(key=re.escape(key)))
        for pattern in direct_patterns
    ]
    for key in keys
}
hits = {key: [] for key in keys}
scanned = 0
for root in roots:
    if not root.is_dir():
        continue
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        posix = path.as_posix()
        if any(part in posix for part in skip_parts):
            continue
        try:
            if path.stat().st_size > 4 * 1024 * 1024:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw:
            continue
        scanned += 1
        data = raw.decode("utf-8", errors="ignore")
        lines = data.splitlines()
        for key in keys:
            if key.encode() not in raw:
                continue
            for line_number, line in enumerate(lines, 1):
                if key not in line:
                    continue
                stripped = line.strip()
                accessor = any(pattern.search(line) for pattern in patterns_by_key[key])
                hits[key].append({
                    "path": posix,
                    "line": line_number,
                    "text": stripped[:500],
                    "accessor": accessor,
                    "comment_only": stripped.startswith(("#", "//", "*", "\x22\x22\x22", "'''")),
                })
                if len(hits[key]) >= 12:
                    break

binary_paths = [
    pathlib.Path("/usr/local/lib/python3.12/dist-packages/nvidia/nccl/lib/libnccl.so.2"),
    pathlib.Path("/usr/local/lib/python3.12/dist-packages/safetensors/_safetensors_rust.abi3.so"),
    pathlib.Path("/usr/local/lib/python3.12/dist-packages/fastsafetensors/cpp.cpython-312-aarch64-linux-gnu.so"),
]
binary_hits = {}
for path in binary_paths:
    if not path.is_file():
        continue
    data = path.read_bytes()
    present = [key for key in keys if key.encode() in data]
    if present:
        binary_hits[str(path)] = present
print(json.dumps({"scanned_files": scanned, "hits": hits, "binary_hits": binary_hits}))
"""


def run(command: list[str], **kwargs) -> str:
    return subprocess.check_output(command, text=True, **kwargs)


def parse_env(path: Path) -> dict[str, str]:
    result = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", raw)
        if not match:
            continue
        key, raw_value = match.groups()
        tokens = shlex.split(raw_value) if raw_value else []
        result[key] = tokens[0] if tokens else ""
    return result


def print_commands() -> dict[int, list[str]]:
    output = run(["bash", str(ROOT / "start-tp4.sh"), "print-env"], cwd=ROOT)
    parts = re.split(r"^== rank ([0-3]) ==$", output, flags=re.MULTILINE)
    if len(parts) != 9:
        raise SystemExit(f"unexpected print-env output:\n{output}")
    return {
        int(parts[index]): shlex.split(parts[index + 1].strip())
        for index in range(1, len(parts), 2)
    }


def split_docker_command(tokens: list[str]) -> tuple[dict[str, str], list[str], str]:
    if tokens[:3] != ["docker", "run", "-d"]:
        raise SystemExit(f"unexpected docker command: {tokens[:3]}")
    env = {}
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
        elif token.startswith("spark-ds41f:"):
            return env, tokens[index + 1 :], token
        else:
            index += 1
            continue
        key, separator, value = pair.partition("=")
        if not separator or not key or key in env:
            raise SystemExit(f"invalid or duplicate env pair: {pair!r}")
        env[key] = value
    raise SystemExit("image token not found")


def resolve_recipe() -> dict:
    path = ROOT / "scripts/recipe.py"
    spec = importlib.util.spec_from_file_location("spark_ds41f_recipe_audit", path)
    if not spec or not spec.loader:
        raise SystemExit(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cluster = json.loads((ROOT / "config/cluster.json").read_text(encoding="utf-8"))
    return module.resolve(cluster, "tp4-ring")


def expected_serve_args(
    resolved: dict,
    managed: dict[str, str],
    profile: dict,
    rank: int,
    *,
    render_site_values: bool = True,
) -> list[str]:
    args = list(resolved["serve_args"])
    args[0] = MODEL_CONTAINER_PATH
    if render_site_values:
        args[args.index("--host") + 1] = managed["API_HOST"]
        args[args.index("--port") + 1] = managed["PORT"]
    for flag, value in profile.get("serve_options", {}).items():
        args[args.index(flag) + 1] = value
    args.extend(["--api-key", managed["API_KEY"]])
    args.extend(
        [
            "--node-rank",
            str(rank),
            "--master-addr",
            managed["HEAD_MANAGEMENT_IP"],
            "--master-port",
            "9999",
        ]
    )
    if rank:
        args.append("--headless")
    return args


def flag_positions(args: list[str]) -> list[tuple[int, str]]:
    return [(index, token) for index, token in enumerate(args) if token.startswith("--")]


def flag_values(args: list[str]) -> dict[str, list[str]]:
    values = {}
    positions = flag_positions(args)
    for offset, (index, flag) in enumerate(positions):
        end = positions[offset + 1][0] if offset + 1 < len(positions) else len(args)
        values.setdefault(flag, []).append(args[index + 1 : end])
    return values


def scan_container(image: str, keys: list[str]) -> dict:
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network=none",
            "--entrypoint",
            "python3",
            image,
            "-",
            *keys,
        ],
        input=CONTAINER_SCANNER,
        text=True,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise SystemExit(
            "container source scan failed: "
            f"returncode={result.returncode} stdout={result.stdout[-1000:]!r} "
            f"stderr={result.stderr[-4000:]!r}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"container source scan emitted invalid JSON: {exc}; "
            f"stdout={result.stdout[-2000:]!r} stderr={result.stderr[-4000:]!r}"
        ) from exc


def source_layer(path: str) -> str:
    if path.endswith("/vllm/envs.py"):
        return "vllm_env_registry"
    if "/tilelang/" in path:
        return "tilelang_runtime"
    if "/triton/" in path:
        return "triton_runtime"
    if "/transformers/" in path or "/huggingface_hub/" in path:
        return "hf_runtime"
    if "/safetensors/" in path or "/fastsafetensors/" in path:
        return "safetensors_runtime"
    if "/torch/" in path:
        return "pytorch_runtime"
    if "/vllm/" in path:
        return "vllm_python"
    return "python_runtime"


def format_source_hit(hit: dict) -> str:
    suffix = " accessor" if hit["accessor"] else " reference"
    return f"{hit['path']}:{hit['line']}: {hit['text']} [{suffix}]"


def sorted_source_hits(hits: list[dict]) -> list[dict]:
    """Prefer executable accessors over nearby comments and error strings."""
    return sorted(
        (hit for hit in hits if not hit["comment_only"]),
        key=lambda hit: (not hit["accessor"], hit["path"], hit["line"]),
    )


def runtime_log_evidence(container: str) -> dict[str, str]:
    result = subprocess.run(
        ["docker", "logs", container],
        text=True,
        check=False,
        capture_output=True,
    )
    logs = result.stdout + result.stderr
    evidence = {}
    for key, pattern in RUNTIME_LOG_PATTERNS.items():
        match = re.search(pattern, logs)
        if match:
            evidence[key] = "startup log: " + match.group(0)[:500]
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="spark-ds41f:x1112")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    managed = parse_env(ROOT / ".env.deepseek")
    profile_path = ROOT / managed["DS41F_RUN_PROFILE"]
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    commands = print_commands()

    actual_env_by_rank = {}
    actual_serve_by_rank = {}
    image = ""
    for rank, command in commands.items():
        env, serve_args, image = split_docker_command(command)
        actual_env_by_rank[rank] = env
        actual_serve_by_rank[rank] = serve_args

    key_sets = {frozenset(env) for env in actual_env_by_rank.values()}
    if len(key_sets) != 1:
        raise SystemExit("rank container env key sets differ")
    keys = sorted(next(iter(key_sets)))
    if len(keys) != 86:
        raise SystemExit(f"expected 86 container keys, got {len(keys)}")

    source_scan = scan_container(image, keys)
    observed_logs = runtime_log_evidence("deepseek-r0")
    resolved = resolve_recipe()
    # Keep the official recipe values. Site lifecycle additions below make the
    # argv lengths comparable; the matrix then exposes every value override.
    official_args = expected_serve_args(
        resolved, managed, {}, 0, render_site_values=False
    )
    actual_args = actual_serve_by_rank[0]
    official_positions = flag_positions(official_args)
    actual_positions = flag_positions(actual_args)
    official_flags = [flag for _, flag in official_positions]
    actual_flags = [flag for _, flag in actual_positions]
    duplicate_flags = sorted(
        {flag for flag in actual_flags if actual_flags.count(flag) > 1}
    )
    official_values = flag_values(official_args)
    actual_values = flag_values(actual_args)
    value_differences = {
        flag: {
            "official": official_values.get(flag),
            "actual": actual_values.get(flag),
        }
        for flag in sorted(set(official_values) | set(actual_values))
        if official_values.get(flag) != actual_values.get(flag)
    }

    binary_keys = {
        key
        for present in source_scan["binary_hits"].values()
        for key in present
    }
    records = {}
    for key in keys:
        values = {
            str(rank): actual_env_by_rank[rank][key]
            for rank in sorted(actual_env_by_rank)
        }
        value_sources = []
        if key in resolved["environment"]:
            value_sources.append("official_recipe")
        if key == "NCCL_IB_QPS_PER_CONNECTION":
            value_sources.append("site_fixed")
        if key in profile.get("environment", {}):
            value_sources.append("runtime_profile")
        if key in SITE_DERIVED_KEYS:
            value_sources.append("site_derived")
        if key in SITE_OVERRIDE_KEYS:
            value_sources.append("site_override")

        if key in STALE_KEYS:
            layer = "none"
            status = "stale_no_consumer"
            evidence = [STALE_KEYS[key]]
        elif key in LIBRARY_LAYERS:
            layer, library_evidence = LIBRARY_LAYERS[key]
            status = "consumed"
            evidence = [library_evidence]
            if key in binary_keys:
                evidence.append(
                    "Exact key found in runtime binary: "
                    + ", ".join(
                        path
                        for path, present in source_scan["binary_hits"].items()
                        if key in present
                    )
                )
            source_hits = sorted_source_hits(source_scan["hits"].get(key, []))
            evidence.extend(format_source_hit(hit) for hit in source_hits[:3])
        else:
            source_hits = sorted_source_hits(source_scan["hits"].get(key, []))
            if not source_hits:
                layer = "none"
                status = "unresolved"
                evidence = ["No runtime source reader or binary string was found."]
            else:
                layer = source_layer(source_hits[0]["path"])
                status = "consumed"
                evidence = [format_source_hit(hit) for hit in source_hits[:6]]

        records[key] = {
            "values_by_rank": values,
            "value_sources": value_sources,
            "consumer_layer": layer,
            "status": status,
            "consumer_evidence": evidence,
            "runtime_evidence": observed_logs.get(key),
        }

    unresolved = sorted(
        key for key, record in records.items() if record["status"] == "unresolved"
    )
    stale = sorted(
        key for key, record in records.items() if record["status"] == "stale_no_consumer"
    )
    matrix = {
        "image": image,
        "image_id": run(["docker", "image", "inspect", image, "--format", "{{.Id}}"]).strip(),
        "source_scan": {
            "roots": [
                "/opt/spark-ds41f/candidate/vllm/vllm",
                "/opt/spark-ds41f/candidate/b12x/b12x",
                "/usr/local/lib/python3.12/dist-packages/{vllm,sparknet,tilelang,triton,transformers,huggingface_hub,safetensors,fastsafetensors,torch/distributed}",
            ],
            "scanned_files": source_scan["scanned_files"],
            "binary_hits": source_scan["binary_hits"],
        },
        "summary": {
            "container_keys": len(records),
            "consumed": len(records) - len(stale) - len(unresolved),
            "stale_no_consumer": len(stale),
            "unresolved": len(unresolved),
            "stale_keys": stale,
            "unresolved_keys": unresolved,
        },
        "argv": {
            "official_count": len(official_args),
            "actual_count": len(actual_args),
            "same_length": len(official_args) == len(actual_args),
            "same_flag_positions": official_positions == actual_positions,
            "missing_flags": sorted(set(official_flags) - set(actual_flags)),
            "extra_flags": sorted(set(actual_flags) - set(official_flags)),
            "duplicate_flags": duplicate_flags,
            "value_differences": value_differences,
            "official_argv": official_args,
            "actual_argv": actual_args,
        },
        "keys": records,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(matrix, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            matrix["summary"]
            | {
                "argv_same_flag_positions": matrix["argv"]["same_flag_positions"],
                "value_differences": sorted(value_differences),
                "out": str(args.out),
            },
            indent=2,
        )
    )
    return 0 if not unresolved and matrix["argv"]["same_flag_positions"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
