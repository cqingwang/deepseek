#!/usr/bin/env python3
"""Audit the live four-rank image labels and critical runtime binaries."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


NODES = {
    0: "192.168.2.180",
    1: "192.168.2.161",
    2: "192.168.2.102",
    3: "192.168.2.238",
}
EXPECTED_LABELS = {
    "local.spark-ds41f.vllm.tree": "ed1bdb7a7684da33d403a055521ac0c4dd7ca670",
    "local.spark-ds41f.b12x.tree": "1a647a311ca55d45070ab83d60668d9c8fb8886c",
    "local.spark-ds41f.nccl.tree": "6af10aa730a64fcee8781d62340a9c9184e79469",
    "local.spark-ds41f.tilelang.tree": "e2b001828fd4e38e6a2eee61f818d5083cb1335f",
    "local.spark-ds41f.tile_kernels.tree": "cb2fa41c8af290204c81c547ed678ebf957cca53",
    "local.spark-ds41f.sparknet.tree": "8cc28a5cd3e683c1f114ed843453e3ce34f323e0",
}
BINARIES = (
    "/usr/local/lib/python3.12/dist-packages/nvidia/nccl/lib/libnccl.so.2",
    "/opt/sparknet/roce/roce_proxy-256c711c72bc15fd.so",
    "/opt/spark-ds41f/candidate/vllm/vllm/_C_stable_libtorch.abi3.so",
    "/opt/spark-ds41f/candidate/vllm/vllm/_moe_C_stable_libtorch.abi3.so",
)


def ssh(host: str, command: str) -> str:
    return subprocess.check_output(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            f"chan@{host}",
            command,
        ],
        text=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="spark-ds41f:x1112")
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    failures = []
    nodes = {}
    layer_sets = set()
    label_sets = set()
    binary_sets = {path: set() for path in BINARIES}
    for rank, host in NODES.items():
        container = f"deepseek-r{rank}"
        payload = json.loads(
            ssh(host, f"docker image inspect {args.image} --format '{{{{json .}}}}'")
        )
        all_labels = payload["Config"]["Labels"]
        labels = {
            key: all_labels.get(key)
            for key in EXPECTED_LABELS
        }
        image_id = payload["Id"]
        layers = payload["RootFS"]["Layers"]
        hashes = {}
        for path in BINARIES:
            hashes[path] = ssh(
                host, f"docker exec {container} sha256sum {path}"
            ).split()[0]
        version = ssh(
            host,
            f"docker exec {container} python3 -c "
            "'import ctypes; v=ctypes.c_int(); "
            "lib=ctypes.CDLL(\"/usr/local/lib/python3.12/dist-packages/nvidia/nccl/lib/libnccl.so.2\"); "
            "assert lib.ncclGetVersion(ctypes.byref(v)) == 0; print(v.value)'",
        ).strip()
        if image_id != args.image_id:
            failures.append(f"rank {rank}: image ID {image_id} != {args.image_id}")
        if labels != EXPECTED_LABELS:
            failures.append(f"rank {rank}: source tree labels differ from the pinned official recipe")
        if version != "23007":
            failures.append(f"rank {rank}: NCCL version code {version} != 23007")
        layer_sets.add(tuple(layers))
        label_sets.add(tuple(sorted(labels.items())))
        for path, digest in hashes.items():
            binary_sets[path].add(digest)
        nodes[rank] = {
            "host": host,
            "image_id": image_id,
            "layer_count": len(layers),
            "source_tree_labels": labels,
            "build_labels": {
                key: value
                for key, value in all_labels.items()
                if key.startswith("local.spark-ds41f.")
            },
            "nccl_version_code": int(version),
            "binary_sha256": hashes,
        }

    if len(layer_sets) != 1:
        failures.append("four ranks do not share one image layer list")
    if len(label_sets) != 1:
        failures.append("four ranks do not share one source-label set")
    for path, hashes in binary_sets.items():
        if len(hashes) != 1:
            failures.append(f"{path}: four ranks do not share one binary hash")

    result = {
        "ok": not failures,
        "image": args.image,
        "image_id": args.image_id,
        "expected_labels": EXPECTED_LABELS,
        "nodes": nodes,
        "failures": failures,
        "provenance_limit": (
            "The public release publishes no image or binary asset. Alignment is proven "
            "to the pinned source trees, NCCL version, and one identical image ID on all "
            "four ranks; no official reference binary is available for byte comparison."
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"ok": result["ok"], "out": str(args.out), "failures": failures}, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
