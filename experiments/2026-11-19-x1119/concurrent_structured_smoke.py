#!/usr/bin/env python3
"""Exercise concurrent grammar-constrained output across copy-draft decode rows."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


SCHEMA = {
    "type": "object",
    "properties": {
        "records": {
            "type": "array",
            "minItems": 64,
            "maxItems": 64,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "label": {"type": "string"},
                    "active": {"type": "boolean"},
                },
                "required": ["id", "label", "active"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["records"],
    "additionalProperties": False,
}


def request_once(base: str, api_key: str, wave: int, slot: int, timeout: float) -> dict:
    body = {
        "model": "deepseek-v4.1-flash",
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Return exactly 64 JSON records for wave {wave}, slot {slot}. "
                    "Use ids 0..63, short labels, and active=true. "
                    "Return only the JSON object."
                ),
            }
        ],
        "temperature": 0,
        "max_tokens": 1536,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "records",
                "strict": True,
                "schema": SCHEMA,
            },
        },
    }
    request = urllib.request.Request(
        base.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
            status = response.status
    except urllib.error.HTTPError as error:
        return {
            "wave": wave,
            "slot": slot,
            "status": error.code,
            "elapsed_s": round(time.monotonic() - started, 3),
            "error": error.read().decode(errors="replace")[:2000],
        }
    except Exception as error:
        return {
            "wave": wave,
            "slot": slot,
            "status": None,
            "elapsed_s": round(time.monotonic() - started, 3),
            "error": repr(error),
        }

    content = payload.get("choices", [{}])[0].get("message", {}).get("content")
    parsed = json.loads(content) if isinstance(content, str) else None
    records = parsed.get("records") if isinstance(parsed, dict) else None
    return {
        "wave": wave,
        "slot": slot,
        "status": status,
        "elapsed_s": round(time.monotonic() - started, 3),
        "response_id": payload.get("id"),
        "finish_reason": payload.get("choices", [{}])[0].get("finish_reason"),
        "record_count": len(records) if isinstance(records, list) else None,
        "usage": payload.get("usage"),
        "valid_schema": isinstance(records, list)
        and len(records) == 64
        and all(
            isinstance(record.get("id"), int)
            and isinstance(record.get("label"), str)
            and isinstance(record.get("active"), bool)
            for record in records
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--waves", type=int, default=8)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()

    started = time.monotonic()
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for wave in range(args.waves):
            futures = [
                pool.submit(
                    request_once,
                    args.base,
                    args.api_key,
                    wave,
                    slot,
                    args.timeout,
                )
                for slot in range(args.concurrency)
            ]
            rows.extend(future.result() for future in futures)

    failures = [
        row
        for row in rows
        if row.get("status") != 200 or row.get("valid_schema") is not True
    ]
    result = {
        "schema_version": 1,
        "base": args.base,
        "waves": args.waves,
        "concurrency": args.concurrency,
        "elapsed_s": round(time.monotonic() - started, 3),
        "rows": rows,
        "failures": failures,
        "ok": not failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if failures:
        print(f"concurrent structured output failed: {len(failures)}/{len(rows)}")
        return 1
    print(
        f"concurrent structured output OK: {len(rows)} requests, "
        f"{result['elapsed_s']:.1f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
