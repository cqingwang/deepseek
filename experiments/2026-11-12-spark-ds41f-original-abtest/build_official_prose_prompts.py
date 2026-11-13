#!/usr/bin/env python3
"""Emit the exact prose style used by the official performance report."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "docs/spark-ds41f"))

from servebench.prompts import default_prompts


CODE_STYLES = ("Python function", "SQL schema")
STRUCTURED_STYLES = ("JSON object",)


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/opt/abtest-inputs/prose-prompts-official.jsonl")
    prompts = []
    for prompt in default_prompts():
        text = prompt["text"]
        if any(marker in text for marker in CODE_STYLES):
            continue
        if any(marker in text for marker in STRUCTURED_STYLES):
            continue
        prompts.append(prompt)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(prompt, ensure_ascii=False) for prompt in prompts) + "\n")
    print(json.dumps({"count": len(prompts), "out": str(out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
