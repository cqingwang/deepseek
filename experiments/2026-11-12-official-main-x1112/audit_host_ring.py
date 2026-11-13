#!/usr/bin/env python3
"""Audit generated mesh scripts, live routes, TC rules and host ring inventory."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[4]
CONFIG = ROOT / "spark" / "config.yaml"
sys.path.insert(0, str(ROOT / "spark/ring/mesh"))
from rule_contract import (
    render_systemd_unit,
    route_expected_fragments,
    tc_expected_fragments,
)
PLAN_PATH = ROOT / "spark/ring/mesh/plan.py"
_plan_spec = importlib.util.spec_from_file_location("spark_ring_mesh_plan", PLAN_PATH)
PLAN = importlib.util.module_from_spec(_plan_spec)
assert _plan_spec.loader is not None
_plan_spec.loader.exec_module(PLAN)


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


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def generated_live_requirements(up_text: str) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Extract exact route/TC fields expected from the generated mesh-up script."""
    routes = {}
    tc_rules = {}
    for raw_line in up_text.splitlines():
        line = raw_line.strip()
        if line.startswith("ip route replace "):
            command = shlex.split(line)
            destination = command[3]
            routes[destination] = route_expected_fragments(command)
            continue
        if line.startswith("tc filter replace "):
            command = shlex.split(line)
            netdev = command[command.index("dev") + 1]
            preference = command[command.index("pref") + 1]
            tc_rules[f"{netdev}:{preference}"] = tc_expected_fragments(command)
    return routes, tc_rules


