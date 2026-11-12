#!/usr/bin/env python3
"""Emit the fixed prose-only prompt set used by the narrowed decode gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from servebench.prompts import TEMPLATES, default_prompts


# Natural-language prose only. Code, SQL, JSON objects and itemized-list templates
# are deliberately excluded from this acceptance lane.
PROSE_TEMPLATE_INDICES = (0, 1, 4, 5, 6, 9, 10, 11, 12, 13, 15)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    # Keep the exact prompt texts and ids from the official default set; only
    # remove its code, SQL, JSON and itemized-list task templates.
    prose_prefixes = tuple(
        TEMPLATES[index].partition("{topic}")[0] for index in PROSE_TEMPLATE_INDICES
    )
    prompts = [
        prompt
        for prompt in default_prompts()
        if prompt["text"].startswith(prose_prefixes)
    ]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "\n".join(json.dumps(prompt, ensure_ascii=False) for prompt in prompts) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"count": len(prompts), "out": str(args.out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
