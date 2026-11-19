#!/usr/bin/env python3
"""Collect the x1119 live contract with the x1118 collector's verified checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "2026-11-18-x1118" / "collect_live.py"
spec = importlib.util.spec_from_file_location("x1118_collect_live", SOURCE)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot import {SOURCE}")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

collector.IMAGE = "spark-ds41f:x1119"
collector.EXPECTED_IMAGE_LABELS = {
    **collector.EXPECTED_IMAGE_LABELS,
    "local.spark-ds41f.vllm.tree": "259cf7edee8961f40e89b48002ae7394ebca49e5",
}

raise SystemExit(collector.main())