def generated_marker_devices(up_text: str) -> list[str]:
    """Return the exact RDMA devices the generated mesh-up starts markers on."""
    for raw_line in up_text.splitlines():
        line = raw_line.strip()
        if (
            line.startswith("for d in ")
            and "systemctl start spark-ring-mesh-marker@$d" in line
        ):
            return sorted(line[len("for d in ") :].split("; do", 1)[0].split())
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    spark_nodes = config["common"]["spark_nodes"]
    ring = config["common"]["ring"]
    mesh = ring["mesh"]
    dispatcher_source = ROOT / "spark/ring/mesh/90-spark-ring-mesh"
    expected_dispatcher_hash = sha256(dispatcher_source)
    runtime_root = str(mesh["runtime_root"])
    expected_unit_hashes = {
        "/etc/systemd/system/spark-ring-mesh.service": sha256_text(
            render_systemd_unit(
                ROOT / "spark/ring/mesh/systemd/spark-ring-mesh.service.in",
                runtime_root,
            )
        ),
        "/etc/systemd/system/spark-ring-mesh-marker@.service": sha256_text(
            render_systemd_unit(
                ROOT / "spark/ring/mesh/systemd/spark-ring-mesh-marker@.service.in",
                runtime_root,
            )
        ),
    }
    failures = []
    nodes = {}
    for name, record in sorted(
        config["common"]["ring"]["nodes"].items(), key=lambda item: int(item[1]["rank"])
    ):
        ssh_target = spark_nodes[name]["ssh"]
        host = ssh_target.split("@", 1)[1]
        generated_up = args.generated / f"mesh-up-{name}.sh"
        generated_down = args.generated / f"mesh-down-{name}.sh"
        up_text = generated_up.read_text(encoding="utf-8")
        expected_routes = sorted(set(re.findall(r"ip route show exact (\d+\.\d+\.\d+\.\d+/32)", up_text)))
        expected_tc = sorted(set(re.findall(r"tc -s filter show dev (\S+) ingress pref (\d+)", up_text)))
        route_requirements, tc_requirements = generated_live_requirements(up_text)
        expected_marker_devices = generated_marker_devices(up_text)
        expected_markers = {
            f"spark-ring-mesh-marker@{device}.service"
            for device in expected_marker_devices
        }
        if not route_requirements:
            failures.append(f"{name}: generated mesh-up has no verifiable route replacements")
        if not tc_requirements:
            failures.append(f"{name}: generated mesh-up has no verifiable TC replacements")
        if not expected_markers:
            failures.append(f"{name}: generated mesh-up has no marker device contract")
        host_inventory = {}
        try:
            host_inventory = PLAN.inventory(
                ssh_target,
                ring["roce_gid_index"],
                PLAN.configured_endpoints(ring["nodes"][name]),
            )
            failures.extend(
                PLAN.check_host(
                    ssh_target,
                    host_inventory,
                    ring["nodes"][name],
                    ring,
                )
            )
        except SystemExit as exc:
            failures.append(f"{name}: live fabric inventory failed: {exc}")
        installed_hashes = {}
        for installed in (
            f"/opt/spark-ring-mesh/mesh-up-{name}.sh",
            f"/opt/spark-ring-mesh/mesh-down-{name}.sh",
            "/opt/spark-ring-mesh/mesh-up.sh",
            "/opt/spark-ring-mesh/mesh-down.sh",
        ):
            installed_hashes[installed] = ssh(host, f"sha256sum {installed}").split()[0]
        service = ssh(host, "systemctl is-active spark-ring-mesh.service").strip()
        service_enabled = ssh(host, "systemctl is-enabled spark-ring-mesh.service").strip()
        network_manager = ssh(host, "systemctl is-active NetworkManager").strip()
        markers = [
            line.split()[0]
            for line in ssh(
                host,
                "systemctl list-units 'spark-ring-mesh-marker@*.service' "
                "--state=active --no-legend --no-pager",
            ).splitlines()
            if line.strip()
        ]
        try:
            dispatcher_hash = ssh(
                host,
                "test -x /etc/NetworkManager/dispatcher.d/90-spark-ring-mesh "
                "&& sha256sum /etc/NetworkManager/dispatcher.d/90-spark-ring-mesh",
            ).split()[0]
        except subprocess.CalledProcessError:
            dispatcher_hash = ""
            failures.append(
                f"{name}: NetworkManager mesh dispatcher is missing or not executable"
            )
        installed_unit_hashes = {}
        for installed, expected in expected_unit_hashes.items():
            try:
                installed_unit_hashes[installed] = ssh(
                    host,
                    f"test -r {shlex.quote(installed)} && sha256sum {shlex.quote(installed)}",
                ).split()[0]
            except subprocess.CalledProcessError:
                installed_unit_hashes[installed] = ""
            if installed_unit_hashes[installed] != expected:
                failures.append(
                    f"{name}: installed systemd unit differs from source: {installed}"
                )
        route_results = {}
        for route in expected_routes:
            route_results[route] = ssh(host, f"ip route show exact {route}").strip()
            if not route_results[route]:
                failures.append(f"{name}: missing live route {route}")
                continue
            missing = [
                fragment
                for fragment in route_requirements.get(route, [])
                if fragment not in route_results[route]
            ]
            if missing:
                failures.append(
                    f"{name}: live route {route} does not match generated next hop: {missing}"
                )
        tc_results = {}
        for netdev, preference in expected_tc:
            output = ssh(host, f"tc -s filter show dev {netdev} ingress pref {preference}")
            key = f"{netdev}:{preference}"
            tc_results[key] = output
            missing = [
                fragment
                for fragment in tc_requirements.get(key, [])
                if fragment not in output
            ]
            if missing:
                failures.append(
                    f"{name}: TC rule {key} does not match generated selector/action: {missing}"
                )
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
        if service_enabled != "enabled":
            failures.append(f"{name}: spark-ring-mesh.service is {service_enabled}")
        if network_manager != "active":
            failures.append(f"{name}: NetworkManager is {network_manager}")
        if set(markers) != expected_markers:
            failures.append(
                f"{name}: active marker units {sorted(markers)} "
                f"differ from generated devices {sorted(expected_markers)}"
            )
        if dispatcher_hash != expected_dispatcher_hash:
            failures.append(
                f"{name}: NetworkManager dispatcher differs from spark/ring/mesh source"
            )
        if probe_source != mesh["probe_source_sha256"]:
            failures.append(f"{name}: probe source hash differs from common.ring.mesh")
        nodes[name] = {
            "host": host,
            "rank": record["rank"],
            "host_inventory": host_inventory,
            "service": service,
            "service_enabled": service_enabled,
            "network_manager": network_manager,
            "active_markers": markers,
            "expected_markers": sorted(expected_markers),
            "dispatcher_sha256": dispatcher_hash,
            "expected_dispatcher_sha256": expected_dispatcher_hash,
            "installed_unit_sha256": installed_unit_hashes,
            "expected_unit_sha256": expected_unit_hashes,
            "expected_routes": route_results,
            "route_requirements": route_requirements,
            "tc_rules": tc_results,
            "tc_requirements": tc_requirements,
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
            "mtu": ring["mtu"],
            "roce_mtu": ring["roce_mtu"],
            "gid_index": ring["roce_gid_index"],
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
