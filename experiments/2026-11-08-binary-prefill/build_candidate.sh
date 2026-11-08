#!/usr/bin/env bash
# Build the thin source overlay on spark-a. This does not rebuild vLLM/NCCL.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAG="${CANDIDATE_IMAGE_TAG:-spark-ds41f:binary-prefill-20261108-01}"
BASE_IMAGE="${CANDIDATE_BASE_IMAGE:-spark-ds41f:1030}"
SOURCE="$ROOT/overlay"
RECEIPTS="$ROOT/receipts"

die() { echo "ERROR: $*" >&2; exit 1; }

[ -d "$SOURCE" ] || die "missing overlay source tree: $SOURCE"
[ -f "$ROOT/Dockerfile" ] || die "missing Dockerfile"
[ -f "$ROOT/smoke_slice_env.py" ] || die "missing smoke script"
docker image inspect "$BASE_IMAGE" >/dev/null 2>&1 \
  || die "base image is absent: $BASE_IMAGE"
if docker image inspect "$TAG" >/dev/null 2>&1; then
  die "candidate tag already exists; choose a new immutable tag: $TAG"
fi

source_sha="$(python3 - "$SOURCE" <<'PY'
import hashlib
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
digest = hashlib.sha256()
for path in sorted(root.rglob("*")):
    if not path.is_file():
        continue
    digest.update(path.relative_to(root).as_posix().encode())
    digest.update(b"\0")
    digest.update(path.read_bytes())
(sys.stdout.write(digest.hexdigest()))
PY
)"
mkdir -p "$RECEIPTS"

docker build \
  --network=host \
  --build-arg "BASE_IMAGE=$BASE_IMAGE" \
  --build-arg "CANDIDATE_SOURCE_SHA256=$source_sha" \
  --tag "$TAG" \
  --file "$ROOT/Dockerfile" \
  "$ROOT"

image_id="$(docker image inspect "$TAG" --format '{{.Id}}')"
docker run --rm --network=none \
  --env EXPECTED_MOE=4096 \
  --env EXPECTED_WO=2048 \
  --env EXPECTED_BLOCK=64 \
  --volume "$ROOT/smoke_slice_env.py:/tmp/smoke_slice_env.py:ro" \
  --entrypoint python3 "$TAG" /tmp/smoke_slice_env.py
docker run --rm --network=none \
  --env SPARK_DS41F_MOE_SLICE_TOKENS=8192 \
  --env SPARK_DS41F_WO_SLICE_TOKENS=4096 \
  --env SPARK_DS41F_PREFILL_BLOCK_M=128 \
  --env EXPECTED_MOE=8192 \
  --env EXPECTED_WO=4096 \
  --env EXPECTED_BLOCK=128 \
  --volume "$ROOT/smoke_slice_env.py:/tmp/smoke_slice_env.py:ro" \
  --entrypoint python3 "$TAG" /tmp/smoke_slice_env.py

python3 - "$RECEIPTS" "$TAG" "$image_id" "$BASE_IMAGE" "$source_sha" <<'PY'
import datetime
import json
import pathlib
import sys

receipts, tag, image_id, base_image, source_sha = sys.argv[1:]
payload = {
    "tag": tag,
    "image_id": image_id,
    "base_image": base_image,
    "source_sha256": source_sha,
    "build_kind": "thin-python-overlay-no-native-rebuild",
    "built_at_utc": datetime.datetime.now(datetime.timezone.utc)
    .strftime("%Y-%m-%dT%H:%M:%SZ"),
}
path = pathlib.Path(receipts) / f"{image_id.removeprefix('sha256:')[:16]}.json"
path.write_text(json.dumps(payload, indent=2) + "\n")
print(path)
print(json.dumps(payload, indent=2))
PY
