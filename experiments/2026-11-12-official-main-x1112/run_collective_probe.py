#!/usr/bin/env python3
"""Run the live x1112 container env through the TP4 collective probe.

The probe reuses the launcher-rendered Docker arguments and container
environment, but replaces the serving entrypoint with the probe script. This
keeps the measured fabric path identical to serving while avoiding a second,
independently maintained copy of the per-rank HCA and rendezvous settings.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path


HEAD = "192.168.2.180"
HOSTS = {
    0: "192.168.2.180",
    1: "192.168.2.161",
    2: "192.168.2.102",
    3: "192.168.2.238",
}
REMOTE_ROOT = "/opt/spark/deepseek"
IMAGE = "spark-ds41f:x1112"
DEFAULT_PROBE_SOURCE = f"{REMOTE_ROOT}/scripts/probe_collectives.py"
MASTER_PORT = 29581


def ssh(host: str, command: str, *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
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
        capture_output=True,
        timeout=timeout,
    )


def launcher_commands() -> dict[int, list[str]]:
    result = ssh(
        HEAD,
        f"cd {shlex.quote(REMOTE_ROOT)} && bash ./start-tp4.sh print-env",
    )
    if result.returncode:
        raise RuntimeError(f"launcher print-env failed:\n{result.stdout}\n{result.stderr}")
    parts = re.split(r"^== rank ([0-3]) ==$", result.stdout, flags=re.MULTILINE)
    if len(parts) != 9:
        raise RuntimeError(f"unexpected launcher output:\n{result.stdout}")
    return {
        int(parts[index]): shlex.split(parts[index + 1].strip())
        for index in range(1, len(parts), 2)
    }


def probe_command(rank: int, launcher: list[str], probe_source: str) -> list[str]:
    if launcher[:2] != ["docker", "run"] or launcher[2] != "-d":
        raise ValueError(f"rank {rank}: unexpected launcher prefix {launcher[:4]}")
    if launcher[4] != f"deepseek-r{rank}":
        raise ValueError(f"rank {rank}: unexpected container name {launcher[4]}")
    try:
        image = launcher.index(IMAGE)
    except ValueError as error:
        raise ValueError(f"rank {rank}: launcher does not name {IMAGE}") from error

    command = launcher[:image]
    command[2] = "--rm"
    command[4] = f"spark-collective-probe-r{rank}"
    command.insert(5, "--entrypoint=/usr/bin/timeout")
    command = [
        "--memory=12g" if token == "--memory=112g"
        else "--memory-swap=12g" if token == "--memory-swap=112g"
        else token
        for token in command
    ]
    command.extend(
        [
            "--volume",
            f"{probe_source}:/probe.py:ro",
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
            HEAD,
            "--master-port",
            str(MASTER_PORT),
            "--benchmark",
            "--counter-samples",
            "--numerics",
        ]
    )
    return command


def parse_probe_output(output: str) -> tuple[dict | None, list[dict]]:
    records = []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    finals = [record for record in records if "timings" in record]
    return (finals[-1] if finals else None), records


def summarize(rank: int, returncode: int, stdout: str, stderr: str) -> dict:
    final, records = parse_probe_output(stdout)
    timings = []
    if final:
        for timing in final.get("timings", []):
            samples = sorted(timing.get("microseconds_per_call", []))
            median = samples[len(samples) // 2] if samples else None
            timings.append(
                {
                    "dtype": timing.get("dtype"),
                    "elements_per_rank": timing.get("elements_per_rank"),
                    "operation": timing.get("operation"),
                    "median_us": median,
                    "expected_backend": timing.get("expected_backend"),
                    "proxy_payload_bytes": timing.get("proxy_payload_bytes"),
                    "port_deltas": timing.get("port_deltas"),
                    "rdma_error_deltas": timing.get("rdma_error_deltas"),
                }
            )
    return {
        "rank": rank,
        "returncode": returncode,
        "passed": bool(final and final.get("passed") is True and returncode == 0),
        "transport": final.get("transport") if final else None,
        "checks": len(final.get("checks", [])) if final else 0,
        "numerical_checks": len(final.get("numerical_checks", [])) if final else 0,
        "timing_count": len(timings),
        "timings": timings,
        "json_records": len(records),
        "stderr_tail": "\n".join(stderr.splitlines()[-40:]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--probe-source", default=DEFAULT_PROBE_SOURCE)
    parser.add_argument(
        "--port-samples",
        action="store_true",
        help="enable in-image ethtool samples; the x1112 image does not provide ethtool",
    )
    parser.add_argument("--timeout", type=int, default=720)
    args = parser.parse_args()

    commands = {
        rank: probe_command(rank, launcher, args.probe_source)
        for rank, launcher in launcher_commands().items()
    }
    if args.port_samples:
        for command in commands.values():
            command.insert(command.index("--numerics"), "--port-samples")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "commands.json").write_text(
        json.dumps(
            {
                str(rank): command
                for rank, command in commands.items()
            },
            indent=2,
        )
        + "\n"
    )
    if args.dry_run:
        for rank, command in commands.items():
            print(f"# rank {rank} @ {HOSTS[rank]}")
            print(shlex.join(command))
        return 0

    def run_one(rank: int) -> tuple[int, subprocess.CompletedProcess[str]]:
        command = commands[rank]
        result = ssh(
            HOSTS[rank],
            "bash -lc " + shlex.quote(shlex.join(command)),
            timeout=args.timeout,
        )
        (args.out / f"rank-{rank}.stdout.jsonl").write_text(result.stdout)
        (args.out / f"rank-{rank}.stderr.log").write_text(result.stderr)
        return rank, result

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(run_one, rank) for rank in commands]
        for future in concurrent.futures.as_completed(futures):
            rank, result = future.result()
            results[rank] = summarize(rank, result.returncode, result.stdout, result.stderr)
            print(
                f"rank {rank}: rc={result.returncode} passed={results[rank]['passed']} "
                f"transport={results[rank]['transport']} timings={results[rank]['timing_count']}",
                flush=True,
            )

    summary = {
        "probe": str(Path(__file__).resolve()),
        "image": IMAGE,
        "master_addr": HEAD,
        "master_port": MASTER_PORT,
        "ranks": results,
        "passed": all(item["passed"] for item in results.values()),
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"passed": summary["passed"], "out": str(args.out)}, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
