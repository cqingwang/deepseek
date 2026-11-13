#!/usr/bin/env python3
"""Create the ignored site profile for the original-source abtest checkout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path("/opt/abtest/spark-ds41f")
SOURCE = ROOT / "config/cluster.json"
OUTPUT = ROOT / "config/abtest.local.json"
NODES = ROOT / "config/nodes-ring4.local.json"
OVERLAY_NODES = ROOT / "config/nodes-ring4.overlay.local.json"
MODEL_DIR = "/opt/models/deepseek-ai/DeepSeek-V4.1-Flash-1017"
OVERLAY_IPS = {
    "192.168.2.180": "10.200.0.1",
    "192.168.2.161": "10.200.0.2",
    "192.168.2.102": "10.200.0.3",
    "192.168.2.238": "10.200.0.4",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", choices=("wifi", "overlay"), default="wifi")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    cluster = json.loads(SOURCE.read_text(encoding="utf-8"))
    cluster["container"]["image"] = "spark-ds41f:x1112-original"
    cluster["deployment"]["repository"] = "git@github.com:christopherowen/spark-ds41f.git"
    cluster["host"]["home"] = "/home/chan"
    control_interface = "wlP9s9"
    control_ip = "192.168.2.180"
    if args.control == "overlay":
        nodes = json.loads(NODES.read_text(encoding="utf-8"))
        for node in nodes["nodes"]:
            node["control_ip"] = OVERLAY_IPS[node["name"]]
        OVERLAY_NODES.write_text(json.dumps(nodes, indent=2) + "\n", encoding="utf-8")
        cluster["nodes_config"] = "config/nodes-ring4.overlay.local.json"
        cluster["shapes"]["tp4-ring"]["nodes_config"] = "config/nodes-ring4.overlay.local.json"
        control_interface = "ctrl0"
        control_ip = "10.200.0.1"
    cluster["distributed"]["master_addr"] = control_ip
    for key in ("GLOO_SOCKET_IFNAME", "TP_SOCKET_IFNAME"):
        cluster["environment"][key] = control_interface
    for shape in cluster["shapes"].values():
        if "NCCL_SOCKET_IFNAME" in shape.get("environment", {}):
            shape["environment"]["NCCL_SOCKET_IFNAME"] = control_interface
    cluster["models"]["v41-flash-1017"] = {
        "snapshot": MODEL_DIR,
        "blobs": MODEL_DIR,
        "notes": "Reuse the existing flat model directory read-only; no second weight copy.",
    }
    args.output.write_text(json.dumps(cluster, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
