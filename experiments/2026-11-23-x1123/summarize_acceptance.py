#!/usr/bin/env python3
"""Summarize two x1123 bench runs against the official README workbooks."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "2026-11-18-x1118"
    / "summarize_acceptance.py"
)
spec = importlib.util.spec_from_file_location("x1118_summarize_acceptance", SOURCE)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot import {SOURCE}")
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)

# The current Sonar bench names the reasoning-off cases `prose-c*` and
# `code-c*`; `reasoning_share` in each point is 0. The historical workbook
# called the same measurements `prose-nothink-*` and `code-nothink-*`.
summary.DECODE_GATES = {
    "prose-c1": 59.1,
    "prose-c2": 84.8,
    "prose-c4": 124.9,
    "code-c1": 80.6,
    "code-c2": 118.2,
    "code-c4": 165.1,
}

raise SystemExit(summary.main())
