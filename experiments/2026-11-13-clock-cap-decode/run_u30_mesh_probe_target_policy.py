#!/usr/bin/env python3
"""Run the collective probe with the live target's 8 MiB one-shot policy."""

from __future__ import annotations

import concurrent.futures
import json
import shlex
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
IMAGE = "spark-ds41f:x1112"
PROBE = "/opt/spark/deepseek/scripts/probe_collectives.py"
NODES = (
    (0, "192.168.2.180", ROOT / "remote-inspect-r0.json"),
    (1, "192.168.2.161", ROOT / "remote-inspect-r1.json"),
    (2, "192.168.2.102", ROOT / "remote-inspect-r2.json"),
    (3, "192.168.2.238", ROOT / "remote-inspect-r3.json"),
)
OVERRIDES = {
    "NCCL_DEBUG": "INFO",
    "NCCL_DEBUG_SUBSYS": "INIT,GRAPH,NET",
}


def command(rank: int, host: str, inspect_path: Path) -> str:
    record = json.loads(inspect_path.read_text(encoding="utf-8"))[0]
    env = dict(item.split("=", 1) for item in record["Config"]["Env"])
    env.update(OVERRIDES)
    args = [
        "docker",
        "run",
        "--rm",
        "--name=spark-collective-probe-target-policy",
        "--network=host",
        "--ipc=host",
        "--gpus=all",
        "--device=/dev/infiniband:/dev/infiniband:rwm",
        "--cap-add=IPC_LOCK",
        "--ulimit=memlock=-1:-1",
        "--ulimit=core=0",
        "--shm-size=256m",
        f"--cpuset-cpus={record['HostConfig']['CpusetCpus']}",
        "--entrypoint=/usr/bin/timeout",
    ]
    for key, value in sorted(env.items()):
        args.extend(("--env", f"{key}={value}"))
    for bind in record["HostConfig"].get("Binds") or []:
        args.extend(("--volume", bind))
    args.extend(
        (
            "--volume",
            f"{PROBE}:/probe.py:ro",
            IMAGE,
            "--signal=TERM",
            "--kill-after=10s",
            "600s",
            "python3",
            "/probe.py",
            "--rank",
            str(rank),
            "--world-size",
            "4",
            "--master-addr",
            "192.168.2.180",
            "--master-port",
            "29999",
            "--benchmark",
            "--counter-samples",
            "--numerics",
        )
    )
    return shlex.join(args)


def run_one(rank: int, host: str, inspect_path: Path) -> tuple[int, str, int, str, str]:
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", f"chan@{host}", "bash -s"],
        input=command(rank, host, inspect_path),
        text=True,
        capture_output=True,
        timeout=720,
    )
    return rank, host, result.returncode, result.stdout, result.stderr


def main() -> int:
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda item: run_one(*item), NODES))
    ranks = {}
    for rank, host, returncode, stdout, stderr in results:
        parsed = []
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    parsed.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        ranks[str(rank)] = {
            "host": host,
            "returncode": returncode,
            "final": parsed[-1] if parsed else None,
            "timings_stream": parsed[:-1],
            "stderr_tail": "\n".join(stderr.splitlines()[-30:]),
        }
    payload = {
        "image": IMAGE,
        "profile": "target /30 + spark-ring-mesh, live 8 MiB policy",
        "passed": all(
            item["returncode"] == 0
            and item["final"]
            and item["final"].get("passed") is True
            for item in ranks.values()
        ),
        "ranks": ranks,
    }
    output = ROOT / "u30-mesh-probe-target-policy.json"
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": payload["passed"], "output": str(output)}, indent=2))
    for rank, item in ranks.items():
        final = item["final"] or {}
        print(
            rank,
            item["host"],
            "rc=",
            item["returncode"],
            "passed=",
            final.get("passed"),
            "timings=",
            len(final.get("timings", [])),
        )
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
