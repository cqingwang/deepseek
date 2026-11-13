#!/usr/bin/env python3
"""Compare every original-abtest container with the rendered official launch contract."""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path


REPO = Path("/opt/abtest/spark-ds41f")
CONFIG = "config/abtest.local.json"
IMAGE = "spark-ds41f:x1112-original"
CONTAINER = "spark-ds41f"
NODES = {
    0: "192.168.2.180",
    1: "192.168.2.161",
    2: "192.168.2.102",
    3: "192.168.2.238",
}


def ssh(host: str, command: str) -> str:
    return subprocess.check_output(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", f"chan@{host}", command],
        text=True,
    )


def expected_contract(host: str) -> dict:
    rendered = subprocess.check_output(
        [
            str(REPO / "bin/spark"),
            "--cluster-config",
            CONFIG,
            "render",
            host,
        ],
        cwd=REPO,
        text=True,
    )
    tokens = shlex.split(rendered)
    image_index = tokens.index(IMAGE)
    env = {}
    mounts = {}
    index = 0
    while index < image_index:
        token = tokens[index]
        if token == "--env":
            key, value = tokens[index + 1].split("=", 1)
            env[key] = value
            index += 2
            continue
        if token.startswith("--env="):
            key, value = token[len("--env=") :].split("=", 1)
            env[key] = value
        elif token == "--volume":
            parts = tokens[index + 1].split(":")
            mounts[parts[1]] = {"source": parts[0], "readonly": parts[-1] == "ro"}
            index += 2
            continue
        elif token.startswith("--volume="):
            parts = token[len("--volume=") :].split(":")
            mounts[parts[1]] = {"source": parts[0], "readonly": parts[-1] == "ro"}
        index += 1
    return {"env": env, "mounts": mounts, "argv": tokens[image_index + 1 :]}


def actual_contract(host: str) -> dict:
    raw = ssh(host, f"docker inspect {CONTAINER}")
    record = json.loads(raw)[0]
    config = record["Config"]
    actual_env = dict(item.split("=", 1) for item in config.get("Env", []) if "=" in item)
    mounts = {
        mount["Destination"]: {
            "source": mount["Source"],
            "readonly": not mount["RW"],
        }
        for mount in record.get("Mounts", [])
    }
    return {
        "record": record,
        "env": actual_env,
        "mounts": mounts,
        "argv": config.get("Cmd") or [],
    }


def main() -> int:
    failures = []
    nodes = {}
    image_ids = set()
    layer_sets = set()
    for rank, host in NODES.items():
        expected = expected_contract(host)
        actual = actual_contract(host)
        record = actual["record"]
        state = record["State"]
        image = ssh(host, f"docker image inspect {IMAGE} --format '{{{{json .}}}}'")
        image_payload = json.loads(image)
        image_ids.add(image_payload["Id"])
        layer_sets.add(tuple(image_payload["RootFS"]["Layers"]))

        for key, wanted in expected["env"].items():
            if actual["env"].get(key) != wanted:
                failures.append(
                    f"rank {rank}: env {key}={actual['env'].get(key)!r}, expected {wanted!r}"
                )
        for destination, wanted in expected["mounts"].items():
            if actual["mounts"].get(destination) != wanted:
                failures.append(
                    f"rank {rank}: mount {destination}={actual['mounts'].get(destination)!r}, "
                    f"expected {wanted!r}"
                )
        if actual["argv"] != expected["argv"]:
            failures.append(f"rank {rank}: serve argv differs from render")
        if state.get("Status") != "running":
            failures.append(f"rank {rank}: status {state.get('Status')!r}")
        if state.get("OOMKilled") is not False:
            failures.append(f"rank {rank}: OOMKilled={state.get('OOMKilled')!r}")
        if record.get("RestartCount") != 0:
            failures.append(f"rank {rank}: RestartCount={record.get('RestartCount')!r}")
        if record.get("Config", {}).get("Image") != IMAGE:
            failures.append(
                f"rank {rank}: image {record.get('Config', {}).get('Image')!r} != {IMAGE!r}"
            )

        nodes[rank] = {
            "host": host,
            "container_id": record["Id"],
            "image": record["Config"]["Image"],
            "image_id": image_payload["Id"],
            "env_keys": len(expected["env"]),
            "argv_tokens": len(expected["argv"]),
            "state": {
                "status": state.get("Status"),
                "oom_killed": state.get("OOMKilled"),
                "restart_count": record.get("RestartCount"),
            },
            "mounts": actual["mounts"],
        }

    if len(image_ids) != 1:
        failures.append(f"four ranks do not share one image ID: {sorted(image_ids)}")
    if len(layer_sets) != 1:
        failures.append("four ranks do not share one RootFS layer list")
    result = {
        "ok": not failures,
        "image": IMAGE,
        "image_id": next(iter(image_ids)) if len(image_ids) == 1 else None,
        "nodes": nodes,
        "failures": failures,
    }
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
