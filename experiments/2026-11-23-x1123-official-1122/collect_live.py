#!/usr/bin/env python3
"""Collect and verify the final x1123/1122 live contract on four Sparks."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "2026-11-18-x1118" / "collect_live.py"
spec = importlib.util.spec_from_file_location("x1118_collect_live", SOURCE)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot import {SOURCE}")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

collector.IMAGE = "spark-ds41f:x1123"
collector.EXPECTED_ENV_COUNT = 91
collector.EXPECTED_IMAGE_LABELS = {
    **collector.EXPECTED_IMAGE_LABELS,
    "local.spark-ds41f.vllm.tree": "8e0683a9276389957e49b7a26bd2bd062d788558",
}
collector.EXPECTED_ARGS = {
    "--max-model-len": "1000000",
    "--max-num-seqs": "8",
    "--max-num-batched-tokens": "16384",
    "--kv-cache-memory-bytes": "8076928000",
    "--long-prefill-token-threshold": "16336",
    "--max-cudagraph-capture-size": "48",
    "--limit-mm-per-prompt": '{"image":67}',
    "--master-port": "25000",
    "--enable-per-request-metrics": 1,
    "num_speculative_tokens": 5,
    "max_new_tokens": 65536,
}
collector.EXPECTED_STARTUP_HEAD = {
    "max_num_seqs": "8",
    "max_model_len": "1000000",
    "max_num_batched_tokens": "16384",
    "max_new_tokens": "65536",
    "master_port": "25000",
    "num_spec_tokens": "5",
}
collector.EXPECTED_STARTUP_WORKER = {"kv_cache_gib": "7.52"}

raise SystemExit(collector.main())
