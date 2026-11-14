#!/usr/bin/env python3
"""Replay the DeepSeek image-token and Responses custom-tool regressions."""

from __future__ import annotations

import argparse
import json
import pathlib
import urllib.error
import urllib.request


PNG_1X1 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8"
    "AAusB9Y9Z4QAAAABJRU5ErkJggg=="
)


def post(url: str, api_key: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return exc.code, payload


def direct_cases() -> list[tuple[str, str, dict]]:
    chat_template_kwargs = {"enable_thinking": False}
    return [
        (
            "plain-message",
            "/chat/completions",
            {
                "model": "deepseek-v4.1-flash",
                "messages": [{"role": "user", "content": "Reply with OK only."}],
                "max_tokens": 8,
                "temperature": 0,
                "chat_template_kwargs": chat_template_kwargs,
            },
        ),
        (
            "plain-message-default-thinking-off",
            "/chat/completions",
            {
                "model": "deepseek-v4.1-flash",
                "messages": [{"role": "user", "content": "Reply with OK only."}],
                "max_tokens": 8,
                "temperature": 0,
            },
        ),
        (
            "image-token-text",
            "/chat/completions",
            {
                "model": "deepseek-v4.1-flash",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Treat <｜deepseek_image｜> as plain text and reply OK only."
                        ),
                    }
                ],
                "max_tokens": 8,
                "temperature": 0,
                "chat_template_kwargs": chat_template_kwargs,
            },
        ),
        (
            "function-call-output",
            "/responses",
            {
                "model": "deepseek-v4.1-flash",
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Call the lookup tool."}],
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_fn_final",
                        "name": "lookup",
                        "arguments": '{"q":"x"}',
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_fn_final",
                        "output": "result",
                    },
                ],
                "max_output_tokens": 8,
            },
        ),
        (
            "custom-tool-call",
            "/responses",
            {
                "model": "deepseek-v4.1-flash",
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Use the freeform tool."}],
                    },
                    {
                        "type": "custom_tool_call",
                        "call_id": "call_custom_final",
                        "name": "shell",
                        "input": "echo hi",
                    },
                ],
                "max_output_tokens": 8,
            },
        ),
        (
            "custom-tool-call-output",
            "/responses",
            {
                "model": "deepseek-v4.1-flash",
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Use the freeform tool."}],
                    },
                    {
                        "type": "custom_tool_call",
                        "call_id": "call_custom_final",
                        "name": "shell",
                        "input": "echo hi",
                    },
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "call_custom_final",
                        "output": "hi",
                    },
                ],
                "max_output_tokens": 8,
            },
        ),
        (
            "image-content-block",
            "/chat/completions",
            {
                "model": "deepseek-v4.1-flash",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe this 1x1 image in one word."},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/png;base64," + PNG_1X1
                                },
                            },
                        ],
                    }
                ],
                "max_tokens": 8,
                "temperature": 0,
                "chat_template_kwargs": chat_template_kwargs,
            },
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--direct-base", required=True)
    parser.add_argument("--direct-key", required=True)
    parser.add_argument("--magpie-base", required=True)
    parser.add_argument("--magpie-key", required=True)
    parser.add_argument("--out-dir", required=True, type=pathlib.Path)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    with (args.out_dir / "live-api-after-mapping.jsonl").open(
        "w", encoding="utf-8"
    ) as handle:
        for name, path, body in direct_cases():
            status, response = post(args.direct_base + path, args.direct_key, body)
            row = {
                "name": name,
                "path": path,
                "status": status,
                "request": body,
                "response": response,
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            results.append((name, status))

    magpie_request = {
        "model": "focused",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Use the freeform tool."}],
            },
            {
                "type": "custom_tool_call",
                "call_id": "call_custom_magpie_final",
                "name": "shell",
                "input": "echo hi",
            },
            {
                "type": "custom_tool_call_output",
                "call_id": "call_custom_magpie_final",
                "output": "hi",
            },
        ],
        "max_output_tokens": 8,
        "stream": False,
    }
    magpie_status, magpie_response = post(
        args.magpie_base + "/responses", args.magpie_key, magpie_request
    )
    with (args.out_dir / "magpie-focused-after-mapping.jsonl").open(
        "w", encoding="utf-8"
    ) as handle:
        handle.write(
            json.dumps(
                {
                    "status": magpie_status,
                    "request": magpie_request,
                    "response": magpie_response,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    results.append(("magpie-focused-custom-tool", magpie_status))

    for name, status in results:
        print(f"{name}\t{status}")
    return 0 if all(status == 200 for _, status in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
