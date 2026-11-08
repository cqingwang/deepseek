#!/usr/bin/env python3
"""Spark 部署总控的配置与文件工具集。"""

from __future__ import annotations

import argparse
import contextlib
import io
import ipaddress
import json
import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    import yaml
except ModuleNotFoundError:
    yaml = None  # type: ignore[assignment]


FIELD_SEPARATOR = "\x1f"
NO_ARGS = "__NO_ARGS__"


def physical_ring_order(config: dict[str, Any]) -> list[str]:
    """按 common.ring rank 返回物理顺序；旧配置只在没有环拓扑时回退。"""
    common = config.get("common") or {}
    ring_nodes = (common.get("ring") or {}).get("nodes") or {}
    if isinstance(ring_nodes, dict) and ring_nodes:
        try:
            order = sorted(ring_nodes, key=lambda node: int(ring_nodes[node]["rank"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit("common.ring.nodes 必须为每个节点提供整数 rank") from exc
        if len(order) != len(set(order)):
            raise SystemExit("common.ring.nodes rank 重复")
        return [str(node) for node in order]
    head = str(common.get("head_node", "spark-a"))
    workers = common.get("worker_nodes") or [common.get("worker_node", "spark-b")]
    return [head, *(str(node) for node in workers)]


def ordered_cluster_names(config: dict[str, Any], head_name: str | None = None) -> tuple[str, list[str]]:
    """从物理环 ranks 派生默认 head/workers，允许 target 显式覆盖节点角色。"""
    order = physical_ring_order(config)
    head = str(head_name or order[0])
    if head not in order:
        raise SystemExit(f"节点 {head} 不在 common.ring.nodes 中")
    if head == order[0]:
        return head, [node for node in order if node != head]
    return head, [node for node in order if node != head]


def require_yaml() -> Any:
    if yaml is None:
        raise SystemExit("缺少 PyYAML，无法读取配置；请使用已安装 PyYAML 的 Python")
    return yaml


def load_config(config_path: Path) -> dict[str, Any]:
    yaml_module = require_yaml()
    try:
        with config_path.open(encoding="utf-8") as stream:
            config = yaml_module.safe_load(stream) or {}
    except FileNotFoundError as exc:
        raise SystemExit(f"配置文件不存在: {config_path}") from exc
    except yaml_module.YAMLError as exc:
        raise SystemExit(f"配置文件无法解析: {config_path}: {exc}") from exc
    if not isinstance(config, dict):
        raise SystemExit(f"配置文件根节点必须是对象: {config_path}")
    return config


def git_revision(path: Path) -> str:
    """返回工作树 HEAD；非 Git 目录或缺少 git 时返回空串。"""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def normalize_model_id(root: str, value: Any) -> str:
    model_id = str(value).strip()
    prefix = f"{root}/"
    if root and model_id.startswith(prefix):
        return model_id[len(prefix) :]
    return model_id


def engine_enabled(raw_value: Any) -> bool:
    """把 YAML 布尔值和常见字符串形式统一为严格的启用状态。"""
    if isinstance(raw_value, bool):
        return raw_value
    return str(raw_value).strip().lower() not in {"", "0", "false", "no", "off"}


def resolve_engine_or_common(
    engine: dict[str, Any], common: dict[str, Any], key: str, default: Any = None
) -> Any:
    """读取配方字段；未显式覆盖时统一继承 common 的共享定义。"""
    engine_value = engine.get(key)
    if engine_value is not None and engine_value != "":
        return engine_value
    common_value = common.get(key)
    if common_value is not None and common_value != "":
        return common_value
    return default


def configured_runtime_rank_order(
    engine: dict[str, Any],
    engine_id: str,
    topology: dict[str, dict[str, str]],
    nnodes: int,
    head_name: str,
) -> list[str] | None:
    """Reject TP4 rank permutations; execution ranks follow the physical ring SSOT."""
    if nnodes != 4:
        return None
    if len(topology) != 4:
        raise SystemExit(f"引擎 {engine_id} 的 TP4 rank 顺序要求 common.ring 恰好登记四个节点")
    physical_order = [
        node for node, _record in sorted(topology.items(), key=lambda pair: int(pair[1]["rank"]))
    ]
    if "head_node" in engine:
        raise SystemExit(
            f"引擎 {engine_id} 不得重复配置 head_node；TP4 head 必须直接继承 common.ring rank 0"
        )
    if "worker_nodes" in engine:
        raise SystemExit(
            f"引擎 {engine_id} 不得重复配置 worker_nodes；TP4 worker 顺序必须直接继承 common.ring"
        )
    if head_name != physical_order[0]:
        raise SystemExit(
            f"引擎 {engine_id} 的 TP4 head 必须是 common.ring rank 0: {physical_order[0]}"
        )
    raw_order = engine.get("runtime_rank_order")
    if raw_order is not None:
        raise SystemExit(
            f"引擎 {engine_id} 不得配置 runtime_rank_order；TP4 runtime rank 必须直接继承 common.ring 物理 rank"
        )
    return None


def parse_api_endpoint(raw_url: Any) -> tuple[str, int]:
    api_url = str(raw_url or "").strip()
    parsed = urlparse(api_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SystemExit(f"引擎或 common.api_url 必须是包含 host 的 HTTP(S) URL: {api_url}")
    try:
        port = parsed.port
    except ValueError as exc:
        raise SystemExit(f"引擎或 common.api_url 端口无效: {api_url}") from exc
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.hostname, port


def parse_command(raw_command: Any, engine_id: str, field_name: str) -> tuple[str, str]:
    try:
        command_parts = shlex.split(str(raw_command or ""))
    except ValueError as exc:
        raise SystemExit(f"引擎 {engine_id} 的 {field_name} 不是合法命令: {exc}") from exc
    if not command_parts:
        raise SystemExit(f"引擎 {engine_id} 缺少 {field_name} 命令")
    return command_parts[0], FIELD_SEPARATOR.join(command_parts[1:])


def validate_output_values(values: list[str], context: str) -> None:
    if any("\t" in value or "\n" in value for value in values):
        raise SystemExit(f"{context}包含非法换行或制表符")


def target_container_names(engine_id: str, nnodes: int) -> list[str]:
    """按 target ID 派生所有 rank 容器名，生命周期入口必须复用此契约。"""
    if not engine_id or nnodes < 1:
        raise SystemExit(f"引擎 {engine_id} 的 nnodes 必须为正整数")
    return [f"{engine_id}-r{rank}" for rank in range(nnodes)]


def resolve_cluster_nodes(
    config: dict[str, Any],
    head_name_override: str | None = None,
    worker_names_override: list[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """从 common.spark_nodes 和 common.ring rank 派生默认节点顺序。"""
    common = config.get("common") or {}
    nodes = common.get("spark_nodes")
    if nodes is None:
        return config.get("head") or {}, [config.get("worker") or {}]
    if not isinstance(nodes, dict):
        raise SystemExit("common.spark_nodes 必须是以节点名为键的对象")
    default_order = physical_ring_order(config)
    head_name = str(head_name_override or default_order[0])
    head = nodes.get(head_name)
    worker_names = worker_names_override or [name for name in default_order if name != head_name]
    if not isinstance(worker_names, list) or not worker_names:
        raise SystemExit("common.ring.nodes 必须提供至少一个 worker 节点")
    workers = [nodes.get(str(worker_name)) for worker_name in worker_names]
    if not isinstance(head, dict) or any(not isinstance(worker, dict) for worker in workers):
        raise SystemExit(f"common.spark_nodes 缺少 head/worker 节点: {head_name}, {worker_names}")
    return head, workers  # type: ignore[return-value]


def resolve_head_worker(config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """从 common.spark_nodes 派生现有双节点启动协议。"""
    head, workers = resolve_cluster_nodes(config)
    return head, workers[0]


def resolve_engine_workers(
    config: dict[str, Any], engine: dict[str, Any], ordered_workers: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """按引擎节点组裁剪 worker；未声明节点组时保持旧双节点回退。"""
    nnodes = int(engine.get("nnodes", 1))
    if nnodes >= 4:
        return ordered_workers
    configured_workers = engine.get("worker_nodes")
    if isinstance(configured_workers, list) and configured_workers:
        return ordered_workers[: max(nnodes - 1, 0)]
    if ordered_workers:
        return [ordered_workers[0]]
    raise SystemExit("common.ring.nodes 未提供默认 worker 节点")


def load_ring_topology(config_path: Path) -> dict[str, dict[str, str]]:
    """加载并校验 common.ring 的物理双平面拓扑。"""
    config = load_config(config_path)
    common = config.get("common") or {}
    management_nodes = common.get("spark_nodes") or {}
    ring = common.get("ring") or {}
    ring_nodes = ring.get("nodes") or {}
    mesh = ring.get("mesh")
    if not isinstance(mesh, dict):
        raise SystemExit("common.ring.mesh 必须是对象，并定义通用环网主机运行时")
    for field in ("runtime_root", "state_root"):
        value = str(mesh.get(field, "")).strip()
        if not value or not os.path.isabs(value):
            raise SystemExit(f"common.ring.mesh.{field} 必须是非空绝对路径")
    runtime_assets_root = str(common.get("runtime_assets_root", "")).rstrip("/")
    mesh_runtime_root = str(mesh["runtime_root"]).rstrip("/")
    if runtime_assets_root and (
        mesh_runtime_root == runtime_assets_root
        or mesh_runtime_root.startswith(runtime_assets_root + "/")
    ):
        raise SystemExit("common.ring.mesh.runtime_root 不能位于 target runtime_assets 下")
    sparkring_commit = str(mesh.get("sparkring_commit", "")).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sparkring_commit):
        raise SystemExit("common.ring.mesh.sparkring_commit 必须是完整 40 位提交 SHA")
    probe_source_sha256 = str(mesh.get("probe_source_sha256", "")).strip()
    if not re.fullmatch(r"[0-9a-f]{64}", probe_source_sha256):
        raise SystemExit("common.ring.mesh.probe_source_sha256 必须是完整 SHA-256")
    if not isinstance(management_nodes, dict) or not isinstance(ring_nodes, dict):
        raise SystemExit("common.spark_nodes 和 common.ring.nodes 必须是对象")
    try:
        mtu = int(ring["mtu"])
        roce_gid_index = int(ring["roce_gid_index"])
        int(ring["roce_mtu"])
        int(ring["hairpin_num_queues"])
        int(ring["hairpin_queue_size"])
        str(ring["flow_steering_mode"])
        str(ring["hw_tc_offload"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit("common.ring 必须提供有效 mtu、roce_mtu、roce_gid_index 和 hairpin_queue_size") from exc
    if mtu < 1500 or roce_gid_index < 0 or int(ring["roce_mtu"]) < 1 or int(ring["hairpin_queue_size"]) < 1:
        raise SystemExit("common.ring 的 MTU、GID index 或 hairpin queue 配置无效")
    if set(ring_nodes) != set(management_nodes):
        raise SystemExit("common.ring.nodes 与 common.spark_nodes 必须登记相同节点")
    topology: dict[str, dict[str, str]] = {}
    ranks: set[int] = set()
    for node, raw_node in ring_nodes.items():
        if not isinstance(raw_node, dict):
            raise SystemExit(f"环拓扑节点 {node} 必须是对象")
        management = management_nodes.get(node) or {}
        ports_by_plane = [raw_node.get("ports"), raw_node.get("ports2")]
        if not isinstance(management, dict) or any(not isinstance(ports, list) or len(ports) != 2 for ports in ports_by_plane):
            raise SystemExit(f"环拓扑节点 {node} 必须包含两个 ports、两个 ports2 和管理面节点")
        try:
            rank = int(raw_node.get("rank"))
        except (TypeError, ValueError) as exc:
            raise SystemExit(f"环拓扑节点 {node} 的 rank 无效: {raw_node.get('rank')}") from exc
        if node in topology or rank in ranks:
            raise SystemExit(f"环拓扑节点或 rank 重复: {node}, rank={rank}")
        node_hcas: list[str] = []
        record: dict[str, str] = {
            "node": str(node),
            "role": "head" if rank == 0 else "worker",
            "ssh": str(management.get("ssh", "")),
            "management_ip": str(management.get("management_ip", "")),
            "management_if": str(management.get("management_if", "")),
            "mtu": str(mtu),
            "roce_mtu": str(ring["roce_mtu"]),
            "roce_gid_index": str(roce_gid_index),
        "hairpin_num_queues": str(ring["hairpin_num_queues"]),
        "hairpin_queue_size": str(ring["hairpin_queue_size"]),
        "flow_steering_mode": str(ring["flow_steering_mode"]),
        "hw_tc_offload": str(ring["hw_tc_offload"]),
            "rank": str(rank),
        }
        for plane_index, raw_ports in enumerate(ports_by_plane):
            plane = "ports" if plane_index == 0 else "ports2"
            for index, raw_port in enumerate(raw_ports):
                if not isinstance(raw_port, dict):
                    raise SystemExit(f"环拓扑节点 {node} 的 {plane}[{index}] 必须是对象")
                required = ("interface", "cidr", "neighbor", "neighbor_ip", "hca")
                if any(not str(raw_port.get(key, "")).strip() for key in required):
                    raise SystemExit(f"环拓扑节点 {node} 的 {plane}[{index}] 字段不完整")
                hca = str(raw_port["hca"]).strip()
                if hca in node_hcas:
                    raise SystemExit(f"环拓扑节点 {node} 重复登记 RDMA 设备: {hca}")
                node_hcas.append(hca)
                try:
                    local = ipaddress.ip_interface(str(raw_port["cidr"]))
                    peer = ipaddress.ip_address(str(raw_port["neighbor_ip"]))
                except ValueError as exc:
                    raise SystemExit(f"环拓扑节点 {node} 的 {plane}[{index}] 地址无效: {raw_port}") from exc
                if local.version != 4 or peer.version != 4 or peer not in local.network:
                    raise SystemExit(f"环拓扑节点 {node} 的 {plane}[{index}] 邻居地址不在本地 IPv4 子网: {raw_port}")
                prefix = "fabric" if plane_index == 0 else "fabric2"
                record[f"{prefix}_if{index}"] = str(raw_port["interface"])
                record[f"{prefix}_ip{index}"] = str(local)
                record[f"{prefix}_neighbor{index}"] = str(raw_port["neighbor"])
                record[f"{prefix}_neighbor_ip{index}"] = str(peer)
                record[f"{prefix}_hca{index}"] = hca
                if plane_index == 0:
                    record[f"local_ip{index}"] = str(local)
                    record[f"neighbor{index}"] = str(raw_port["neighbor"])
                    record[f"neighbor_ip{index}"] = str(peer)
            if plane_index == 1:
                for index in range(2):
                    if record[f"fabric_neighbor{index}"] != record[f"fabric2_neighbor{index}"]:
                        raise SystemExit(f"环拓扑节点 {node} 的 ports2[{index}] 邻居与 ports[{index}] 不一致")
        if len(node_hcas) != 4:
            raise SystemExit(f"环拓扑节点 {node} 的 ports/ports2 必须各自登记两个唯一 RDMA 设备")
        record["hca_all"] = ",".join(node_hcas)
        topology[str(node)] = record
        ranks.add(rank)
    if len(topology) != 4 or ranks != {0, 1, 2, 3}:
        raise SystemExit("四节点环拓扑必须恰好包含 rank 0..3")
    expected_hcas = topology[physical_ring_order(config)[0]]["hca_all"]
    for node, record in topology.items():
        interface_names: set[str] = set()
        if record["hca_all"] != expected_hcas:
            raise SystemExit("双平面 RoCE 要求 common.ring.nodes 各节点端口 HCA 顺序一致")
        for index in range(2):
            if record[f"fabric_neighbor{index}"] not in topology:
                raise SystemExit(f"环拓扑节点 {node} 的邻居不存在: {record[f'fabric_neighbor{index}']}")
            if record[f"fabric_neighbor{index}"] == record[f"fabric_neighbor{1 - index}"]:
                raise SystemExit(f"环拓扑节点 {node} 的两个物理端口不能连接同一邻居")
            if record[f"fabric_neighbor{index}"] != record[f"fabric2_neighbor{index}"]:
                raise SystemExit(f"环拓扑节点 {node} 的双平面邻居不一致: port{index}")
            for plane in ("fabric", "fabric2"):
                interface = record[f"{plane}_if{index}"]
                if interface in interface_names:
                    raise SystemExit(f"环拓扑接口重复: {interface}")
                interface_names.add(interface)
                peer_name = record[f"{plane}_neighbor{index}"]
                peer_record = topology.get(peer_name)
                if peer_record is None:
                    raise SystemExit(f"环拓扑节点 {node} 的邻居不存在: {peer_name}")
                reciprocal = [
                    peer_index
                    for peer_index in (0, 1)
                    if peer_record[f"{plane}_neighbor{peer_index}"] == node
                    and peer_record[f"{plane}_neighbor_ip{peer_index}"] == record[f"{plane}_ip{index}"].split("/", 1)[0]
                ]
                if len(reciprocal) != 1:
                    raise SystemExit(f"环拓扑节点 {node} 的 {plane}[{index}] 邻居地址/边不互易")
                local = ipaddress.ip_interface(record[f"{plane}_ip{index}"])
                remote = ipaddress.ip_interface(peer_record[f"{plane}_ip{reciprocal[0]}"])
                if record[f"{plane}_neighbor_ip{index}"] != str(remote.ip) or local.network != remote.network:
                    raise SystemExit(f"环拓扑节点 {node} 的 {plane}[{index}] 与邻居子网/地址不一致")
        neighbor_ranks = {
            int(topology[record[f"fabric_neighbor{index}"]]["rank"])
            for index in (0, 1)
        }
        if neighbor_ranks != {(int(record["rank"]) - 1) % 4, (int(record["rank"]) + 1) % 4}:
            raise SystemExit(f"环拓扑节点 {node} 的邻居不构成 common.ring rank 环")
    return topology


def command_ring_topology(arguments: argparse.Namespace) -> int:
    """输出兼容主机验收脚本的环记录；记录源仍是 config.yaml。"""
    config_path = Path(arguments.config)
    config = load_config(config_path)
    topology = load_ring_topology(config_path)
    if arguments.json:
        print(json.dumps({"ring": config["common"]["ring"], "nodes": topology}, sort_keys=True))
        return 0
    fields = (
        "node", "role", "management_ip", "management_if", "fabric_if0", "local_ip0",
        "neighbor0", "neighbor_ip0", "fabric_if1", "local_ip1", "neighbor1",
        "neighbor_ip1", "hca_all", "mtu", "rank",
    )
    for node in sorted(topology, key=lambda item: int(topology[item]["rank"])):
        print("\t".join(topology[node][field] for field in fields))
    return 0


def command_ring_local_fabric(arguments: argparse.Namespace) -> int:
    """输出本机各 fabric netdev/HCA 对及队列参数，供 mesh 服务消费。"""
    config_path = Path(arguments.config)
    config = load_config(config_path)
    topology = load_ring_topology(config_path)
    node = str(arguments.node or os.uname().nodename)
    record = topology.get(node)
    if record is None:
        raise SystemExit(f"本机节点未登记在 common.ring.nodes: {node}")
    for plane in ("fabric", "fabric2"):
        for index in (0, 1):
            print("\t".join((
                record[f"{plane}_if{index}"],
                record[f"{plane}_hca{index}"],
                record["mtu"],
                record["roce_gid_index"],
                record["hairpin_queue_size"],
            )))
    return 0


def command_ring_fabric_prefixes(arguments: argparse.Namespace) -> int:
    """输出每个节点双 fabric 平面的 IPv4 前缀，仍由 common.ring 唯一派生。"""
    config = load_config(Path(arguments.config))
    common = config.get("common") or {}
    ring_nodes = (common.get("ring") or {}).get("nodes") or {}
    topology = load_ring_topology(Path(arguments.config))
    for node in sorted(topology, key=lambda item: int(topology[item]["rank"])):
        raw_node = ring_nodes.get(node) or {}
        prefixes: list[str] = []
        for plane in ("ports", "ports2"):
            ports = raw_node.get(plane) or []
            if not isinstance(ports, list) or len(ports) != 2:
                raise SystemExit(f"环拓扑节点 {node} 必须为 {plane} 配置两个端口")
            for port in ports:
                try:
                    interface = ipaddress.ip_interface(str(port["cidr"]))
                except (KeyError, ValueError) as exc:
                    raise SystemExit(f"环拓扑节点 {node} 的 {plane} 地址无效: {port}") from exc
                prefix = ".".join(str(interface.ip).split(".")[:3]) + "."
                if prefix not in prefixes:
                    prefixes.append(prefix)
        if len(prefixes) != 2:
            raise SystemExit(f"环拓扑节点 {node} 必须派生两个不同的 fabric 前缀: {prefixes}")
        print(f"{node}\t{' '.join(prefixes)}")
    return 0


def command_ring_transfer_route(arguments: argparse.Namespace) -> int:
    """按 common.ring rank 输出物理相邻传输路径。"""
    config_path = Path(arguments.config)
    config = load_config(config_path)
    topology = load_ring_topology(config_path)
    route = physical_ring_order(config)
    if len(route) != 4 or len(set(route)) != 4:
        raise SystemExit("镜像环路必须由 common.ring rank 0..3 唯一组成")
    if any(name not in topology for name in route):
        raise SystemExit(f"镜像环路节点未全部登记在 common.ring: {route}")
    if [int(topology[name]["rank"]) for name in route] != [0, 1, 2, 3]:
        raise SystemExit(f"镜像环路顺序必须与 common.ring rank 0..3 一致: {route}")

    for source_name, target_name in zip(route, route[1:]):
        source = topology[source_name]
        edge = next(
            (index for index in (0, 1) if source[f"neighbor{index}"] == target_name),
            None,
        )
        if edge is None:
            raise SystemExit(f"common.ring rank 不是物理环相邻路径: {source_name} -> {target_name}")
        print(
            "\t".join(
                (
                    source_name,
                    source["management_ip"],
                    source[f"fabric_if{edge}"],
                    source[f"local_ip{edge}"].split("/", 1)[0],
                    target_name,
                    topology[target_name]["management_ip"],
                    source[f"neighbor_ip{edge}"],
                )
            )
        )
    return 0


def target_runtime_ring_order(
    config: dict[str, Any], target: str, topology: dict[str, dict[str, str]]
) -> list[str]:
    physical_order = physical_ring_order(config)
    if not target:
        return physical_order
    engines = config.get("engines") or {}
    engine = engines.get(target) if isinstance(engines, dict) else None
    if not isinstance(engine, dict):
        raise SystemExit(f"环网 target 未登记: {target}")
    nnodes = int(engine.get("nnodes", 1))
    if nnodes != 4:
        raise SystemExit(f"环网 target rank 映射仅支持 TP4: {target}")
    head_name = str(engine.get("head_node", physical_order[0]))
    return configured_runtime_rank_order(engine, target, topology, nnodes, head_name) or physical_order


def command_ring_peer_hca(arguments: argparse.Namespace) -> int:
    """输出按物理或 target runtime rank 编号的环邻居 HCA 映射。"""
    config = load_config(Path(arguments.config))
    topology = load_ring_topology(Path(arguments.config))
    runtime_order = target_runtime_ring_order(config, str(arguments.target), topology)
    runtime_rank_by_node = {node: rank for rank, node in enumerate(runtime_order)}
    for node in sorted(topology, key=lambda item: int(topology[item]["rank"])):
        print(f"{node}\t{ring_peer_hca(topology[node], topology, runtime_rank_by_node)}")
    return 0


def command_ring_runtime_fabric(arguments: argparse.Namespace) -> int:
    """按 target runtime rank 输出每个节点前后邻居的 SSOT 接口/HCA。"""
    config = load_config(Path(arguments.config))
    topology = load_ring_topology(Path(arguments.config))
    runtime_order = target_runtime_ring_order(config, str(arguments.target), topology)

    for rank, node in enumerate(runtime_order):
        record = topology[node]
        previous_node = runtime_order[(rank - 1) % len(runtime_order)]
        next_node = runtime_order[(rank + 1) % len(runtime_order)]
        previous_ports = [
            port for port in (0, 1) if record[f"neighbor{port}"] == previous_node
        ]
        next_ports = [port for port in (0, 1) if record[f"neighbor{port}"] == next_node]
        if len(previous_ports) != 1 or len(next_ports) != 1:
            raise SystemExit(
                f"target runtime 顺序不是 common.ring 物理环: node={node}, "
                f"prev={previous_node}, next={next_node}"
            )
        previous_port = previous_ports[0]
        next_port = next_ports[0]
        print(
            "\t".join(
                (
                    node,
                    str(rank),
                    previous_node,
                    record[f"fabric_if{previous_port}"],
                    ring_hca_for_port(record, previous_port),
                    next_node,
                    record[f"fabric_if{next_port}"],
                    ring_hca_for_port(record, next_port),
                    record["hca_all"],
                )
            )
        )
    return 0


def build_ring_netplan(config_path: Path, node: str) -> dict[str, Any]:
    """Validate and render only this node's primary/secondary fabric interfaces."""
    config = load_config(config_path)
    common = config.get("common") or {}
    ring = common.get("ring") or {}
    ring_nodes = ring.get("nodes") or {}
    mtu = int(ring.get("mtu", 0))
    if mtu < 1500 or node not in ring_nodes or len(ring_nodes) != 4:
        raise SystemExit(f"环网节点或 MTU 配置无效: node={node}, mtu={mtu}")

    parsed_ports: dict[str, dict[str, list[dict[str, Any]]]] = {}
    all_interfaces: set[tuple[str, str]] = set()
    all_addresses: set[ipaddress.IPv4Address] = set()
    for node_name, node_config in ring_nodes.items():
        parsed_ports[node_name] = {}
        for plane in ("ports", "ports2"):
            plane_ports = node_config.get(plane) or []
            if not isinstance(plane_ports, list) or len(plane_ports) != 2:
                raise SystemExit(f"环网节点 {node_name} 必须为 {plane} 配置两个端口")
            parsed_ports[node_name][plane] = plane_ports
            for port in plane_ports:
                interface = str(port.get("interface", "")).strip()
                neighbor = str(port.get("neighbor", "")).strip()
                interface_key = (str(node_name), interface)
                if not interface or interface_key in all_interfaces or neighbor not in ring_nodes:
                    raise SystemExit(f"环网接口或邻居无效: {node_name} {plane} {port}")
                all_interfaces.add(interface_key)
                try:
                    local = ipaddress.ip_interface(str(port["cidr"]))
                    peer = ipaddress.ip_address(str(port["neighbor_ip"]))
                except (KeyError, ValueError) as exc:
                    raise SystemExit(f"环网地址无效: {node_name} {plane} {port}") from exc
                if local.version != 4 or local.network.prefixlen != 30 or peer not in local.network:
                    raise SystemExit(f"环网链路必须使用同网段 IPv4 /30: {node_name} {plane} {port}")
                if local.ip in all_addresses:
                    raise SystemExit(f"环网地址重复: {local.ip}")
                all_addresses.add(local.ip)

    for plane in ("ports", "ports2"):
        for node_name, planes in parsed_ports.items():
            for port in planes[plane]:
                local = ipaddress.ip_interface(str(port["cidr"]))
                peer_ip = ipaddress.ip_address(str(port["neighbor_ip"]))
                reciprocal = [
                    candidate
                    for candidate in parsed_ports[str(port["neighbor"])][plane]
                    if candidate.get("neighbor") == node_name
                ]
                if len(reciprocal) != 1:
                    raise SystemExit(f"环网链路缺少唯一反向邻接: {node_name} {plane} {port}")
                peer = reciprocal[0]
                if (
                    ipaddress.ip_interface(str(peer["cidr"])).ip != peer_ip
                    or ipaddress.ip_address(str(peer["neighbor_ip"])) != local.ip
                    or ipaddress.ip_interface(str(peer["cidr"])).network != local.network
                ):
                    raise SystemExit(f"环网两端地址不匹配: {node_name} {plane} {port}")

    yaml_module = require_yaml()
    ethernets: dict[str, Any] = {}
    for plane in ("ports", "ports2"):
        for port in parsed_ports[node][plane]:
            interface = str(port["interface"])
            ethernets[interface] = {
                "match": {"name": interface},
                "addresses": [str(port["cidr"])],
                "dhcp4": False,
                "mtu": mtu,
            }
    return {"network": {"version": 2, "renderer": "NetworkManager", "ethernets": ethernets}}


def command_render_ring_netplan(arguments: argparse.Namespace) -> int:
    result = build_ring_netplan(Path(arguments.config), arguments.node)
    yaml_module = require_yaml()
    print(yaml_module.safe_dump(result, sort_keys=False), end="")
    return 0


def resolve_direct_fabric_pair(
    topology: dict[str, dict[str, str]], head_name: str, worker_name: str
) -> tuple[str, str]:
    """从唯一物理环拓扑取得双节点直连两端的 /30 地址。"""
    record = topology.get(head_name) or {}
    for port in (0, 1):
        if record.get(f"neighbor{port}") != worker_name:
            continue
        head_address = record.get(f"local_ip{port}", "")
        worker_address = record.get(f"neighbor_ip{port}", "")
        return head_address.split("/", 1)[0], worker_address.split("/", 1)[0]
    return "", ""


def ring_hca_for_port(record: dict[str, str], port: int) -> str:
    """读取同一物理邻居在两个 fabric plane 上显式登记的 RDMA 设备。"""
    if port not in (0, 1):
        raise SystemExit(f"节点 {record['node']} 的物理环端口索引无效: {port}")
    return f"{record[f'fabric_hca{port}']},{record[f'fabric2_hca{port}']}"


def ring_peer_hca(
    record: dict[str, str],
    topology: dict[str, dict[str, str]],
    runtime_rank_by_node: dict[str, int] | None = None,
) -> str:
    """输出 ring-only NCCL 使用的 `peer_rank=hca[,hca];...` 映射。"""
    mappings = []
    for port in (0, 1):
        neighbor = record[f"neighbor{port}"]
        if neighbor not in topology:
            raise SystemExit(f"节点 {record['node']} 的环邻居不存在: {neighbor}")
        if runtime_rank_by_node is not None and neighbor not in runtime_rank_by_node:
            raise SystemExit(f"节点 {record['node']} 的运行 rank 表缺少邻居: {neighbor}")
        peer_rank = (
            runtime_rank_by_node[neighbor]
            if runtime_rank_by_node is not None
            else int(topology[neighbor]["rank"])
        )
        mappings.append(f"{peer_rank}={ring_hca_for_port(record, port)}")
    return ";".join(mappings)


def ring_b12x_peer_hca_maps(
    topology: dict[str, dict[str, str]], runtime_rank_by_node: dict[str, int]
) -> str:
    """从物理邻居关系派生 B12X 三对端点的双路径 HCA 索引。"""
    if set(topology) != set(runtime_rank_by_node) or set(runtime_rank_by_node.values()) != set(range(4)):
        raise SystemExit("B12X ring map 必须覆盖四个物理节点和 runtime rank 0..3")
    runtime_rows = [""] * 4
    for node, record in topology.items():
        source_rank = int(record["rank"])
        clockwise_rank = (source_rank + 1) % 4
        counter_clockwise_rank = (source_rank - 1) % 4
        node_by_rank = {int(item["rank"]): name for name, item in topology.items()}
        clockwise = node_by_rank[clockwise_rank]
        counter_clockwise = node_by_rank[counter_clockwise_rank]
        clockwise_ports = [
            index for index in (0, 1) if record[f"fabric_neighbor{index}"] == clockwise
        ]
        counter_clockwise_ports = [
            index for index in (0, 1) if record[f"fabric_neighbor{index}"] == counter_clockwise
        ]
        if len(clockwise_ports) != 1 or len(counter_clockwise_ports) != 1:
            raise SystemExit(f"节点 {node} 的 B12X 方向端口无法从 common.ring 唯一派生")
        cw_port, ccw_port = clockwise_ports[0], counter_clockwise_ports[0]
        peers = {
            runtime_rank_by_node[clockwise]: f"{cw_port}/{cw_port + 2}",
            runtime_rank_by_node[counter_clockwise]: f"{ccw_port}/{ccw_port + 2}",
            runtime_rank_by_node[node_by_rank[(source_rank + 2) % 4]]: (
                f"{cw_port}/{ccw_port + 2}"
                if source_rank < 2
                else f"{ccw_port}/{cw_port + 2}"
            ),
        }
        runtime_rank = runtime_rank_by_node[node]
        runtime_rows[runtime_rank] = ",".join(
            f"{peer_rank}={hca_pair}" for peer_rank, hca_pair in sorted(peers.items())
        )
    return ";".join(runtime_rows)


def ring_nccl_v5_dev_map(b12x_peer_hca_maps: str) -> str:
    """Convert topology-derived B12X pairs into v5 ring-adjacent dev maps."""
    rows: list[str] = []
    for rank, segment in enumerate(b12x_peer_hca_maps.split(";")):
        adjacent = {(rank + 1) % 4, (rank - 1) % 4}
        entries: list[str] = []
        for item in segment.split(","):
            peer_text, separator, pair = item.partition("=")
            if not separator or not peer_text.isdigit():
                raise SystemExit(f"B12X peer map 项非法: {item}")
            peer = int(peer_text)
            if peer not in adjacent:
                continue
            first, pair_separator, second = pair.partition("/")
            if not pair_separator or not first.isdigit() or not second.isdigit():
                raise SystemExit(f"B12X peer map 双口索引非法: {item}")
            entries.append(f"{peer}={first},{second}")
        if len(entries) != 2:
            raise SystemExit(f"B12X peer map rank {rank} 未给出两个环邻对端: {segment}")
        rows.append(f"r{rank}:" + ";".join(entries))
    if len(rows) != 4:
        raise SystemExit(f"B12X peer map 必须覆盖 rank 0..3: {b12x_peer_hca_maps}")
    return "|".join(rows)


def ring_subnet_prefix_len(topology: dict[str, dict[str, str]]) -> str:
    """从两平面所有端口 CIDR 取唯一子网前缀；混合前缀 fail-fast。"""
    prefixes = {
        ipaddress.ip_interface(record[f"{plane}_ip{index}"]).network.prefixlen
        for record in topology.values()
        for plane in ("fabric", "fabric2")
        for index in (0, 1)
    }
    if len(prefixes) != 1:
        raise SystemExit(f"NCCL subnet-aware routing 要求双平面使用统一 IPv4 前缀: {sorted(prefixes)}")
    return str(prefixes.pop())


def ring_primary_addr_range(topology: dict[str, dict[str, str]]) -> str:
    """把 primary fabric 的已登记子网归并为一个精确 CIDR 范围。"""
    networks = {
        ipaddress.ip_interface(topology[node][f"fabric_ip{index}"]).network
        for node in topology
        for index in (0, 1)
    }
    collapsed = list(ipaddress.collapse_addresses(networks))
    if len(collapsed) != 1:
        raise SystemExit(
            "switchless NCCL addr range 要求 common.ring primary fabric 子网可归并为单一 CIDR: "
            f"{sorted(map(str, networks))}"
        )
    return str(collapsed[0])


def append_managed_env(raw_env: str, additions: dict[str, str], context: str) -> str:
    """拒绝受管参数被手工重复，再按唯一派生值追加。"""
    tokens = raw_env.split()
    managed_keys = set(additions)
    duplicated = [token.split("=", 1)[0] for token in tokens if token.split("=", 1)[0] in managed_keys]
    if duplicated:
        raise SystemExit(f"{context} 中含受 common.ring 管理的重复参数: {sorted(set(duplicated))}")
    return " ".join([*tokens, *(f"{key}={value}" for key, value in additions.items())])


def nccl_env_keys(raw_env: str, context: str) -> set[str]:
    """收集 NCCL 环境变量，并拒绝同一字符串内的重复赋值。"""
    keys: list[str] = []
    for token in raw_env.split():
        key, separator, _value = token.partition("=")
        if separator and key.startswith("NCCL_"):
            keys.append(key)
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise SystemExit(f"{context} 中 NCCL 参数重复赋值: {duplicates}")
    return set(keys)


def direct_peer_hca(
    record: dict[str, str], peer_name: str, peer_rank: int
) -> str:
    """为双节点 TP 将物理直连边映射到 launcher 的局部 rank。"""
    for port in (0, 1):
        if record.get(f"neighbor{port}") == peer_name:
            return f"{peer_rank}={ring_hca_for_port(record, port)}"
    raise SystemExit(
        f"双节点 TP 要求物理直连：{record.get('node', '?')} -> {peer_name} 不在 common.ring 中"
    )


def command_check_dependency(_arguments: argparse.Namespace) -> int:
    require_yaml()
    return 0


def command_query_model(arguments: argparse.Namespace) -> int:
    config = load_config(Path(arguments.config))
    common = config.get("common") or {}
    root = str(common.get("model_path", "")).rstrip("/")
    runtime_assets_root = str(common.get("runtime_assets_root", "")).rstrip("/")
    if not runtime_assets_root.startswith("/"):
        raise SystemExit("common.runtime_assets_root 必须是绝对路径")
    default_model = str(common.get("default_model", ""))
    engines = config.get("engines") or {}
    matches: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    selection = str(arguments.selection or "").strip()
    target = str(arguments.target or "").strip()
    default_engine = str(common.get("default_engine", "")).strip()
    selected_engine_id = target or (default_engine if not selection else "")
    if selected_engine_id:
        if selected_engine_id not in engines:
            raise SystemExit(f"目标引擎未在 config.yaml 中登记: {selected_engine_id}")
        selected_engine = engines[selected_engine_id] or {}
        target_items = [
            item for item in selected_engine.get("models") or [] if str(item.get("role", "")) == "target"
        ]
        if target:
            wanted_model = normalize_model_id(root, selection or default_model)
            if not selection and len(target_items) == 1:
                wanted_model = normalize_model_id(root, target_items[0].get("id", ""))
            target_items = [
                item
                for item in target_items
                if normalize_model_id(root, item.get("id", "")) == wanted_model
            ]
        if len(target_items) == 1:
            matches.append((selected_engine_id, selected_engine, target_items[0]))
    else:
        wanted_model = normalize_model_id(root, selection or default_model)
        for configured_engine_id, configured_engine in engines.items():
            engine_id = str(configured_engine_id)
            for model_item in (configured_engine or {}).get("models") or []:
                if normalize_model_id(root, model_item.get("id", "")) == wanted_model:
                    matches.append((engine_id, configured_engine, model_item))
    if len(matches) != 1:
        selection_label = arguments.selection or default_model
        if len(matches) > 1:
            raise SystemExit(f"模型对应多个配方，请使用 --target 指定目标引擎: {selection_label}")
        if target:
            raise SystemExit(f"目标引擎 {target} 未登记模型: {selection_label}")
        raise SystemExit(f"模型未在 config.yaml 中唯一登记: {selection_label}")

    engine_id, engine, model_item = matches[0]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", engine_id):
        raise SystemExit(f"引擎 ID 不能用于 runtime_assets 子目录: {engine_id}")
    runtime_assets = f"{runtime_assets_root}/{engine_id}"
    remote_head = engine_enabled(engine.get("remote_head", False))
    api_host, api_port = parse_api_endpoint(
        resolve_engine_or_common(engine, common, "api_url"),
    )
    physical_order = physical_ring_order(config)
    head_name = str(engine.get("head_node", physical_order[0]))
    nnodes = int(engine.get("nnodes", 1))
    topology = load_ring_topology(Path(arguments.config)) if common.get("spark_nodes") else {}
    runtime_rank_order = configured_runtime_rank_order(
        engine, engine_id, topology, nnodes, head_name
    )
    configured_worker_names = engine.get("worker_nodes")
    if runtime_rank_order is not None:
        worker_name_override = runtime_rank_order[1:]
        if isinstance(configured_worker_names, list) and configured_worker_names:
            if [str(name) for name in configured_worker_names] != worker_name_override:
                raise SystemExit(
                    f"引擎 {engine_id} 的 worker_nodes 与 runtime_rank_order 不一致"
                )
    else:
        worker_name_override = (
            [str(name) for name in configured_worker_names]
            if isinstance(configured_worker_names, list) and configured_worker_names
            else None
        )
    head, ordered_workers = resolve_cluster_nodes(
        config,
        head_name_override=head_name,
        worker_names_override=worker_name_override,
    )
    workers = resolve_engine_workers(config, engine, ordered_workers)
    worker = workers[0]
    worker_names = worker_name_override or [name for name in physical_order if name != head_name]
    worker_names = worker_names[: len(workers)]
    if runtime_rank_order is not None:
        runtime_rank_by_node = {
            node_name: rank for rank, node_name in enumerate(runtime_rank_order)
        }
    elif nnodes < 4:
        selected_runtime_order = [head_name, *worker_names[: len(workers)]]
        runtime_rank_by_node = {
            node_name: rank for rank, node_name in enumerate(selected_runtime_order)
        }
    else:
        runtime_rank_by_node = {
            node_name: int(record["rank"]) for node_name, record in topology.items()
        }
    direct_head_fabric_ip, direct_worker_fabric_ip = resolve_direct_fabric_pair(
        topology, head_name, str(worker_names[0])
    ) if nnodes == 2 and worker_names else ("", "")
    model_id = normalize_model_id(root, model_item.get("id", ""))
    draft_model_id = normalize_model_id(root, engine.get("draft_model", ""))
    all_nodes = common.get("spark_nodes") if isinstance(common.get("spark_nodes"), dict) else {}
    ordered_all_worker_names = [
        str(name)
        for name in physical_order
        if str(name) != head_name
    ]
    ordered_all_worker_names.extend(
        str(name)
        for name in all_nodes
        if str(name) != head_name and str(name) not in ordered_all_worker_names
    )
    head_ring = topology.get(head_name) or {}
    worker_ring = topology.get(worker_names[0]) or {}
    direct_port = next(
        (port for port in (0, 1) if head_ring.get(f"neighbor{port}") == worker_names[0]),
        None,
    )
    worker_direct_port = next(
        (port for port in (0, 1) if worker_ring.get(f"neighbor{port}") == head_name),
        None,
    )
    head_ring_ifname = (
        str(head_ring.get(f"fabric_if{direct_port}", "")) if direct_port is not None else ""
    )
    worker_ring_ifname = (
        str(worker_ring.get(f"fabric_if{worker_direct_port}", ""))
        if worker_direct_port is not None
        else ""
    )
    head_direct_hca = (
        ring_hca_for_port(head_ring, int(direct_port)).split(",", 1)[0]
        if direct_port is not None and head_ring
        else str(head.get("hca", ""))
    )
    worker_direct_hca = (
        ring_hca_for_port(worker_ring, int(worker_direct_port)).split(",", 1)[0]
        if worker_direct_port is not None and worker_ring
        else str(worker.get("hca", ""))
    )
    if nnodes == 2:
        if direct_port is None or worker_direct_port is None:
            raise SystemExit(
                f"引擎 {engine_id} 的 TP2 节点必须使用 common.ring 物理直连边: "
                f"{head_name} -> {worker_names[0]}"
            )
        head_peer_hca = direct_peer_hca(head_ring, worker_names[0], 1)
        worker_peer_hca = direct_peer_hca(worker_ring, head_name, 0)
    else:
        head_peer_hca = (
            ring_peer_hca(topology[head_name], topology, runtime_rank_by_node)
            if topology
            else ""
        )
        worker_peer_hca = (
            " ".join(
                ring_peer_hca(topology[node_name], topology, runtime_rank_by_node)
                for node_name in worker_names[: len(workers)]
            )
            if topology
            else ""
        )
    peer_hca_by_rank: dict[str, str] = {}
    if nnodes >= 4 and topology:
        peer_hca_by_rank[str(runtime_rank_by_node[head_name])] = head_peer_hca
        for node_name, peer_map in zip(worker_names[: len(workers)], worker_peer_hca.split()):
            peer_hca_by_rank[str(runtime_rank_by_node[node_name])] = peer_map
        if set(peer_hca_by_rank) != {"0", "1", "2", "3"} or any(not value for value in peer_hca_by_rank.values()):
            raise SystemExit(f"引擎 {engine_id} 未能从 common.ring 派生完整 TP4 peer-HCA 映射")

    engine_extra_container_env = str(engine.get("extra_container_env", ""))
    b12x_runtime_env: dict[str, str] = {}
    if engine_enabled(engine.get("b12x_roce_ring", False)):
        if len(topology) != 4:
            raise SystemExit(f"引擎 {engine_id} 声明 b12x_roce_ring，但 common.ring 不是四节点拓扑")
        ring = common.get("ring") or {}
        b12x_hcas = topology[physical_order[0]]["hca_all"]
        b12x_runtime_env = {
            "B12X_ROCE_HCA": b12x_hcas,
            "B12X_ROCE_GID_INDEX": str(ring["roce_gid_index"]),
            "B12X_NEXT_ROCE_GID_INDEX": str(ring["roce_gid_index"]),
            "B12X_ROCE_PEER_HCA_MAPS": ring_b12x_peer_hca_maps(topology, runtime_rank_by_node),
        }
        engine_extra_container_env = append_managed_env(
            engine_extra_container_env,
            b12x_runtime_env,
            f"engines.{engine_id}.extra_container_env",
        )
    engine_extra_docker_env = str(engine.get("extra_docker_env", ""))
    if topology:
        topology_managed_nccl_keys = {
            "NCCL_IB_GID_INDEX",
            "NCCL_IB_GID_INDEX_FORCE",
            "NCCL_IB_SUBNET_PREFIX_LEN",
            "NCCL_IB_HCA",
            "PEER_HCA_RANK0",
            "PEER_HCA_RANK1",
            "PEER_HCA_RANK2",
            "PEER_HCA_RANK3",
            "B12X_ROCE_HCA",
            "B12X_ROCE_GID_INDEX",
            "B12X_NEXT_ROCE_GID_INDEX",
            "B12X_ROCE_PEER_HCA_MAPS",
            "NCCL_RING_DEV_MAP",
        }
        duplicated_nccl_keys = sorted({
            token.split("=", 1)[0]
            for token in engine_extra_docker_env.split()
            if token.split("=", 1)[0] in topology_managed_nccl_keys
        })
        if duplicated_nccl_keys:
            raise SystemExit(
                f"engines.{engine_id}.extra_docker_env 含由 common.ring/target 派生的重复参数: "
                f"{duplicated_nccl_keys}"
            )
    nccl_transport_env = engine.get("nccl_transport_env") or {}
    if not isinstance(nccl_transport_env, dict):
        raise SystemExit(f"engines.{engine_id}.nccl_transport_env 必须是 NCCL key/value mapping")
    nccl_ib_subnet_prefix_len = str(engine.get("nccl_ib_subnet_prefix_len", ""))
    derives_subnet_prefix = engine_enabled(engine.get("nccl_ib_subnet_prefix_len_from_ring", False))
    subnet_aware_target_field = engine_enabled(engine.get("nccl_ib_subnet_aware_routing", False))
    subnet_aware_transport_value = str(nccl_transport_env.get("NCCL_IB_SUBNET_AWARE_ROUTING", ""))
    if subnet_aware_target_field and subnet_aware_transport_value:
        raise SystemExit(
            f"engines.{engine_id} 在 nccl_ib_subnet_aware_routing 与 nccl_transport_env 重复配置 NCCL_IB_SUBNET_AWARE_ROUTING"
        )
    uses_subnet_aware_routing = subnet_aware_target_field or engine_enabled(subnet_aware_transport_value)
    if derives_subnet_prefix or uses_subnet_aware_routing:
        if not topology:
            raise SystemExit(f"引擎 {engine_id} 要求从 common.ring 派生 NCCL subnet prefix")
        nccl_ib_subnet_prefix_len = ring_subnet_prefix_len(topology)
    ring_roce_gid_index = str((topology.get(physical_order[0]) or {}).get("roce_gid_index", ""))
    ring_subnet_prefix = ring_subnet_prefix_len(topology) if topology else ""
    ring_primary_address_range = ring_primary_addr_range(topology) if topology else ""
    if subnet_aware_target_field:
        engine_extra_docker_env = append_managed_env(
            engine_extra_docker_env,
            {"NCCL_IB_SUBNET_AWARE_ROUTING": "1"},
            f"engines.{engine_id}.extra_docker_env",
        )
    runtime_head_rank = runtime_rank_by_node.get(head_name, 0)
    runtime_worker_ranks = " ".join(
        str(runtime_rank_by_node.get(node_name, index + 1))
        for index, node_name in enumerate(worker_names[: len(workers)])
    )
    head_hca_all = str(head_ring.get("hca_all", head.get("hca_all", head.get("hca", ""))))
    worker_hca_all = " ".join(
        str((topology.get(worker_name) or {}).get("hca_all", ""))
        or str(node.get("hca_all", node.get("hca", "")))
        for worker_name, node in zip(worker_names[: len(workers)], workers)
    )
    if topology and str(engine.get("nccl_ib_hca", "")).strip():
        raise SystemExit(
            f"引擎 {engine_id} 不得重复配置 nccl_ib_hca；HCA 清单必须来自 common.ring"
        )
    invalid_nccl_env = {
        key: value
        for key, value in nccl_transport_env.items()
        if not re.fullmatch(r"NCCL_[A-Z0-9_]+", str(key))
        or not str(value)
        or any(character.isspace() or character == "=" for character in str(value))
    }
    if invalid_nccl_env:
        raise SystemExit(f"engines.{engine_id}.nccl_transport_env 含无效参数: {invalid_nccl_env}")
    duplicated_nccl_env_keys = sorted(set(nccl_transport_env) & {
        "NCCL_IB_GID_INDEX",
        "NCCL_IB_GID_INDEX_FORCE",
        "NCCL_IB_SUBNET_PREFIX_LEN",
        "NCCL_IB_HCA",
        "PEER_HCA_RANK0",
        "PEER_HCA_RANK1",
        "PEER_HCA_RANK2",
        "PEER_HCA_RANK3",
        "B12X_ROCE_HCA",
        "B12X_ROCE_GID_INDEX",
        "B12X_NEXT_ROCE_GID_INDEX",
        "B12X_ROCE_PEER_HCA_MAPS",
        "NCCL_RING_DEV_MAP",
        "NCCL_HOST_DIR",
        "NCCL_CONTAINER_DIR",
        "NCCL_SO_NAME",
        "NCCL_PIN_HOST",
        "NCCL_PIN_CONTAINER",
        "NCCL_PIP_SO",
        "NCCL_SHA256",
        "NCCL_LIBRARY_SHA256",
        "SWITCHLESS_NCCL_SHA256",
    })
    if duplicated_nccl_env_keys:
        raise SystemExit(
            f"engines.{engine_id}.nccl_transport_env 不得保存拓扑/target 派生值或 NCCL 资产路径: "
            f"{duplicated_nccl_env_keys}"
        )
    extra_docker_nccl_keys = nccl_env_keys(
        engine_extra_docker_env, f"engines.{engine_id}.extra_docker_env"
    )
    extra_container_nccl_keys = nccl_env_keys(
        engine_extra_container_env, f"engines.{engine_id}.extra_container_env"
    )
    transport_nccl_keys = {str(key) for key in nccl_transport_env}
    configured_nccl_sources = (
        extra_docker_nccl_keys | extra_container_nccl_keys | transport_nccl_keys
    )
    duplicate_sources = sorted(
        (extra_docker_nccl_keys & extra_container_nccl_keys)
        | (extra_docker_nccl_keys & transport_nccl_keys)
        | (extra_container_nccl_keys & transport_nccl_keys)
    )
    direct_nccl_fields = {
        "nccl_net": "NCCL_NET",
        "nccl_ib_disable": "NCCL_IB_DISABLE",
        "nccl_p2p_disable": "NCCL_P2P_DISABLE",
        "nccl_shm_disable": "NCCL_SHM_DISABLE",
        "nccl_host_dir": "NCCL_HOST_DIR",
        "nccl_container_dir": "NCCL_CONTAINER_DIR",
        "nccl_so_name": "NCCL_SO_NAME",
        "nccl_pin_host": "NCCL_PIN_HOST",
        "nccl_pin_container": "NCCL_PIN_CONTAINER",
        "nccl_ib_subnet_prefix_len": "NCCL_IB_SUBNET_PREFIX_LEN",
        "nccl_ib_gid_index_force": "NCCL_IB_GID_INDEX_FORCE",
        "nccl_switchless_ring_only": "NCCL_SWITCHLESS_RING_ONLY",
        "nccl_skip_tree_connect": "NCCL_SKIP_TREE_CONNECT",
        "nccl_extended_ipv4_gids": "NCCL_IB_EXTENDED_IPV4_GIDS",
        "nccl_ib_tc": "NCCL_IB_TC",
        "nccl_pip_so": "NCCL_PIP_SO",
    }
    duplicate_sources.extend(
        env_key
        for field, env_key in direct_nccl_fields.items()
        if str(engine.get(field, "")) and env_key in configured_nccl_sources
    )
    duplicate_sources = sorted(set(duplicate_sources))
    if duplicate_sources:
        raise SystemExit(
            f"engines.{engine_id} 在多个 target NCCL 配置源重复定义: {duplicate_sources}"
        )
    nccl_transport_env_string = " ".join(
        f"{key}={value}" for key, value in nccl_transport_env.items()
    )

    def configured_nccl_value(field: str, env_key: str) -> str:
        direct = str(engine.get(field, ""))
        mapped = str(nccl_transport_env.get(env_key, ""))
        if direct and mapped:
            raise SystemExit(f"engines.{engine_id} 重复配置 NCCL 值 {env_key}")
        return direct or mapped

    start_script, start_args = parse_command(engine.get("start"), engine_id, "start")
    stop_script, _stop_args = parse_command(engine.get("stop"), engine_id, "stop")
    values = {
        "model_id": model_id,
        "model_path": f"{root}/{model_id}" if root else model_id,
        "runtime_assets_root": runtime_assets_root,
        "runtime_assets": runtime_assets,
        "state_dir": f"{runtime_assets}/state",
        "logs_dir": f"{runtime_assets}/logs",
        "results_dir": f"{runtime_assets}/results",
        "cache_dir": f"{runtime_assets}/cache",
        "engram_dir": f"{runtime_assets}/engram",
        "role": str(model_item.get("role", "")),
        "reason": str(model_item.get("reason", "")),
        "engine_id": engine_id,
        "container_prefix": engine_id,
        "engine_dir": str(engine.get("engine_dir", engine_id)),
        "env_dir": str(engine.get("env_dir", engine_id)),
        "remote_head": str(remote_head).lower(),
        "image": str(engine.get("image", "")),
        "base_image": str(engine.get("base_image", "")),
        "build_dockerfile": str(engine.get("build_dockerfile", "")),
        "build_args": str(engine.get("build_args", "")),
        "load_format": str(engine.get("load_format", "")),
        "skip_pull": str(engine.get("skip_pull", "")),
        "skip_build": str(engine.get("skip_build", "")),
        "ship_image_ring": str(engine_enabled(engine.get("ship_image_ring", False))).lower(),
        "b12x_ring_dir": str(engine.get("b12x_ring_dir", "")),
        "ring_nccl_dir": str(engine.get("ring_nccl_dir", "")),
        "ring_nccl_sha256": str(engine.get("ring_nccl_sha256", "")),
        "nccl_pin_sha256": str(engine.get("nccl_pin_sha256", "")),
        # HCA enumeration belongs to common.ring; don't duplicate its physical-device list per target.
        "nccl_ib_hca": head_hca_all if topology else str(engine.get("nccl_ib_hca") or head_hca_all),
        "nccl_ib_tc": configured_nccl_value("nccl_ib_tc", "NCCL_IB_TC"),
        "nccl_pip_so": str(engine.get("nccl_pip_so", "")),
        "nccl_switchless_ring_only": configured_nccl_value("nccl_switchless_ring_only", "NCCL_SWITCHLESS_RING_ONLY"),
        "nccl_skip_tree_connect": configured_nccl_value("nccl_skip_tree_connect", "NCCL_SKIP_TREE_CONNECT"),
        "nccl_ib_subnet_prefix_len": nccl_ib_subnet_prefix_len,
        "ring_roce_gid_index": ring_roce_gid_index,
        "ring_subnet_prefix_len": ring_subnet_prefix,
        "ring_primary_addr_range": ring_primary_address_range,
        # The parent launcher validates these derived B12X values before it
        # forwards the same values through EXTRA_CONTAINER_ENV to each rank.
        "b12x_roce_hca": b12x_runtime_env.get("B12X_ROCE_HCA", ""),
        "b12x_roce_gid_index": b12x_runtime_env.get("B12X_ROCE_GID_INDEX", ""),
        "b12x_next_roce_gid_index": b12x_runtime_env.get("B12X_NEXT_ROCE_GID_INDEX", ""),
        "b12x_roce_peer_hca_maps": b12x_runtime_env.get("B12X_ROCE_PEER_HCA_MAPS", ""),
        "nccl_ring_dev_map_from_ring": str(engine.get("nccl_ring_dev_map_from_ring", "")),
        "nccl_ib_gid_index_force": str(engine.get("nccl_ib_gid_index_force", "")),
        "nccl_net": configured_nccl_value("nccl_net", "NCCL_NET"),
        "nccl_ib_disable": configured_nccl_value("nccl_ib_disable", "NCCL_IB_DISABLE"),
        "nccl_p2p_disable": configured_nccl_value("nccl_p2p_disable", "NCCL_P2P_DISABLE"),
        "nccl_shm_disable": configured_nccl_value("nccl_shm_disable", "NCCL_SHM_DISABLE"),
        "nccl_host_dir": configured_nccl_value("nccl_host_dir", "NCCL_HOST_DIR"),
        "nccl_container_dir": configured_nccl_value("nccl_container_dir", "NCCL_CONTAINER_DIR"),
        "nccl_so_name": configured_nccl_value("nccl_so_name", "NCCL_SO_NAME"),
        "nccl_pin_host": configured_nccl_value("nccl_pin_host", "NCCL_PIN_HOST"),
        "nccl_pin_container": configured_nccl_value("nccl_pin_container", "NCCL_PIN_CONTAINER"),
        "nccl_transport_env": nccl_transport_env_string,
        "tp_mode": str(engine.get("tp_mode", "")),
        "transport": str(engine.get("transport", "")),
        "torch_use_rtld_global": str(engine.get("torch_use_rtld_global", "")),
        "use_host_nccl": str(engine.get("use_host_nccl", "")),
        "fabric_preflight": str(engine.get("fabric_preflight", "")),
        "fabric_check": str(engine.get("fabric_check", "")),
        "fabric_ring_graph": str(engine.get("fabric_ring_graph", "")),
        "nccl_extended_ipv4_gids": configured_nccl_value("nccl_extended_ipv4_gids", "NCCL_IB_EXTENDED_IPV4_GIDS"),
        "overlay_remote": str(engine.get("overlay_remote", "")),
        "draft_model_id": draft_model_id,
        "hf_endpoint": str(engine.get("hf_endpoint", "")),
        "dflash_revision": str(engine.get("dflash_revision", "")),
        "start_script": start_script,
        "stop_script": stop_script,
        "stop_command": str(engine.get("stop", "")),
        "env_file": str(engine.get("env_file", "")),
        "env_template": str(engine.get("env_template", "")),
        "head_fabric_ip": direct_head_fabric_ip
        or str((topology.get(head_name) or {}).get("local_ip0", "")).split("/", 1)[0],
        "head_ifname": head_ring_ifname or str(head.get("ifname", "")),
        "head_management_if": str(head.get("management_if", "")),
        "head_hca": head_direct_hca,
        "worker_ssh": str(worker.get("ssh", "")),
        "worker_management_ip": str(worker.get("management_ip", "")),
        "worker_fabric_ip": direct_worker_fabric_ip
        or str((topology.get(worker_names[0]) or {}).get("local_ip0", "")).split("/", 1)[0],
        "head_management_ip": str(head.get("management_ip", "")),
        "worker_management_ips": " ".join(str(node.get("management_ip", "")) for node in workers),
        "all_worker_management_ips": " ".join(
            str(all_nodes.get(node_name, {}).get("management_ip", ""))
            for node_name in ordered_all_worker_names
        ),
        "worker_fabric_ips": (
            direct_worker_fabric_ip
            if nnodes == 2
            else " ".join(
                str((topology.get(worker_name) or {}).get("local_ip0", "")).split("/", 1)[0]
                for worker_name in worker_names[: len(workers)]
            )
        ),
        "head_node": head_name,
        "runtime_rank_order": " ".join([head_name, *worker_names[: len(workers)]]),
        "control_head_node": physical_order[0],
        "worker_nodes": " ".join(worker_names),
        "head_hca_all": head_hca_all,
        "worker_hca_all": worker_hca_all,
        "worker_hca_ranks": " ".join(
            str((topology.get(worker_name) or {}).get("hca_all", ""))
            or str(node.get("hca_all", node.get("hca", "")))
            for worker_name, node in zip(worker_names[: len(workers)], workers)
        ),
        "head_fabric_ifs": ",".join(topology[head_name][key] for key in ("fabric_if0", "fabric_if1"))
        if topology
        else str(head.get("ifname", "")),
        "worker_fabric_ifs": " ".join(
            ",".join(topology[node_name][key] for key in ("fabric_if0", "fabric_if1"))
            for node_name in worker_names[: len(workers)]
        )
        if topology
        else " ".join(str(node.get("ifname", "")) for node in workers),
        "head_rank": str(runtime_head_rank),
        "worker_ranks": runtime_worker_ranks,
        "head_peer_hca": head_peer_hca,
        "worker_peer_hca": worker_peer_hca,
        "worker_ifname": worker_ring_ifname or str(worker.get("ifname", "")),
        "worker_management_if": str(worker.get("management_if", "")),
        "worker_hca": worker_direct_hca,
        "user": str(common.get("user", "")),
        "master_port": str(resolve_engine_or_common(engine, common, "master_port", 25000)),
        "tensor_parallel_size": str(engine.get("tensor_parallel_size", 1)),
        "nnodes": str(engine.get("nnodes", 1)),
        "container_names": " ".join(target_container_names(engine_id, int(engine.get("nnodes", 1)))),
        "api_host": api_host,
        "port": str(api_port),
        "max_request": str(resolve_engine_or_common(engine, common, "max_request", "")),
        "max_token": str(resolve_engine_or_common(engine, common, "max_token", "")),
        "max_num_batched_tokens": str(common.get("max_num_batched_tokens", "")),
        "mtp_num_tokens": str(common.get("mtp_num_tokens", "")),
        "gpu_mem_util": str(engine.get("gpu_mem_util", "")),
        "engine_max_num_seqs": str(engine.get("max_num_seqs", "")),
        "engine_max_num_batched_tokens": str(engine.get("max_num_batched_tokens", "")),
        "kv_cache_dtype": str(engine.get("kv_cache_dtype", "")),
        "dflash_tokens": str(engine.get("dflash_tokens", "")),
        "dflash_draft_tp": str(engine.get("dflash_draft_tp", "")),
        "dflash_draft_sample_method": str(engine.get("dflash_draft_sample_method", "")),
        "dflash_rejection_sample_method": str(engine.get("dflash_rejection_sample_method", "")),
        "coop_profile": str(engine.get("coop_profile", "")),
        "coop_qualification": str(engine.get("coop_qualification", "")),
        "coop_maintenance_test": str(engine.get("coop_maintenance_test", "")),
        "enforce_eager": str(engine.get("enforce_eager", "")),
        "cg_estimate": str(engine.get("cg_estimate", "")),
        "sparse_mla_slice_tokens": str(engine.get("sparse_mla_slice_tokens", "")),
        "disable_pynccl": str(engine.get("disable_pynccl", "")),
        "mixed_prefill_chunk": str(engine.get("mixed_prefill_chunk", "")),
        "fair_prefill_chunk": str(engine.get("fair_prefill_chunk", "")),
        "fair_prefill_share": str(engine.get("fair_prefill_share", "")),
        "fair_prefill_max_interval_ms": str(engine.get("fair_prefill_max_interval_ms", "")),
        "fair_prefill_max_step_ms": str(engine.get("fair_prefill_max_step_ms", "")),
        "fair_prefill_max_chunks": str(engine.get("fair_prefill_max_chunks", "")),
        "apc_retention_interval_swa": str(engine.get("apc_retention_interval_swa", "")),
        "load_clone": str(engine.get("load_clone", "")),
        "load_prefetch": str(engine.get("load_prefetch", "")),
        "draft_kv_compact": str(engine.get("draft_kv_compact", "")),
        "indexer_workspace": str(engine.get("indexer_workspace", "")),
        "spinwait_ms": str(engine.get("spinwait_ms", "")),
        "host_mem_hygiene": str(engine.get("host_mem_hygiene", "")),
        "apc_no_store": str(engine.get("apc_no_store", "")),
        "kv_capacity_log": str(engine.get("kv_capacity_log", "")),
        "boot_shape_warmup": str(engine.get("boot_shape_warmup", "")),
        "expose_cache_reset": str(engine.get("expose_cache_reset", "")),
        "exl3_moe_fast": str(engine.get("exl3_moe_fast", "")),
        "kda_bf16_large_m": str(engine.get("kda_bf16_large_m", "")),
        "default_thinking": str(common.get("default_thinking", "")).lower(),
        "api_key": str(common.get("api_key", "")),
        "enabled": str(engine_enabled(engine.get("enabled", True))).lower(),
        "unavailable_reason": str(engine.get("unavailable_reason", "")),
        "served_model_name": str(engine.get("served_model_name", model_id)),
        "engine_extra_docker_env": engine_extra_docker_env,
        "engine_extra_container_env": engine_extra_container_env,
        "peer_hca_rank0": peer_hca_by_rank.get("0", ""),
        "peer_hca_rank1": peer_hca_by_rank.get("1", ""),
        "peer_hca_rank2": peer_hca_by_rank.get("2", ""),
        "peer_hca_rank3": peer_hca_by_rank.get("3", ""),
        "prefill_fp8": str(engine.get("prefill_fp8", "")),
        "mem_fraction_static": str(engine.get("mem_fraction_static", "")),
        "head_mem_fraction_static": str(engine.get("head_mem_fraction_static", "")),
        "max_running_requests": str(engine.get("max_running_requests", "")),
        "chunked_prefill_size": str(engine.get("chunked_prefill_size", "")),
        "max_total_tokens": str(engine.get("max_total_tokens", "")),
        "cuda_graph_max_bs_decode": str(engine.get("cuda_graph_max_bs_decode", "")),
        "ep_size": str(engine.get("ep_size", "")),
        "dsv41_cache_gib": str(engine.get("dsv41_cache_gib", "")),
        "dsv41_engram_cache_gib": str(engine.get("dsv41_engram_cache_gib", "")),
        "dsv41_cache_ways": str(engine.get("dsv41_cache_ways", "")),
        "dsv41_io_threads": str(engine.get("dsv41_io_threads", "")),
        "dsv41_prefill_empty_cache_tokens": str(engine.get("dsv41_prefill_empty_cache_tokens", "")),
        "dsv41_indexer_budget_mib": str(engine.get("dsv41_indexer_budget_mib", "")),
        "dsv41_serial_weight_load": str(engine.get("dsv41_serial_weight_load", "")),
        "offload_mode": str(engine.get("offload_mode", "")),
        "dsv41_resident_scales": str(engine.get("dsv41_resident_scales", "")),
        "dsv41_stats_seconds": str(engine.get("dsv41_stats_seconds", "")),
        "dsv41_tp_pad": str(engine.get("dsv41_tp_pad", "")),
        "dsv41_mxfp8_backend": str(engine.get("dsv41_mxfp8_backend", "")),
        "sglang_flashinfer_moe_fused_finalize": str(engine.get("sglang_flashinfer_moe_fused_finalize", "")),
        "pytorch_cuda_alloc_conf": str(engine.get("pytorch_cuda_alloc_conf", "")),
        "spec_algo": str(engine.get("spec_algo", "")),
        "spec_mode": str(engine_enabled(engine.get("spec_mode", False))).lower(),
        "dspark_sps_table": str(engine.get("dspark_sps_table", "")),
        "dspark_sts_table": str(engine.get("dspark_sts_table", "")),
        "dsv41_packed_dir": str(engine.get("dsv41_packed_dir", "")),
        "reasoning_effort": str(common.get("reasoning_effort", "")),
        "extra_sglang_args": str(engine.get("extra_sglang_args", "")),
        "extra_args": str(engine.get("extra_args", "")),
        "adaptive_k": str(engine.get("adaptive_k", "")),
        "adaptive_k_set": str(engine.get("adaptive_k_set", "")),
        "dense_fp8": str(engine.get("dense_fp8", "")),
        "coop_geometry": str(engine.get("coop_geometry", "")),
        "coop_overlay_host": str(engine.get("coop_overlay_host", "")),
        "fused_moe": str(engine.get("fused_moe", "")),
        "fat_kernel": str(engine.get("fat_kernel", "")),
        "fat_grouped": str(engine.get("fat_grouped", "")),
        "temp_rows_fused": str(engine.get("temp_rows_fused", "")),
        "image": str(engine.get("image", "")),
        "base_image": str(engine.get("base_image", "")),
        "container_name": str(engine.get("container_name", "")),
        "model_container_path": str(engine.get("model_container_path", "")),
        "dflash2_container_path": str(engine.get("dflash2_container_path", "")),
        "drafter": str(engine.get("drafter", "")),
        "parallel": str(engine.get("parallel", "")),
        "kv": str(engine.get("kv", "")),
        "dense": str(engine.get("dense", "")),
        "vision": str(engine.get("vision", "")),
        "thinking": str(engine.get("thinking", "")),
        "comm": str(engine.get("comm", "")),
        "roce_max_kb": str(engine.get("roce_max_kb", "")),
        "max_tokens": str(resolve_engine_or_common(engine, common, "max_tokens", "")),
        "pull": str(engine.get("pull", "")),
        "start_args": start_args,
    }
    validate_output_values(list(values.values()), "配置值")
    for key, value in values.items():
        print(f"{key}\t{value}")
    return 0


def _parse_env_assignments(raw_env: str, context: str) -> dict[str, str]:
    """Parse the one-line KEY=value container environment without shell execution."""
    assignments: dict[str, str] = {}
    for token in raw_env.split():
        key, separator, value = token.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise SystemExit(f"{context} 含非法环境项: {token}")
        if key in assignments:
            raise SystemExit(f"{context} 重复定义环境项: {key}")
        assignments[key] = value
    return assignments


def _merge_env_assignments(
    target: dict[str, str], additions: dict[str, str], context: str
) -> None:
    duplicates = sorted(set(target) & set(additions))
    if duplicates:
        raise SystemExit(f"{context} 存在多个配置源重复定义: {duplicates}")
    target.update(additions)


def _managed_model_values(
    config_path: Path, target: str, model_id: str
) -> tuple[dict[str, str], dict[str, Any]]:
    """Reuse query-model as the single route/rank derivation path."""
    captured = io.StringIO()
    arguments = argparse.Namespace(
        config=str(config_path),
        target=target,
        selection=model_id,
    )
    with contextlib.redirect_stdout(captured):
        command_query_model(arguments)
    values: dict[str, str] = {}
    for line in captured.getvalue().splitlines():
        key, separator, value = line.partition("\t")
        if not separator:
            raise SystemExit(f"query-model 输出缺少制表符: {line}")
        values[key] = value

    config = load_config(config_path)
    engines = config.get("engines") or {}
    engine = engines.get(target)
    if not isinstance(engine, dict):
        raise SystemExit(f"目标引擎未在 config.yaml 中登记: {target}")
    return values, engine


def _deepseek_flash_extra_args(engine: dict[str, Any]) -> list[str]:
    raw_args = engine.get("extra_sglang_args")
    if raw_args is None:
        return []
    if isinstance(raw_args, str):
        try:
            return shlex.split(raw_args)
        except ValueError as exc:
            raise SystemExit(f"deepseek_flash extra_sglang_args 无法解析: {exc}") from exc
    if not isinstance(raw_args, list) or any(not isinstance(item, str) for item in raw_args):
        raise SystemExit("deepseek_flash extra_sglang_args 必须是字符串列表")
    if any(not item for item in raw_args):
        raise SystemExit("deepseek_flash extra_sglang_args 不能包含空参数")
    return list(raw_args)


_DEEPSEEK_FLASH_WF_RING_ENV_KEYS = {
    "DSV41_HC_FUSED",
    "DSV41_INDEXER_HOIST",
    "DSV41_PREFILL_EMPTY_CACHE_TOKENS",
    "DSV41_IDLE_RELEASE",
    "DSV41_ENGRAM_CACHE_GIB",
    "DSV41_WO_A_W8",
    "DSV41_WO_A_W8_MID",
    "DSV41_WO_A_W8_DROP",
    "DSV41_REPLICATED_SPLIT",
    "SGLANG_FLASHINFER_MOE_FUSED_FINALIZE",
    "DSV41_SPEC_SYNC_FREE",
    "DSV41_EAGER_GLUE",
    "DSV41_FOLDED_FENCE",
    "DSV41_SKIP_NONFINAL_DECODER",
    "DSV41_DRAFT_HEAD_FP8",
    "DSV41_DRAFT_HEAD_FP8_IMPL",
    "DSV41_DRAFT_MAIN_PROJ_SPLIT",
    "DSV41_VERIFY_CAP",
    "DSV41_SHARED_PAD_K",
    "DSV41_ROUTER_LIVE",
    "DSV41_MOE_B12X_NEXT_DETERMINISTIC",
    "TP_SIZE",
    "DSV41_PREFILL_SP",
    "SGLANG_MAX_NEW_TOKENS_LIMIT",
    "DSV41_SPARSE_LOCAL_CONSUME",
    "DSV41_SPARSE_LOCAL_CONSUME_TIER",
    "DSV41_SPARSE_LOCAL_ADMISSION",
    "DSV41_SPARSE_LOCAL_ADMISSION_CTX_CAP",
    "DSV41_SPARSE_LOCAL_ADMISSION_MRR",
    "DSV41_CERT_HEAD",
    "DSV41_CERT_HEAD_M",
    "DSV41_CERT_HEAD_LOG_EVERY",
    "DSV41_CERT_HEAD_DEBUG",
    "SGLANG_ENABLE_HEALTH_ENDPOINT_GENERATION",
    "NCCL_BUFFSIZE",
    "NCCL_LL128_BUFFSIZE",
    "NCCL_PROTO",
    "DSV41_L2_PREFETCH",
    "DSV41_L2_PREFETCH_MB",
    "SGLANG_DSV4_O12_SCHED_ASYNC",
    "SGLANG_DSV4_O12_RECV_INTERVAL",
    "DSV41_FP4KV_R1_INKERNEL",
    "DSV41_PREFILL_SP_FP8",
    "DSV41_PREFILL_SP_TAIL_GATHER",
    "DSV41_FL6_RECALL_FIX",
    "DSV41_ROCE_RING",
    "SGLANG_ROCE_ALLREDUCE",
    "B12X_ROCE_TWO_WAVE_THRESHOLD_BYTES",
    "SGLANG_ROCE_MAX_SIZE",
    "DSV41_ROCE_GATHER",
    "DSV41_SPLIT_COMPACT_GATHER",
    "DSV41_REPLICATED_SPLIT_WINDOW",
    "SGLANG_DSV41_EFFORT_DEFAULT",
    "SGLANG_DSV41_RF_INJECT",
    "SGLANG_DSV4_KV_LAYOUT",
    "DSV41_PREFILL_BYPASS",
    "SGLANG_ENABLE_DSV41_ENGRAM_HOST_TABLE",
}

_DEEPSEEK_FLASH_BOOT_ENV_KEYS = {
    "NCCL_NET",
    "NCCL_IB_HCA",
    "NCCL_SOCKET_IFNAME",
    "GLOO_SOCKET_IFNAME",
    "NCCL_DEBUG",
    "NCCL_DEBUG_SUBSYS",
    "HF_HUB_OFFLINE",
    "SGLANG_ENABLE_JIT_DEEPGEMM",
    "NCCL_ALGO",
    "NCCL_BUFFSIZE",
    "NCCL_CROSS_NIC",
    "NCCL_CUMEM_ENABLE",
    "NCCL_CUMEM_HOST_ENABLE",
    "NCCL_IB_DISABLE",
    "NCCL_IB_MERGE_NICS",
    "NCCL_IB_RETRY_CNT",
    "NCCL_IB_SUBNET_AWARE_ROUTING",
    "NCCL_IB_TIMEOUT",
    "NCCL_IGNORE_CPU_AFFINITY",
    "NCCL_LL128_BUFFSIZE",
    "NCCL_MAX_NCHANNELS",
    "NCCL_MIN_NCHANNELS",
    "NCCL_NET_PLUGIN",
    "NCCL_P2P_DISABLE",
    "NCCL_PROTO",
    "NCCL_SET_THREAD_NAME",
    "NCCL_SHM_DISABLE",
    "NCCL_TUNER_THRESHOLD",
    "PYTHONPATH",
    "DSV41_MOE_B12X_NEXT",
    "DSV41_LAUNCHER",
    "OFFLOAD_MODE",
    "DSV41_SOURCE",
    "DSV41_CACHE_GIB",
    "DSV41_IO_THREADS",
    "DSV41_RESIDENT_SCALES",
    "DSV41_ENGRAM_PREFETCH",
    "DSV41_ENGRAM_PREFETCH_ROWS",
    "DSV41_ENGRAM_CHUNK_LIFETIME",
    "DSV41_ENGRAM_CACHE_GIB",
    "DSV41_CACHE_WAYS",
    "DSV41_DENSE_INDEXER_LOGITS_BUDGET_BYTES",
    "DSV41_DG_SPARSE_SM121",
    "DSV41_PACKED_DIR",
    "NNODES",
    "DSV41_STATS_SECONDS",
    "SGLANG_ENABLE_DSV41_ENGRAM_HOST_TABLE",
    "MALLOC_ARENA_MAX",
    "PYTORCH_CUDA_ALLOC_CONF",
    "TP_SIZE",
    "DSV41_FP4KV_PORT",
}


def command_query_managed_env(arguments: argparse.Namespace) -> int:
    """Emit final host/container environment for one managed target."""
    target = str(arguments.target or "").strip()
    if target == "deepseek":
        rows = _deepseek_ds41f_managed_env(
            Path(arguments.config), target, str(arguments.model or "").strip()
        )
        for key, value in rows:
            print(f"{key}\t{value}")
        return 0
    if target != "deepseek_flash":
        raise SystemExit(
            f"query-managed-env 暂只支持已审阅 target deepseek_flash/deepseek，收到: {target or '<empty>'}"
        )
    return _deepseek_flash_managed_env(arguments)


def _deepseek_flash_managed_env(arguments: argparse.Namespace) -> int:
    """deepseek_flash（SGLang 0.3.4 路线）的受管环境生成，审阅面保持不变。"""
    target = "deepseek_flash"
    model_id = str(arguments.model or "deepseek-ai/DeepSeek-V4.1-Flash-1017").strip()
    config_path = Path(arguments.config)
    values, engine = _managed_model_values(config_path, target, model_id)
    if values.get("engine_id") != target:
        raise SystemExit(f"query-model 返回了错误 target: {values.get('engine_id')}")
    if values.get("nnodes") != "4" or values.get("tensor_parallel_size") != "4" or values.get("ep_size") != "1":
        raise SystemExit("deepseek_flash 必须保持 nnodes=4、tensor_parallel_size=4、ep_size=1")
    topology = load_ring_topology(config_path)
    expected_runtime_rank_order = " ".join(
        node for node, _record in sorted(topology.items(), key=lambda item: int(item[1]["rank"]))
    )
    if values.get("runtime_rank_order") != expected_runtime_rank_order:
        raise SystemExit(
            "deepseek_flash runtime rank 必须从 common.ring 唯一派生: "
            f"{expected_runtime_rank_order}"
        )
    if values.get("head_rank") != "0" or values.get("worker_ranks") != "1 2 3":
        raise SystemExit("deepseek_flash rank 面必须为 head=0、workers=1 2 3")

    runtime_assets_root = values["runtime_assets_root"]
    runtime_assets = values["runtime_assets"]
    expected_runtime_assets = f"{runtime_assets_root}/{target}"
    if runtime_assets != expected_runtime_assets:
        raise SystemExit(f"deepseek_flash runtime_assets 必须精确为 {expected_runtime_assets}")
    expected_paths = {
        "CACHE_DIR": f"{runtime_assets}/cache",
        "RESULTS_DIR": f"{runtime_assets}/results",
        "ENGRAM_DIR": f"{runtime_assets}/engram",
    }
    for key, expected in expected_paths.items():
        actual = values["cache_dir" if key == "CACHE_DIR" else "results_dir" if key == "RESULTS_DIR" else "engram_dir"]
        if actual != expected:
            raise SystemExit(f"{key} 必须精确为 {expected}，收到 {actual}")

    image = str(engine.get("image", ""))
    image_id = str(engine.get("image_id", ""))
    if image_id != "sha256:f4cd1a986da6ad474d90ced72f19b76bf235a211f7eb293ec4a4f91f199c07b6":
        raise SystemExit(f"deepseek_flash 镜像 image ID 未登记为本站已验证导入体: {image_id or '<empty>'}")
    if image != "dsv41-sglang-optimized:0.3.4-final-20261028":
        raise SystemExit(f"deepseek_flash 镜像 tag 未登记为本站已验证导入体: {image or '<empty>'}")

    common = load_config(config_path).get("common") or {}
    user = values["user"]
    head_management_ip = values["head_management_ip"]
    worker_management_ips = values["worker_management_ips"].split()
    if not user or not head_management_ip or len(worker_management_ips) != 3:
        raise SystemExit("deepseek_flash 需要四机管理 SSH inventory")
    worker_ssh_targets = " ".join(f"{user}@{ip}" for ip in worker_management_ips)

    all_prefixes = sorted(
        str(engine_id)
        for engine_id, engine_record in (load_config(config_path).get("engines") or {}).items()
        if isinstance(engine_record, dict) and int(engine_record.get("nnodes", 1)) > 1
    )
    if target not in all_prefixes:
        raise SystemExit("deepseek_flash 未进入受管 LLM 容器前缀清单")

    container_env = _parse_env_assignments(
        values.get("engine_extra_container_env", ""),
        "engine_extra_container_env",
    )
    docker_env = _parse_env_assignments(
        values.get("engine_extra_docker_env", ""),
        "engine_extra_docker_env",
    )
    _merge_env_assignments(
        docker_env,
        _parse_env_assignments(
            values.get("nccl_transport_env", ""),
            "nccl_transport_env",
        ),
        "deepseek_flash NCCL transport 环境",
    )
    _merge_env_assignments(container_env, docker_env, "deepseek_flash NCCL 环境")
    mtp_num_tokens = values.get("mtp_num_tokens", "")
    if not mtp_num_tokens.isdigit() or int(mtp_num_tokens) < 2:
        raise SystemExit(f"common.mtp_num_tokens 必须为 >=2 的整数: {mtp_num_tokens or '<empty>'}")
    default_thinking = values.get("default_thinking", "")
    if default_thinking not in {"true", "false"}:
        raise SystemExit(f"common.default_thinking 必须为 true/false: {default_thinking or '<empty>'}")
    max_num_batched_tokens = values.get("max_num_batched_tokens", "")
    if not max_num_batched_tokens.isdigit() or int(max_num_batched_tokens) < 1:
        raise SystemExit(
            f"common.max_num_batched_tokens 必须为 >=1 的整数: {max_num_batched_tokens or '<empty>'}"
        )
    max_request = values.get("max_request", "")
    if not max_request.isdigit() or int(max_request) < 1:
        raise SystemExit(f"common.max_request 必须为 >=1 的整数: {max_request or '<empty>'}")
    max_token = values.get("max_token", "")
    if not max_token.isdigit() or int(max_token) < 1:
        raise SystemExit(f"common.max_token 必须为 >=1 的整数: {max_token or '<empty>'}")
    spec_algo = str(engine.get("spec_algo", "")).strip()
    if spec_algo != "DSPARK":
        raise SystemExit(
            f"deepseek_flash 必须显式声明已验收的投机算法 DSPARK，收到 {spec_algo or '<empty>'}"
        )

    gid_force = str(engine.get("nccl_ib_gid_index_force", "")).strip()
    if gid_force != "-1":
        raise SystemExit(
            "deepseek_flash 必须复用已验收的自动 GID 策略 "
            f"NCCL_IB_GID_INDEX_FORCE=-1，收到 {gid_force or '<empty>'}"
        )
    core_container_env = {
        "API_HOST": values["api_host"],
        "PORT": values["port"],
        "NCCL_NET": values["nccl_net"],
        "NCCL_IB_HCA": values["nccl_ib_hca"],
        "NCCL_IB_SUBNET_PREFIX_LEN": values["nccl_ib_subnet_prefix_len"],
        "NCCL_IB_DISABLE": values["nccl_ib_disable"],
        "NCCL_P2P_DISABLE": values["nccl_p2p_disable"],
        "NCCL_SHM_DISABLE": values["nccl_shm_disable"],
        "NCCL_SOCKET_IFNAME": values["head_management_if"],
        "GLOO_SOCKET_IFNAME": values["head_management_if"],
        "TP_SIZE": values["tensor_parallel_size"],
        "EP_SIZE": values["ep_size"],
        "NNODES": values["nnodes"],
        "DIST_PORT": values["master_port"],
        "DIST_INIT_ADDR": f"{head_management_ip}:{values['master_port']}",
        "CONTEXT_LENGTH": values["max_token"],
        "MAX_RUNNING_REQUESTS": max_request,
        "MAX_PREFILL_TOKENS": max_num_batched_tokens,
        "DSV41_SPARSE_LOCAL_ADMISSION_CTX_CAP": max_token,
        "DSV41_SPARSE_LOCAL_ADMISSION_MRR": max_request,
        "CHUNKED_PREFILL_SIZE": values["chunked_prefill_size"],
        "MEM_FRACTION_STATIC": values["mem_fraction_static"],
        "HEAD_MEM_FRACTION_STATIC": values["head_mem_fraction_static"],
        "MAX_TOTAL_TOKENS": values["max_total_tokens"],
        "CUDA_GRAPH_MAX_BS_DECODE": values["cuda_graph_max_bs_decode"],
        "DIST_TIMEOUT": str(engine.get("dist_timeout", "")),
        "WATCHDOG_TIMEOUT": str(engine.get("watchdog_timeout", "")),
        "SERVED_MODEL_NAME": values["served_model_name"],
        "SPEC_ALGO": spec_algo,
        "SGLANG_MAX_NEW_TOKENS_LIMIT": values["max_tokens"],
        "DSPARK_BLOCK_SIZE": str(int(mtp_num_tokens) - 1),
        "SGLANG_DEFAULT_THINKING": default_thinking,
        "SGLANG_DSV41_REASONING_EFFORT": values["reasoning_effort"],
        "DSV41_SOURCE": values["model_container_path"],
        "HOME": "/root",
        "HF_HUB_OFFLINE": "1",
        "PYTHONPATH": "/opt/dsv41/adapter:/opt/b12x-tree",
        "SGLANG_CACHE_DIR": "/root/.cache/sglang",
        "TRITON_CACHE_DIR": "/root/.cache/triton",
        "TORCHINDUCTOR_CACHE_DIR": "/root/.cache/torch",
        "B12X_COMPILE_CACHE_DIR": "/root/.cache/b12x-compile",
        "B12X_NEXT_COMPILE_CACHE_DIR": "/root/.cache/b12x-next-compile",
        "B12X_ROCE_CACHE_DIR": "/root/.cache/b12x-roce",
    }
    _merge_env_assignments(container_env, core_container_env, "deepseek_flash 受管核心环境")

    forbidden_env_keys = {
        "LUZ_SWITCH_ROCE_FLAT",
        "B12X_ROCE_TOPOLOGY",
        "B12X_ROCE_TRAFFIC_CLASS",
        "NCCL_IB_TC",
        # 0.6.18 companion wheel 与 0.7.2 Python API 不兼容；绕过检查会在
        # 导入期因缺少 get_jit_cache_providers 直接崩溃，不能作为生产修复。
        "FLASHINFER_DISABLE_VERSION_CHECK",
    }
    forbidden_present = sorted(forbidden_env_keys & set(container_env))
    if forbidden_present:
        raise SystemExit(f"deepseek_flash 含已裁定禁键: {forbidden_present}")
    if container_env.get("B12X_ROCE_TOPOLOGY") == "switched":
        raise SystemExit("deepseek_flash 不得使用 B12X_ROCE_TOPOLOGY=switched")

    extra_args = _deepseek_flash_extra_args(engine)
    if "--speculative-algorithm" in extra_args:
        raise SystemExit("deepseek_flash extra_sglang_args 不得重复声明 --speculative-algorithm")
    extra_args.extend(["--speculative-algorithm", spec_algo])
    if "--max-prefill-tokens" in extra_args:
        raise SystemExit(
            "deepseek_flash extra_sglang_args 不得重复声明 --max-prefill-tokens；"
            "该值唯一来自 common.max_num_batched_tokens"
        )
    if "--sleep-on-idle" in extra_args:
        raise SystemExit("deepseek_flash extra_sglang_args 含已退役禁参 --sleep-on-idle")
    if any("--sleep-on-idle" in value for value in container_env.values()):
        raise SystemExit("deepseek_flash 容器环境不得包含 --sleep-on-idle")

    required_container_keys = _DEEPSEEK_FLASH_WF_RING_ENV_KEYS | _DEEPSEEK_FLASH_BOOT_ENV_KEYS | {
        "DSV41_LAUNCHER",
        "B12X_ROCE_HCA",
        "B12X_ROCE_GID_INDEX",
        "B12X_NEXT_ROCE_GID_INDEX",
        "B12X_ROCE_PEER_HCA_MAPS",
        "NCCL_IB_SUBNET_AWARE_ROUTING",
        "NCCL_IB_SUBNET_PREFIX_LEN",
        "MAX_PREFILL_TOKENS",
        "DSV41_SPARSE_LOCAL_ADMISSION_CTX_CAP",
        "DSV41_SPARSE_LOCAL_ADMISSION_MRR",
        "SGLANG_RAGGED_VERIFY_MODE",
    }
    if engine_enabled(engine.get("nccl_ring_dev_map_from_ring", False)):
        if "nccl-ringonly-v5" not in values["ring_nccl_dir"]:
            raise SystemExit("NCCL_RING_DEV_MAP 必须与 v5 ring-only 共享库同时使用")
        container_env["NCCL_RING_DEV_MAP"] = ring_nccl_v5_dev_map(
            container_env["B12X_ROCE_PEER_HCA_MAPS"]
        )
        required_container_keys.add("NCCL_RING_DEV_MAP")
    missing_container_keys = sorted(required_container_keys - set(container_env))
    if missing_container_keys:
        raise SystemExit(f"deepseek_flash 缺少必需容器环境: {missing_container_keys}")
    if container_env["SGLANG_DSV4_KV_LAYOUT"] != "v41":
        raise SystemExit("deepseek_flash SGLANG_DSV4_KV_LAYOUT 必须为 v41")
    if container_env["SGLANG_RAGGED_VERIFY_MODE"] != "static":
        raise SystemExit("deepseek_flash SGLANG_RAGGED_VERIFY_MODE 必须显式为 static")
    if container_env["DSV41_LAUNCHER"] != "tp4":
        raise SystemExit("deepseek_flash DSV41_LAUNCHER 必须为 tp4，确保 TP4 adapter 门真实生效")
    if container_env["B12X_ROCE_TWO_WAVE_THRESHOLD_BYTES"] != "0":
        raise SystemExit("deepseek_flash B12X two-wave 阈值必须显式关闭为 0")
    if container_env["DSV41_PREFILL_BYPASS"] != "2048":
        raise SystemExit("deepseek_flash DSV41_PREFILL_BYPASS 必须为 2048")
    if container_env["DSV41_ENGRAM_CACHE_GIB"] != "2":
        raise SystemExit("deepseek_flash DSV41_ENGRAM_CACHE_GIB 必须为 2")
    if container_env["NCCL_IB_SUBNET_PREFIX_LEN"] != values["nccl_ib_subnet_prefix_len"]:
        raise SystemExit("deepseek_flash NCCL subnet prefix 与 common.ring 派生值不一致")
    if container_env["MAX_PREFILL_TOKENS"] != max_num_batched_tokens:
        raise SystemExit("deepseek_flash MAX_PREFILL_TOKENS 与 common.max_num_batched_tokens 不一致")
    if container_env["DSV41_SPARSE_LOCAL_ADMISSION_CTX_CAP"] != max_token:
        raise SystemExit("deepseek_flash admission CTX_CAP 与 common.max_token 不一致")
    if container_env["DSV41_SPARSE_LOCAL_ADMISSION_MRR"] != max_request:
        raise SystemExit("deepseek_flash admission MRR 与 common.max_request 不一致")
    max_total_tokens = values.get("max_total_tokens", "")
    if not max_total_tokens.isdigit() or int(max_total_tokens) < 1:
        raise SystemExit(f"deepseek_flash max_total_tokens 必须为 >=1 的整数: {max_total_tokens or '<empty>'}")
    if int(max_request) * int(max_token) > int(max_total_tokens):
        raise SystemExit(
            "deepseek_flash common.max_request * common.max_token 超过 max_total_tokens: "
            f"{max_request} * {max_token} > {max_total_tokens}"
        )

    source_root = config_path.resolve().parent
    parent_git_head = git_revision(source_root)
    submodule_git_head = git_revision(source_root / target)

    managed_env: dict[str, str] = {
        "CONFIG_FILE": "/opt/spark/config.yaml",
        "PARENT_GIT_HEAD": parent_git_head,
        "SUBMODULE_GIT_HEAD": submodule_git_head,
        "TARGET_ENGINE_ID": target,
        "TARGET_CONTAINER_PREFIX": target,
        "RUNTIME_ASSETS_MANAGED": "1",
        "RUNTIME_ASSETS_ROOT": runtime_assets_root,
        "RUNTIME_ASSETS": runtime_assets,
        "CACHE_DIR": values["cache_dir"],
        "RESULTS_DIR": values["results_dir"],
        "ENGRAM_DIR": values["engram_dir"],
        "MODEL_DIR": values["model_path"],
        "MODEL_CONTAINER_PATH": values["model_container_path"],
        "MODEL_CONFIG_SHA256": str(engine.get("model_config_sha256", "")),
        "MODEL_SHARDS": str(engine.get("model_shards", "")),
        "MODEL_SHARD_BYTES": str(engine.get("model_shard_bytes", "")),
        "IMAGE": image,
        "IMAGE_ID": image_id,
        "IMAGE_VERSION_LABEL": str(engine.get("image_version_label", "")),
        "IMAGE_LAYERS": str(engine.get("image_layers", "")),
        "SKIP_BUILD": "true",
        "API_HOST": values["api_host"],
        "PORT": values["port"],
        "HEAD_NODE": values["head_node"],
        "HEAD_MANAGEMENT_IP": head_management_ip,
        "HEAD_SSH_TARGET": f"{user}@{head_management_ip}",
        "WORKER_NODES": values["worker_nodes"],
        "WORKER_MANAGEMENT_IPS": values["worker_management_ips"],
        "WORKER_SSH_TARGETS": worker_ssh_targets,
        "WORKER_USER": user,
        "RUNTIME_RANK_ORDER": values["runtime_rank_order"],
        "WORKER_RANKS": values["worker_ranks"],
        "ALL_TARGET_CONTAINER_PREFIXES": " ".join(all_prefixes),
        "ADAPTER_DIR": str(engine.get("adapter_dir", "")),
        "ADAPTER_RUNTIME_MANIFEST": "/opt/spark/deepseek_flash/deploy/managed/adapter-runtime-MANIFEST.sha256",
        "ADAPTER_RUNTIME_FILES_EXPECTED": "81",
        "OVERLAY_DIR": str(engine.get("overlay_dir", "")),
        "OVERLAY_MAP": f"{engine.get('overlay_dir', '')}/overlay-map.tsv",
        "OVERLAY_MD5_MANIFEST": "/opt/spark/deepseek_flash/deploy/prod/overlay-md5.txt",
        "OVERLAY_FILES_EXPECTED": "29",
        "B12X_RING_HOST_DIR": values["b12x_ring_dir"],
        "B12X_NEXT_HOST_DIR": str(engine.get("b12x_next_dir", "")),
        "NCCL_HOST_DIR": values["ring_nccl_dir"],
        "NCCL_SHA256": values["ring_nccl_sha256"],
        "NCCL_RING_DEV_MAP": container_env.get("NCCL_RING_DEV_MAP", ""),
        "NCCL_IB_GID_INDEX_FORCE": gid_force,
        "NCCL_PIP_SO": values["nccl_pip_so"],
        "NCCL_CONTAINER_DIR": values["nccl_container_dir"],
        "NCCL_SO_NAME": values["nccl_so_name"],
        "ENGRAM_MANIFEST": "/opt/spark/deepseek_flash/deploy/reconstruct/0.3.2/weights/engram-bins-manifest.json",
        "ENGRAM_FILES_PER_RANK": "2",
        "CONTAINER_MEMORY": str(engine.get("container_memory", "")),
        "DIST_TIMEOUT": str(engine.get("dist_timeout", "")),
        "WATCHDOG_TIMEOUT": str(engine.get("watchdog_timeout", "")),
        "SERVED_MODEL_NAME": values["served_model_name"],
        "SGLANG_EXTRA_ARGS_JSON": json.dumps(extra_args, ensure_ascii=False),
        "CONTAINER_ENV_KEYS": " ".join(sorted(container_env)),
        "CONTAINER_ENV_JSON": json.dumps(container_env, ensure_ascii=False, sort_keys=True),
        "API_KEY": str(common.get("api_key", "")),
        "DEPLOY_BENCH_DE_C1_MIN": "91",
        "DEPLOY_BENCH_DE_C1_TARGET": "93.47",
        "DEPLOY_BENCH_PR_32K_TARGET": "6485.2",
        "DEPLOY_BENCH_PR_64K_TARGET": "6532.0",
        "DEPLOY_BENCH_PR_128K_TARGET": "6260.0",
        "DEPLOY_BENCH_PR_256K_TARGET": "5604.0",
        "DEPLOY_BENCH_PR_512K_TARGET": "4411.8",
        "DEPLOY_BENCH_512K_TTFT_TARGET": "118.839",
    }

    if any("\n" in value or "\t" in value for value in managed_env.values()):
        raise SystemExit("deepseek_flash managed env 含非法换行或制表符")
    for key, value in managed_env.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise SystemExit(f"deepseek_flash managed env key 非法: {key}")
        print(f"{key}\t{value}")
    return 0


def _deepseek_ds41f_ring_container_env(topology: dict[str, dict[str, str]]) -> dict[str, Any]:
    """从 common.ring 派生 sparknet ring4 的逐节点容器环境。"""
    management_interfaces = {record["management_if"] for record in topology.values()}
    if len(management_interfaces) != 1 or "" in management_interfaces:
        raise SystemExit(
            "deepseek 要求四机共用同一个受管 management_if: "
            f"{sorted(management_interfaces)}"
        )
    gid_indexes = {record["roce_gid_index"] for record in topology.values()}
    if len(gid_indexes) != 1:
        raise SystemExit(f"deepseek 要求四机共用同一个 RoCE GID index: {sorted(gid_indexes)}")
    hca_sets = {tuple(sorted(record["hca_all"].split(","))) for record in topology.values()}
    if len(hca_sets) != 1:
        raise SystemExit("deepseek 要求四机使用同一组、同顺序的 RoCE HCA")

    peer_hcas_by_rank: dict[str, dict[str, list[str]]] = {}
    for _, record in sorted(topology.items(), key=lambda item: int(item[1]["rank"])):
        peer_hcas: dict[str, list[str]] = {}
        for port_index in (0, 1):
            peer = record[f"fabric_neighbor{port_index}"]
            peer_rank = topology[peer]["rank"]
            peer_hcas.setdefault(peer_rank, []).extend(
                [record[f"fabric_hca{port_index}"], record[f"fabric2_hca{port_index}"]]
            )
        expected_peers = {
            str((int(record["rank"]) - 1) % 4),
            str((int(record["rank"]) + 1) % 4),
        }
        if set(peer_hcas) != expected_peers:
            raise SystemExit(
                f"deepseek rank {record['rank']} 的 sparknet 邻居不完整: {sorted(peer_hcas)}"
            )
        peer_hcas_by_rank[record["rank"]] = peer_hcas

    management_if = next(iter(management_interfaces))
    hcas = sorted(next(iter(hca_sets)))
    return {
        "GLOO_SOCKET_IFNAME": management_if,
        "TP_SOCKET_IFNAME": management_if,
        "NCCL_SOCKET_IFNAME": management_if,
        "NCCL_IB_GID_INDEX": next(iter(gid_indexes)),
        "NCCL_IB_HCA": "=" + ",".join(hcas),
        "SPARKNET_ROCE_PEER_HCAS_BY_RANK": json.dumps(
            peer_hcas_by_rank, separators=(",", ":"), sort_keys=True
        ),
    }


def _deepseek_ds41f_managed_env(
    config_path: Path, target: str, model_id: str
) -> list[tuple[str, str]]:
    """deepseek（spark-ds41f）的宿主受管环境与站点覆盖集。

    上游固定配方由 .env.deepseek.example 保存；本函数只输出 launcher 实际消费
    的宿主编排键，以及 config.yaml/common.ring/common.spark_nodes 必须覆盖的
    容器键。宿主键与容器键的消费边界由
    tests/test_deepseek_env_merge_contract.py 锁定。
    """
    values, engine = _managed_model_values(config_path, target, model_id)
    if values.get("engine_id") != target:
        raise SystemExit(f"query-model 返回了错误 target: {values.get('engine_id')}")
    if values.get("nnodes") != "4" or values.get("tensor_parallel_size") != "4":
        raise SystemExit("deepseek 必须保持 nnodes=4、tensor_parallel_size=4 的 TP4 环")
    topology = load_ring_topology(config_path)
    expected_runtime_rank_order = " ".join(
        node for node, _record in sorted(topology.items(), key=lambda item: int(item[1]["rank"]))
    )
    if values.get("runtime_rank_order") != expected_runtime_rank_order:
        raise SystemExit(
            "deepseek runtime rank 必须从 common.ring 唯一派生: "
            f"{expected_runtime_rank_order}"
        )
    if values.get("head_rank") != "0" or values.get("worker_ranks") != "1 2 3":
        raise SystemExit("deepseek rank 面必须为 head=0、workers=1 2 3")
    runtime_assets_root = values["runtime_assets_root"]
    runtime_assets = values["runtime_assets"]
    expected_runtime_assets = f"{runtime_assets_root}/{target}"
    if runtime_assets != expected_runtime_assets:
        raise SystemExit(f"deepseek runtime_assets 必须精确为 {expected_runtime_assets}")
    cache_dir = values["cache_dir"]
    if cache_dir != f"{runtime_assets}/cache":
        raise SystemExit(f"CACHE_DIR 必须精确为 {runtime_assets}/cache，收到 {cache_dir}")

    image = str(engine.get("image", ""))
    image_id = str(engine.get("image_id", ""))
    # 镜像由 config.yaml 显式登记 tag+ID；删除旧生产回退后不再内置隐式白名单。
    if engine.get("allow_unverified_image") is not True:
        raise SystemExit(
            "deepseek 镜像身份未登记为本站已验收组合（tag 与 image ID 必须同时匹配）: "
            f"{image or '<empty>'} / {image_id or '<empty>'}"
        )
    if not re.fullmatch(r"spark-ds41f:[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", image):
        raise SystemExit(f"deepseek 实验镜像 tag 非法: {image or '<empty>'}")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise SystemExit(f"deepseek 实验镜像 image ID 非法: {image_id or '<empty>'}")

    model_container_path = values.get("model_container_path", "")
    if not model_container_path.startswith("/"):
        raise SystemExit(
            f"deepseek model_container_path 必须是绝对路径: {model_container_path or '<empty>'}"
        )
    subnet_prefix_len = values.get("nccl_ib_subnet_prefix_len", "")
    if not subnet_prefix_len.isdigit() or not 1 <= int(subnet_prefix_len) <= 32:
        raise SystemExit(
            "deepseek NCCL_IB_SUBNET_PREFIX_LEN 必须由 common.ring 派生为 1-32: "
            f"{subnet_prefix_len or '<empty>'}"
        )
    ring_env = _deepseek_ds41f_ring_container_env(topology)

    common = load_config(config_path).get("common") or {}
    user = values["user"]
    head_management_ip = values["head_management_ip"]
    worker_management_ips = values["worker_management_ips"].split()
    if not user or not head_management_ip or len(worker_management_ips) != 3:
        raise SystemExit("deepseek 需要四机管理 SSH inventory")
    worker_ssh_targets = " ".join(f"{user}@{ip}" for ip in worker_management_ips)

    managed_env: dict[str, str] = {
        "TARGET_ENGINE_ID": target,
        "TARGET_CONTAINER_PREFIX": target,
        "RUNTIME_ASSETS": runtime_assets,
        "CACHE_DIR": cache_dir,
        "MODEL_DIR": values["model_path"],
        "MODEL_CONTAINER_PATH": model_container_path,
        "IMAGE": image,
        "IMAGE_ID": image_id,
        "API_HOST": values["api_host"],
        "PORT": values["port"],
        "HEAD_MANAGEMENT_IP": head_management_ip,
        "HEAD_SSH_TARGET": f"{user}@{head_management_ip}",
        "WORKER_MANAGEMENT_IPS": values["worker_management_ips"],
        "WORKER_SSH_TARGETS": worker_ssh_targets,
        "WORKER_RANKS": values["worker_ranks"],
        "NNODES": values["nnodes"],
        "TP_SIZE": values["tensor_parallel_size"],
        **ring_env,
        "NCCL_IB_SUBNET_PREFIX_LEN": subnet_prefix_len,
        "API_KEY": str(common.get("api_key", "")),
    }
    runtime_profile = str(engine.get("runtime_profile", "")).strip()
    if runtime_profile:
        profile_path = Path(runtime_profile)
        engine_dir = str(engine.get("engine_dir", target)).strip()
        if (
            profile_path.is_absolute()
            or ".." in profile_path.parts
            or not engine_dir
            or Path(engine_dir).is_absolute()
            or ".." in Path(engine_dir).parts
        ):
            raise SystemExit(
                "deepseek runtime_profile 必须是 engine_dir 内不含 .. 的相对路径: "
                f"{runtime_profile}"
            )
        # 内容与可达性由 head launcher 在启动前 fail-closed 校验；这里只负责把
        # engine_dir 内安全相对路径传给部署层，避免仓库外 profile 混入运行态。
        managed_env["DS41F_RUN_PROFILE"] = runtime_profile
    experimental_container_keys = {
        "SPARK_DS41F_MOE_SLICE_TOKENS",
        "SPARK_DS41F_PREFILL_BLOCK_M",
        "SPARK_DS41F_WO_SLICE_TOKENS",
    }
    experimental_container_env = engine.get("experimental_container_env", {})
    if experimental_container_env is None:
        experimental_container_env = {}
    if not isinstance(experimental_container_env, dict):
        raise SystemExit("deepseek experimental_container_env 必须是映射")
    unknown_experimental_keys = sorted(
        set(experimental_container_env) - experimental_container_keys
    )
    if unknown_experimental_keys:
        raise SystemExit(
            f"deepseek experimental_container_env 含未登记键: {unknown_experimental_keys}"
        )
    for key, value in experimental_container_env.items():
        text = str(value)
        if not text.isdigit() or not 1 <= int(text) <= (1 << 20):
            raise SystemExit(f"deepseek experimental_container_env.{key} 必须是 1..1048576: {value!r}")
        managed_env[key] = text

    managed_env["DS41F_ALLOW_UNVERIFIED_IMAGE"] = "1"
    managed_env["DS41F_CONFIG_FILE"] = str(
        engine.get("ds41f_config_file", "config/cluster.json")
    )
    if any("\n" in value or "\t" in value for value in managed_env.values()):
        raise SystemExit("deepseek managed env 含非法换行或制表符")
    for key in managed_env:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise SystemExit(f"deepseek managed env key 非法: {key}")
    return list(managed_env.items())


def command_query_engines(arguments: argparse.Namespace) -> int:
    config = load_config(Path(arguments.config))
    engines = config.get("engines") or {}
    for configured_engine_id, configured_engine in engines.items():
        engine_id = str(configured_engine_id)
        engine = configured_engine or {}
        engine_dir = str(engine.get("engine_dir", engine_id))
        env_dir = str(engine.get("env_dir", engine_id))
        stop_script, stop_args = parse_command(engine.get("stop"), engine_id, "stop")
        values = [
            engine_id,
            engine_dir,
            env_dir,
            stop_script,
            stop_args or NO_ARGS,
            str(engine.get("env_file", "")),
            str(engine.get("env_template", "")),
            str(engine_enabled(engine.get("enabled", True))).lower(),
            str(engine.get("unavailable_reason", "")) or "-",
            str(engine_enabled(engine.get("remote_head", False))).lower(),
        ]
        validate_output_values(values, f"引擎 {engine_id} 配置")
        print("\t".join(values))
    return 0


def command_write_managed_env(arguments: argparse.Namespace) -> int:
    env_path = Path(arguments.path)
    updates = []
    update_keys = set()
    for pair in arguments.pair:
        key, separator, value = pair.partition("=")
        if not separator or not key.isidentifier():
            raise SystemExit(f"非法环境覆盖项: {pair}")
        update_keys.add(key)
        updates.append(f"{key}={shlex.quote(value)}")
    # DeepSeek previously received the business-level MAX_TOKEN alias. Once a
    # target is migrated to the engine-facing CONTEXT_LENGTH adapter, remove
    # the old alias so stale env files cannot suggest a second source of truth.
    obsolete_keys = {"MAX_TOKEN"} if "CONTEXT_LENGTH" in update_keys else set()

    begin_marker = f"# BEGIN deploy.sh {arguments.marker}"
    end_marker = f"# END deploy.sh {arguments.marker}"
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    retained_lines = []
    deferred_source_lines = []
    inside_managed_block = False
    for line in existing.splitlines():
        if line.startswith("# BEGIN deploy.sh "):
            inside_managed_block = True
            continue
        if inside_managed_block and line.startswith("# END deploy.sh "):
            inside_managed_block = False
            continue
        if not inside_managed_block:
            # 受管变量可能是 profile 的输入；source 必须放在生成块之后，避免
            # profile 在 set -u 下读取尚未写入的 IB_HCA 等变量。
            if re.match(r"^\s*(?:source|\.)\s+", line):
                deferred_source_lines.append(line)
                continue
            # 模板中的旧裸赋值不能继续与受管块并存；shell 会静默采用最后一行，
            # 造成同一配置在人工读取、A/B 和远端重启之间出现隐式漂移。
            assignment = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line)
            if assignment and assignment.group(1) in update_keys | obsolete_keys:
                continue
            retained_lines.append(line)
    while retained_lines and retained_lines[-1] == "":
        retained_lines.pop()
    retained_lines.extend(["", begin_marker, *updates, end_marker])
    if deferred_source_lines:
        retained_lines.extend(["", *deferred_source_lines])
    retained_lines.append("")
    content = "\n".join(retained_lines)

    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{env_path.name}.deploy-",
            dir=str(env_path.parent),
        )
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
            os.replace(temporary_name, env_path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    except OSError as exc:
        raise SystemExit(f"无法原子更新环境文件 {env_path}: {exc}") from exc
    return 0


def command_list_models(arguments: argparse.Namespace) -> int:
    config = load_config(Path(arguments.config))
    common = config.get("common") or {}
    root = str(common.get("model_path", "")).rstrip("/")
    default_model = normalize_model_id(root, common.get("default_model", ""))
    for configured_engine_id, configured_engine in (config.get("engines") or {}).items():
        engine_id = str(configured_engine_id)
        engine = configured_engine or {}
        for model_item in (configured_engine or {}).get("models") or []:
            model_id = str(model_item.get("id", ""))
            role = str(model_item.get("role", ""))
            # list is the operator-facing start menu: dependency-only models
            # are consumed by a target launcher and are not valid start routes.
            if role != "target":
                continue
            marker = "*" if normalize_model_id(root, model_id) == default_model else " "
            enabled = engine_enabled(engine.get("enabled", True))
            state = "ready" if role == "target" and enabled else "blocked"
            reasons = [str(reason) for reason in (model_item.get("reason"), engine.get("unavailable_reason")) if reason]
            reason = f" — {'；'.join(reasons)}" if reasons else ""
            container_names = ",".join(target_container_names(engine_id, int(engine.get("nnodes", 1))))
            print(f"{marker} {model_id}\t{role}\t{engine_id}\t{state}\t{reason}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Spark 部署总控的 Python 工具集")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check-dependency", help="检查 Python 运行依赖")
    check_parser.set_defaults(handler=command_check_dependency)

    query_model_parser = subparsers.add_parser("query-model", help="解析模型路由")
    query_model_parser.add_argument("--config", required=True, help="配置文件路径")
    query_model_parser.add_argument("--target", default="", help="目标 engine ID")
    query_model_parser.add_argument("--selection", default="", help="模型 ID 或模型绝对路径")
    query_model_parser.set_defaults(handler=command_query_model)

    query_engines_parser = subparsers.add_parser("query-engines", help="枚举引擎停止入口")
    query_engines_parser.add_argument("--config", required=True, help="配置文件路径")
    query_engines_parser.set_defaults(handler=command_query_engines)

    managed_env_parser = subparsers.add_parser(
        "query-managed-env", help="生成 target 的最终宿主/容器环境"
    )
    managed_env_parser.add_argument("--config", required=True, help="配置文件路径")
    managed_env_parser.add_argument("--target", required=True, help="目标 engine ID")
    managed_env_parser.add_argument("--model", default="", help="模型 ID")
    managed_env_parser.set_defaults(handler=command_query_managed_env)

    ring_parser = subparsers.add_parser("ring-topology", help="输出 config.yaml 中的物理环记录")
    ring_parser.add_argument("--config", required=True, help="配置文件路径")
    ring_parser.add_argument("--json", action="store_true", help="输出供程序消费的完整拓扑 JSON")
    ring_parser.set_defaults(handler=command_ring_topology)

    ring_local_parser = subparsers.add_parser("ring-local-fabric", help="输出本机环网接口/HCA 与主机参数")
    ring_local_parser.add_argument("--config", required=True, help="配置文件路径")
    ring_local_parser.add_argument("--node", default="", help="节点名；默认读取本机 hostname")
    ring_local_parser.set_defaults(handler=command_ring_local_fabric)

    ring_prefix_parser = subparsers.add_parser(
        "ring-fabric-prefixes", help="输出 config.yaml 中双 fabric 平面 IPv4 前缀"
    )
    ring_prefix_parser.add_argument("--config", required=True, help="配置文件路径")
    ring_prefix_parser.set_defaults(handler=command_ring_fabric_prefixes)

    ring_route_parser = subparsers.add_parser(
        "ring-transfer-route", help="输出 rank 顺序中可达的逐跳物理环传输路径"
    )
    ring_route_parser.add_argument("--config", required=True, help="配置文件路径")
    ring_route_parser.set_defaults(handler=command_ring_transfer_route)

    ring_peer_hca_parser = subparsers.add_parser(
        "ring-peer-hca", help="输出按物理或 target runtime rank 编号的环邻居 HCA 映射"
    )
    ring_peer_hca_parser.add_argument("--config", required=True, help="配置文件路径")
    ring_peer_hca_parser.add_argument("--target", default="", help="target 名称；默认使用物理 rank 顺序")
    ring_peer_hca_parser.set_defaults(handler=command_ring_peer_hca)

    ring_runtime_fabric_parser = subparsers.add_parser(
        "ring-runtime-fabric", help="按 target runtime rank 输出前后邻居接口/HCA"
    )
    ring_runtime_fabric_parser.add_argument("--config", required=True, help="配置文件路径")
    ring_runtime_fabric_parser.add_argument("--target", default="", help="target 名称；默认使用物理 rank 顺序")
    ring_runtime_fabric_parser.set_defaults(handler=command_ring_runtime_fabric)

    netplan_parser = subparsers.add_parser("render-ring-netplan", help="从环拓扑渲染单节点 fabric Netplan")
    netplan_parser.add_argument("--config", required=True, help="配置文件路径")
    netplan_parser.add_argument("--node", required=True, help="节点名")
    netplan_parser.set_defaults(handler=command_render_ring_netplan)

    env_parser = subparsers.add_parser("write-managed-env", help="原子更新环境文件覆盖块")
    env_parser.add_argument("--path", required=True, help="环境文件路径")
    env_parser.add_argument("--marker", required=True, help="覆盖块标识")
    env_parser.add_argument("--pair", action="append", required=True, help="KEY=VALUE 覆盖项")
    env_parser.set_defaults(handler=command_write_managed_env)

    list_parser = subparsers.add_parser("list-models", help="列出模型和可启动状态")
    list_parser.add_argument("--config", required=True, help="配置文件路径")
    list_parser.set_defaults(handler=command_list_models)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    return arguments.handler(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
