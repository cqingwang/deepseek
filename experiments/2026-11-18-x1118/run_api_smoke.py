#!/usr/bin/env python3
"""Replay the stock-model API and multimodal regressions for x1118."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REGRESSIONS = (
    ROOT
    / "experiments/2026-11-14-upstream-api-fixes/run_api_regressions.py"
)


def load_regressions():
    spec = importlib.util.spec_from_file_location("x1118_api_regressions", REGRESSIONS)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {REGRESSIONS}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    regressions = load_regressions()
    rows = []
    for name, path, body in regressions.direct_cases():
        status, response = regressions.post(args.base + path, args.api_key, body)
        rows.append(
            {
                "name": name,
                "path": path,
                "status": status,
                "response_id": response.get("id"),
                "finish_reason": (
                    response.get("choices", [{}])[0].get("finish_reason")
                    if response.get("choices")
                    else None
                ),
                "reasoning_tokens": (
                    response.get("usage", {})
                    .get("completion_tokens_details", {})
                    .get("reasoning_tokens")
                ),
                "response": response,
            }
        )
    result = {
        "schema_version": 1,
        "base": args.base,
        "rows": rows,
        "all_200": all(row["status"] == 200 for row in rows),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if not result["all_200"]:
        for row in rows:
            print(f"ERROR: {row['name']}: {row['status']}")
        return 1
    print(f"API smoke OK: {len(rows)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
