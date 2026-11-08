#!/usr/bin/env python3
"""Run official servebench with the site's bearer token.

Set SERVEBENCH_ROOT to the official checkout. Prompt generation, HTTP payloads,
timing, summarization and comparison remain the unmodified servebench code.
"""

from __future__ import annotations

import os
import sys
import urllib.request


root = os.environ.get("SERVEBENCH_ROOT", "")
if root:
    sys.path.insert(0, root)

token = os.environ.get("SPARK_API_KEY", "deepseek")
original_init = urllib.request.Request.__init__


def authorized_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
    original_init(self, *args, **kwargs)
    if not self.has_header("Authorization"):
        self.add_header("Authorization", f"Bearer {token}")


urllib.request.Request.__init__ = authorized_init  # type: ignore[method-assign]

from servebench.__main__ import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
