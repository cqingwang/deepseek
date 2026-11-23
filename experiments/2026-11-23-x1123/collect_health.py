#!/usr/bin/env python3
"""Collect the final x1123 four-node service and shared-ring health."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


NODES = (
    ("spark-a", "192.168.2.180", 0),
    ("spark-b", "192.168.2.161", 1),
    ("spark-c", "192.168.2.102", 2),
    ("spark-d", "192.168.2.238", 3),
)
IMAGE = "spark-ds41f:x1123"
EXPECTED_IMAGE_ID = (
    "sha256:63f2e77085f018df2099f8fbb118beaf87f0e36069f2c64e5687a302f6a0092f"
)


def ssh(ip: str, command: str) -> str:
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
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def collect_node(name: str, ip: str, rank: int) -> dict:
    container = f"deepseek-r{rank}"
    inspect = json.loads(ssh(ip, f"docker inspect {container}"))[0]
    state = inspect["State"]
    image_id = inspect["Image"]
    memguard = ssh(
        ip,
        f"systemctl is-active --quiet {container}-memguard.service && echo active || echo inactive",
    )
    memavailable_kib = int(
        ssh(ip, "awk '/MemAvailable/ {print $2}' /proc/meminfo")
    )
    gpu_temperature_c, gpu_utilization_pct = ssh(
        ip,
        "nvidia-smi --query-gpu=temperature.gpu,utilization.gpu --format=csv,noheader,nounits | head -1",
    ).split(",")
    ring_service = ssh(ip, "systemctl is-active spark-ring-mesh.service")
    ring_markers = int(
        ssh(
            ip,
            "systemctl list-units --type=service --state=active "
            "'spark-ring-mesh-marker@*' --no-legend | wc -l",
        )
    )
    logs = ssh(ip, f"docker logs {container} 2>&1")
    ring_journal = ssh(
        ip, "journalctl -u spark-ring-mesh.service -n 80 --no-pager"
    )
    fatal_matches = ssh(
        ip,
        f"docker logs {container} 2>&1 | grep -E "
        "'EngineCore encountered a fatal error|CUDA error|OutOfMemory|OOMKilled|"
        "Traceback \\(most recent call last\\)|NCCL error' | tail -20 || true",
    )
    return {
        "name": name,
        "ip": ip,
        "rank": rank,
        "container": container,
        "running": state.get("Running"),
        "exit_code": state.get("ExitCode"),
        "oom_killed": state.get("OOMKilled"),
        "restart_count": inspect.get("RestartCount"),
        "image_id": image_id,
        "memguard": memguard,
        "memavailable_kib": memavailable_kib,
        "gpu_temperature_c": int(gpu_temperature_c),
        "gpu_utilization_pct": int(gpu_utilization_pct),
        "ring_service": ring_service,
        "ring_markers": ring_markers,
        "ring_log_evidence": {
            "nccl_2_30_7": "vLLM is using nccl==2.30.7" in logs,
            "world_size_4": "world_size=4" in logs,
            "mesh_up_ok": "mesh-up OK" in ring_journal,
        },
        "fatal_matches": fatal_matches.splitlines(),
    }


def verify(nodes: list[dict]) -> list[str]:
    problems: list[str] = []
    image_ids = {node["image_id"] for node in nodes}
    if image_ids != {EXPECTED_IMAGE_ID}:
        problems.append(f"image IDs differ or are unexpected: {sorted(image_ids)}")
    for node in nodes:
        prefix = f"{node['name']}:"
        if node["running"] is not True:
            problems.append(prefix + " container is not running")
        if node["oom_killed"] is not False:
            problems.append(prefix + f" OOMKilled={node['oom_killed']}")
        if node["restart_count"] != 0:
            problems.append(prefix + f" RestartCount={node['restart_count']}")
        if node["memguard"] != "active":
            problems.append(prefix + f" memguard={node['memguard']}")
        if node["ring_service"] != "active":
            problems.append(prefix + f" ring service={node['ring_service']}")
        if node["ring_markers"] < 1:
            problems.append(prefix + " no active ring marker")
        if node["fatal_matches"]:
            problems.append(prefix + f" fatal matches={node['fatal_matches']}")
        ring_evidence = node["ring_log_evidence"]
        required_ring_evidence = (
            ring_evidence["world_size_4"] and ring_evidence["mesh_up_ok"]
        )
        if node["rank"] == 0:
            required_ring_evidence = (
                required_ring_evidence and ring_evidence["nccl_2_30_7"]
            )
        if not required_ring_evidence:
            problems.append(prefix + f" ring log evidence={node['ring_log_evidence']}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    nodes = [collect_node(*node) for node in NODES]
    evidence = {
        "schema_version": 1,
        "image": IMAGE,
        "nodes": nodes,
        "problems": verify(nodes),
    }
    evidence["ok"] = not evidence["problems"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": evidence["ok"], "problems": evidence["problems"]}))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
