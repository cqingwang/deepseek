#!/usr/bin/env python3
"""Create or remove a routed GRE control overlay over the physical ring."""

from __future__ import annotations

import argparse
import subprocess


NODES = {
    "spark-a": {
        "host": "192.168.2.180",
        "local": "10.12.1.1",
        "overlay": "10.200.0.1",
        "underlay_routes": [
            "10.23.1.0/24 via 10.12.1.2 dev enp1s0f0np0",
            "10.23.2.0/24 via 10.12.1.2 dev enp1s0f0np0",
        ],
        "hub": True,
        "peers": [
            {"name": "ctrl-b", "local": "10.12.1.1", "remote": "10.12.1.2", "key": "2", "overlay": "10.200.0.2"},
            {"name": "ctrl-c", "local": "10.12.1.1", "remote": "10.23.1.3", "key": "3", "overlay": "10.200.0.3"},
            {"name": "ctrl-d", "local": "10.14.1.1", "remote": "10.14.1.4", "key": "4", "overlay": "10.200.0.4"},
        ],
    },
    "spark-b": {
        "host": "192.168.2.161",
        "local": "10.12.1.2",
        "overlay": "10.200.0.2",
        "remote": "10.12.1.1",
        "key": "2",
        "underlay_routes": [],
        "hub": False,
    },
    "spark-c": {
        "host": "192.168.2.102",
        "local": "10.23.1.3",
        "overlay": "10.200.0.3",
        "remote": "10.12.1.1",
        "key": "3",
        "underlay_routes": [
            "10.12.1.0/24 via 10.23.1.2 dev enp1s0f1np1",
            "10.12.2.0/24 via 10.23.1.2 dev enp1s0f1np1",
        ],
        "hub": False,
    },
    "spark-d": {
        "host": "192.168.2.238",
        "local": "10.14.1.4",
        "overlay": "10.200.0.4",
        "remote": "10.14.1.1",
        "key": "4",
        "underlay_routes": [],
        "hub": False,
    },
}

def run_remote(host: str, script: str) -> None:
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", f"chan@{host}", "sudo", "-n", "bash", "-s"],
        input=script,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise SystemExit(f"{host}: {result.stderr.strip() or result.stdout.strip()}")
    if result.stdout.strip():
        print(f"{host}: {result.stdout.strip()}")


def clean_nft() -> str:
    return r"""
while read -r handle; do
  [ -n "$handle" ] && nft delete rule ip filter DOCKER-USER handle "$handle"
done < <(nft -a list chain ip filter DOCKER-USER | awk '/spark-control-overlay/{print $NF}')
"""


def apply(node: dict) -> None:
    script = ["set -euxo pipefail"]
    script.append("sysctl -w net.ipv4.ip_forward=1 >/dev/null")
    script.append(clean_nft())
    script.append(
        "nft add rule ip filter DOCKER-USER "
        'iifname { "enp1s0f0np0", "enp1s0f1np1", "enP2p1s0f0np0", "enP2p1s0f1np1" } '
        'oifname { "enp1s0f0np0", "enp1s0f1np1", "enP2p1s0f0np0", "enP2p1s0f1np1" } '
        "ip saddr 10.0.0.0/8 ip daddr 10.0.0.0/8 "
        'counter accept comment "spark-control-overlay"'
    )
    script.append(
        "nft add rule ip filter DOCKER-USER "
        'iifname "ctrl*" oifname "ctrl*" '
        "ip saddr 10.200.0.0/24 ip daddr 10.200.0.0/24 "
        'counter accept comment "spark-control-overlay-overlay"'
    )
    for name in ("ctrl0", "ctrl-b", "ctrl-c", "ctrl-d"):
        script.append(f"ip link del {name} 2>/dev/null || true")
    if node["hub"]:
        script.append("ip link add ctrl0 type dummy")
        script.append("ip link set ctrl0 up mtu 1400")
        script.append(f"ip addr add {node['overlay']}/32 dev ctrl0")
        for peer in node["peers"]:
            name = peer["name"]
            script.append(
                f"ip link add {name} type gre local {peer['local']} remote {peer['remote']} key {peer['key']}"
            )
            script.append(f"ip link set {name} up mtu 1400")
            script.append(
                f"ip route replace {peer['overlay']}/32 dev {name} src {node['overlay']}"
            )
    else:
        script.append(
            f"ip link add ctrl0 type gre local {node['local']} remote {node['remote']} key {node['key']}"
        )
        script.append("ip link set ctrl0 up mtu 1400")
        script.append(f"ip addr add {node['overlay']}/32 dev ctrl0")
        script.append("ip route replace 10.200.0.0/24 dev ctrl0")
    for route in node["underlay_routes"]:
        script.append(f"ip route replace {route}")
    script.append("ip -br addr show ctrl0")
    script.append("ip route show 10.200.0.0/24")
    run_remote(node["host"], "\n".join(script) + "\n")


def delete(node: dict) -> None:
    script = ["set -euxo pipefail"]
    script.append(clean_nft())
    for name in ("ctrl0", "ctrl-b", "ctrl-c", "ctrl-d"):
        script.append(f"ip link del {name} 2>/dev/null || true")
    for route in node["underlay_routes"]:
        destination = route.split()[0]
        script.append(f"ip route del {destination} 2>/dev/null || true")
    run_remote(node["host"], "\n".join(script) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("apply", "delete", "ping"))
    args = parser.parse_args()
    if args.action == "apply":
        for node in NODES.values():
            apply(node)
    elif args.action == "delete":
        for node in reversed(list(NODES.values())):
            delete(node)
    else:
        for node in NODES.values():
            result = subprocess.run(
                [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=8",
                    f"chan@{node['host']}",
                    "for t in 10.200.0.1 10.200.0.2 10.200.0.3 10.200.0.4; "
                    "do printf '%s ' \"$t\"; ping -c 3 -W 1 \"$t\" | tail -1; done",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            print(f"===== {node['host']} =====")
            print(result.stdout.strip() or result.stderr.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
