#!/usr/bin/env python3
"""Summarize the two x1123/1122 runs against the current 64 KiB reference."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


# Current 64 KiB/1120 README c1 and raw same-release c2/c4 values. The
# historical 4 KiB r5l table is deliberately absent.
DECODE_GATES = {
    "prose-c1": 89.1,
    "prose-c2": 150.394,
    "prose-c4": 266.942,
    "code-c1": 105.117,
    "code-c2": 186.001,
    "code-c4": 329.017,
}
PREFILL_GATES = {
    "32768": 9615.0,
    "131072": 10108.0,
    "262144": 10056.0,
    "524288": 9833.0,
    "1000000": 9397.0,
}
ALLOWED_LOSS_PCT = 1.0


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != 1:
        raise SystemExit(f"{path}: unexpected bench schema {value.get('schema')!r}")
    if value.get("workload", {}).get("prefill_text") != "source":
        raise SystemExit(f"{path}: expected source-text prefill")
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
            "official_reference": gate,
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
            "official_reference": gate,
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
        "reference": "current-64kib-1120-readme+manifest",
        "decode_reference_note": (
            "prose-c1 is the current README value; c2/c4 and code are the "
            "same-release 1120 manifest tps values because the current README "
            "does not publish those cells"
        ),
        "prefill_reference_note": (
            "current README source-text prefill; the historical 4 KiB r5l "
            "table is not used"
        ),
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
