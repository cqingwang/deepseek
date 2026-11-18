#!/usr/bin/env python3
"""Summarize two x1118 bench runs against the official README workbooks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


# Official README reasoning-off decode table, aggregate tok/s.
DECODE_GATES = {
    "prose-nothink-c1": 59.1,
    "prose-nothink-c2": 84.8,
    "prose-nothink-c4": 124.9,
    "code-nothink-c1": 80.6,
    "code-nothink-c2": 118.2,
    "code-nothink-c4": 165.1,
}
# Current tp4-ring README performance summary, source-text prefill tok/s.
PREFILL_GATES = {
    "32768": 8508.0,
    "131072": 8657.0,
    "262144": 8261.0,
    "524288": 7460.0,
    "1000000": 6012.0,
}
ALLOWED_LOSS_PCT = 1.0


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != 1:
        raise SystemExit(f"{path}: unexpected bench schema {value.get('schema')!r}")
    return value


def point_mean(run: dict, suite: str, point: str, metric: str) -> float:
    value = run["suites"][suite]["points"][point][metric]["mean"]
    if not isinstance(value, (int, float)):
        raise SystemExit(f"run: {suite}.{point}.{metric} is not numeric")
    return float(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", nargs=2, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    runs = [load(path) for path in args.run]

    decode: dict[str, dict] = {}
    for point, gate in DECODE_GATES.items():
        per_run = [point_mean(run, "decode", point, "tps") for run in runs]
        mean = sum(per_run) / len(per_run)
        floor = gate * (1.0 - ALLOWED_LOSS_PCT / 100.0)
        decode[point] = {
            "runs": per_run,
            "mean": mean,
            "official_readme": gate,
            "allowed_floor": floor,
            "delta_pct": (mean / gate - 1.0) * 100.0,
            "passed": mean >= floor,
        }

    prefill: dict[str, dict] = {}
    for size, gate in PREFILL_GATES.items():
        per_run = [point_mean(run, "prefill", size, "prefill_tps") for run in runs]
        mean = sum(per_run) / len(per_run)
        floor = gate * (1.0 - ALLOWED_LOSS_PCT / 100.0)
        prefill[size] = {
            "runs": per_run,
            "mean": mean,
            "official_readme": gate,
            "allowed_floor": floor,
            "delta_pct": (mean / gate - 1.0) * 100.0,
            "passed": mean >= floor,
        }

    failures = [
        f"decode {point}: {row['mean']:.3f} < {row['allowed_floor']:.3f}"
        for point, row in decode.items()
        if not row["passed"]
    ]
    failures += [
        f"prefill {size}: {row['mean']:.3f} < {row['allowed_floor']:.3f}"
        for size, row in prefill.items()
        if not row["passed"]
    ]
    summary = {
        "schema_version": 1,
        "runs": [str(path) for path in args.run],
        "allowed_loss_pct": ALLOWED_LOSS_PCT,
        "decode": decode,
        "prefill": prefill,
        "failures": failures,
        "ok": not failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": summary["ok"], "failures": failures}, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
