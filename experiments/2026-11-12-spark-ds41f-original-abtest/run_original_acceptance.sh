#!/usr/bin/env bash
set -euo pipefail

REPO=/opt/abtest/spark-ds41f
URL=${SERVEBENCH_URL:-http://127.0.0.1:8000}
OUT=/opt/abtest-results/2026-11-12-original
SUITE=/opt/abtest-inputs/acceptance-c1-c2-c4-prose-daily.json

cd "$REPO"
export PYTHONPATH="$REPO"
mkdir -p "$OUT"

for run in 01 02; do
  python3 -m servebench suite "$URL" \
    --suite "$SUITE" \
    --out "$OUT/c1c2c4-prose-run-$run" \
    --label "original-c1c2c4-run-$run" \
    2>&1 | tee "$OUT/c1c2c4-prose-run-$run.log"
done

python3 -m servebench prefill "$URL" \
  --sizes 32768,131072,262144,524288,1000000 \
  --repeats 3 \
  --extra-body '{"chat_template_kwargs":{"thinking":false}}' \
  --label original-prefill \
  --output "$OUT/prefill.json" \
  2>&1 | tee "$OUT/prefill.log"

python3 - "$OUT" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
rows = {}
for run in ("01", "02"):
    for concurrency in ("c1", "c2", "c4"):
        result = json.loads((root / f"c1c2c4-prose-run-{run}" / f"{concurrency}.json").read_text())
        rows.setdefault(concurrency, []).append(result["summary"]["output_tps"]["mean"])

summary = {
    "decode_tps": {
        concurrency: {
            "runs": values,
            "mean": sum(values) / len(values),
        }
        for concurrency, values in rows.items()
    },
    "prefill": json.loads((root / "prefill.json").read_text())["prefill"],
}
(root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary, indent=2))
PY
