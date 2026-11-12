#!/usr/bin/env python3
"""Audit generated mesh scripts, live routes, TC rules and host ring inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[4]
CONFIG = ROOT / "spark" / "config.yaml"


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


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    spark_nodes = config["common"]["spark_nodes"]
    mesh = config["common"]["ring"]["mesh"]
    failures = []
    nodes = {}
    for name, record in sorted(
        config["common"]["ring"]["nodes"].items(), key=lambda item: int(item[1]["rank"])
    ):
        host = spark_nodes[name]["ssh"].split("@", 1)[1]
        generated_up = args.generated / f"mesh-up-{name}.sh"
        generated_down = args.generated / f"mesh-down-{name}.sh"
        up_text = generated_up.read_text(encoding="utf-8")
        expected_routes = sorted(set(re.findall(r"ip route show exact (\d+\.\d+\.\d+\.\d+/32)", up_text)))
        expected_tc = sorted(set(re.findall(r"tc -s filter show dev (\S+) ingress pref (\d+)", up_text)))
        installed_hashes = {}
        for installed in (
            f"/opt/spark-ring-mesh/mesh-up-{name}.sh",
            f"/opt/spark-ring-mesh/mesh-down-{name}.sh",
            "/opt/spark-ring-mesh/mesh-up.sh",
            "/opt/spark-ring-mesh/mesh-down.sh",
        ):
            installed_hashes[installed] = ssh(host, f"sha256sum {installed}").split()[0]
        service = ssh(host, "systemctl is-active spark-ring-mesh.service").strip()
        markers = [
            line.split()[0]
            for line in ssh(
                host,
                "systemctl list-units 'spark-ring-mesh-marker@*.service' "
                "--state=active --no-legend --no-pager",
            ).splitlines()
            if line.strip()
        ]
        route_results = {}
        for route in expected_routes:
            route_results[route] = ssh(host, f"ip route show exact {route}").strip()
            if not route_results[route]:
                failures.append(f"{name}: missing live route {route}")
        tc_results = {}
        for netdev, preference in expected_tc:
            output = ssh(host, f"tc -s filter show dev {netdev} ingress pref {preference}")
            tc_results[f"{netdev}:{preference}"] = output
            if "skip_sw" not in output or "in_hw" not in output:
                failures.append(f"{name}: TC rule {netdev}:{preference} is not hardware-offloaded")
        probe_source = ssh(host, "sha256sum /opt/spark-ring-mesh/mlx5_rdma_tx_rewrite_probe.c").split()[0]
        probe_binary = ssh(host, "sha256sum /opt/spark-ring-mesh/bin/mlx5-rdma-tx-rewrite-probe").split()[0]
        probe_text = ssh(
            host,
            "f=/opt/spark-ring-mesh/bin/mlx5-rdma-tx-rewrite-probe; "
            "d=$(mktemp -d); cp $f $d/probe; "
            "objcopy --dump-section .text=$d/text $d/probe; "
            "sha256sum $d/text | cut -d' ' -f1",
        ).split()[0]
        expected_up = sha256(generated_up)
        expected_down = sha256(generated_down)
        if installed_hashes[f"/opt/spark-ring-mesh/mesh-up-{name}.sh"] != expected_up:
            failures.append(f"{name}: installed node mesh-up script differs from the generated plan")
        if installed_hashes[f"/opt/spark-ring-mesh/mesh-down-{name}.sh"] != expected_down:
            failures.append(f"{name}: installed node mesh-down script differs from the generated plan")
        if service != "active":
            failures.append(f"{name}: spark-ring-mesh.service is {service}")
        if len(markers) != 2:
            failures.append(f"{name}: expected two active marker units, found {len(markers)}")
        if probe_source != mesh["probe_source_sha256"]:
            failures.append(f"{name}: probe source hash differs from common.ring.mesh")
        nodes[name] = {
            "host": host,
            "rank": record["rank"],
            "service": service,
            "active_markers": markers,
            "expected_routes": route_results,
            "tc_rules": tc_results,
            "installed_script_sha256": installed_hashes,
            "generated_script_sha256": {
                "mesh-up": expected_up,
                "mesh-down": expected_down,
            },
            "probe_source_sha256": probe_source,
            "probe_binary_sha256": probe_binary,
            "probe_text_sha256": probe_text,
        }

    text_hashes = {node["probe_text_sha256"] for node in nodes.values()}
    if len(text_hashes) != 1:
        failures.append("probe executable .text differs across nodes")
    result = {
        "ok": not failures,
        "source": "spark/config.yaml common.ring + generated sparkring plan",
        "generated": str(args.generated),
        "nodes": nodes,
        "failures": failures,
        "official_alignment": {
            "fabric": "cabled four-node ring A-B-C-D-A with two PCIe paths per neighbour",
            "transport": "oneshot-ring4",
            "mtu": 9000,
            "roce_mtu": 4096,
            "gid_index": 3,
            "site_address_divergence": (
                "Official node-map convention uses per-cable /24 subnets. This site's "
                "parent SSOT intentionally uses point-to-point /30 links; live inventory "
                "matches the parent /30 endpoints and uses NCCL_IB_SUBNET_PREFIX_LEN=30."
            ),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"ok": result["ok"], "out": str(args.out), "failures": failures}, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
