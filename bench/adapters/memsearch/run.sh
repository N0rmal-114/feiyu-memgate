#!/usr/bin/env bash
# Run the memsearch adapter on the embedder network (no internet) and score. Image must be built (build.sh).
set -euo pipefail
export MSYS_NO_PATHCONV=1
source "$(dirname "${BASH_SOURCE[0]}")/../_env.sh"
REPO=$FMG_REPO

mkdir -p "$REPO/bench/results/raw"
docker run --rm --name fmg-bench-memsearch-run --network fmg-bench --cap-drop ALL \
  --security-opt no-new-privileges:true --memory 4g --cpus 4 --read-only --tmpfs /tmp:exec,size=512m \
  -v "$REPO/bench/data:/data:ro" \
  -v "$REPO/bench/results/raw:/out" \
  fmg-bench-memsearch --data /data --out /out/memsearch.json

(cd "$REPO" && python bench/score.py)
