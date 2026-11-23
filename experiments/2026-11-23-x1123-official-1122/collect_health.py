#!/usr/bin/env python3
"""Collect the final x1123/1122 four-node service and shared-ring health."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "2026-11-23-x1123" / "collect_health.py"
spec = importlib.util.spec_from_file_location("x1123_collect_health", SOURCE)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot import {SOURCE}")
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)

health.EXPECTED_IMAGE_ID = (
    "sha256:5c50e8419ae6a6746ce7f78249ddd7c675c90a11a99f37003c1aba5f32228e33"
)
raise SystemExit(health.main())
