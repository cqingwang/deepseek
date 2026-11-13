#!/usr/bin/env python3
"""Temporarily switch between the site /30 and official-style contiguous /24 ring."""

from __future__ import annotations

import argparse
import socket
import subprocess


LAYOUTS = {
    "spark-a": {
        "enp1s0f0np0": {"u30": "10.251.20.1/30", "u24": "10.40.0.1/24"},
        "enp1s0f1np1": {"u30": "10.251.20.14/30", "u24": "10.40.3.2/24"},
        "enP2p1s0f0np0": {"u30": "10.100.241.1/30", "u24": "10.41.0.1/24"},
        "enP2p1s0f1np1": {"u30": "10.100.241.14/30", "u24": "10.41.3.2/24"},
    },
    "spark-b": {
        "enp1s0f0np0": {"u30": "10.251.20.2/30", "u24": "10.40.0.2/24"},
        "enp1s0f1np1": {"u30": "10.251.20.5/30", "u24": "10.40.1.1/24"},
        "enP2p1s0f0np0": {"u30": "10.100.241.2/30", "u24": "10.41.0.2/24"},
        "enP2p1s0f1np1": {"u30": "10.100.241.5/30", "u24": "10.41.1.1/24"},
    },
    "spark-c": {
        "enp1s0f0np0": {"u30": "10.251.20.9/30", "u24": "10.40.2.1/24"},
        "enp1s0f1np1": {"u30": "10.251.20.6/30", "u24": "10.40.1.2/24"},
        "enP2p1s0f0np0": {"u30": "10.100.241.9/30", "u24": "10.41.2.1/24"},
        "enP2p1s0f1np1": {"u30": "10.100.241.6/30", "u24": "10.41.1.2/24"},
    },
    "spark-d": {
        "enp1s0f0np0": {"u30": "10.251.20.10/30", "u24": "10.40.2.2/24"},
        "enp1s0f1np1": {"u30": "10.251.20.13/30", "u24": "10.40.3.1/24"},
        "enP2p1s0f0np0": {"u30": "10.100.241.10/30", "u24": "10.41.2.2/24"},
        "enP2p1s0f1np1": {"u30": "10.100.241.13/30", "u24": "10.41.3.1/24"},
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("layout", choices=("u24", "u30"))
    args = parser.parse_args()

    host = socket.gethostname()
    if host not in LAYOUTS:
        raise SystemExit(f"unknown host {host!r}")
    for interface, addresses in LAYOUTS[host].items():
        subprocess.run(
            ["sudo", "-n", "ip", "-4", "addr", "flush", "dev", interface],
            check=True,
        )
        subprocess.run(
            ["sudo", "-n", "ip", "link", "set", "dev", interface, "up", "mtu", "9000"],
            check=True,
        )
        subprocess.run(
            ["sudo", "-n", "ip", "addr", "add", addresses[args.layout], "dev", interface],
            check=True,
        )
        print(interface, addresses[args.layout])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
