#!/usr/bin/env python3
"""Replay the stock-model API and multimodal regressions for x1123/1122."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "2026-11-18-x1118" / "run_api_smoke.py"
spec = importlib.util.spec_from_file_location("x1118_api_smoke", SOURCE)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot import {SOURCE}")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

raise SystemExit(smoke.main())
