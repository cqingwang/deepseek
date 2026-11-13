#!/usr/bin/env python3
"""Derive a reversible /24 ring config from the production /30 SSOT."""

from __future__ import annotations

import argparse
import ipaddress
import json
from collections import defaultdict
from pathlib import Path

import yaml


U24_IPS = {
    "spark-a": {
        "enp1s0f0np0": "10.40.0.1",
        "enp1s0f1np1": "10.40.3.2",
        "enP2p1s0f0np0": "10.41.0.1",
        "enP2p1s0f1np1": "10.41.3.2",
    },
    "spark-b": {
        "enp1s0f0np0": "10.40.0.2",
        "enp1s0f1np1": "10.40.1.1",
        "enP2p1s0f0np0": "10.41.0.2",
        "enP2p1s0f1np1": "10.41.1.1",
    },
    "spark-c": {
        "enp1s0f0np0": "10.40.2.1",
        "enp1s0f1np1": "10.40.1.2",
        "enP2p1s0f0np0": "10.41.2.1",
        "enP2p1s0f1np1": "10.41.1.2",
    },
    "spark-d": {
        "enp1s0f0np0": "10.40.2.2",
        "enp1s0f1np1": "10.40.3.1",
        "enP2p1s0f0np0": "10.41.2.2",
        "enP2p1s0f1np1": "10.41.3.1",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    config = yaml.safe_load(args.source.read_text(encoding="utf-8"))
    nodes = config["common"]["ring"]["nodes"]
    endpoints = defaultdict(list)
    for node_name, node in nodes.items():
        for plane in ("ports", "ports2"):
            for port in node[plane]:
                network = str(ipaddress.ip_network(port["cidr"], strict=False))
                endpoints[network].append((node_name, port))

    for network, pair in endpoints.items():
        if len(pair) != 2:
            raise SystemExit(f"{network} has {len(pair)} endpoints")
        (node_a, port_a), (node_b, port_b) = pair
        port_a["cidr"] = f"{U24_IPS[node_a][port_a['interface']]}/24"
        port_a["neighbor"] = node_b
        port_a["neighbor_ip"] = U24_IPS[node_b][port_b["interface"]]
        port_b["cidr"] = f"{U24_IPS[node_b][port_b['interface']]}/24"
        port_b["neighbor"] = node_a
        port_b["neighbor_ip"] = U24_IPS[node_a][port_a["interface"]]

    args.output.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    print(json.dumps({"source": str(args.source), "output": str(args.output), "links": len(endpoints)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
