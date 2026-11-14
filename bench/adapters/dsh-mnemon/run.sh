#!/usr/bin/env bash
# Run both modes from the already-built image (build with build.sh) and score.
#   default   : --network none           -> bench/results/raw/dsh-mnemon.json
#   embedding : --network fmg-bench      -> bench/results/raw/dsh-mnemon-embedding.json (shared bge-m3 embedder)
set -euo pipefail
export MSYS_NO_PATHCONV=1

source "$(dirname "${BASH_SOURCE[0]}")/../_env.sh"
REPO=$FMG_REPO
IMG=fmg-bench-mnemon
mkdir -p "$REPO/bench/results/raw"

run() {  # run <suffix> <network> <mode> <out file name>
  docker run --rm --name "fmg-bench-mnemon-$1" --network "$2" --cap-drop ALL \
    --security-opt no-new-privileges:true --memory 4g --cpus 4 --read-only --tmpfs /tmp:rw,size=512m \
    -e BENCH_MODE="$3" -e BENCH_OUT="/out/$4" \
    -v "$REPO/bench/data:/data:ro" \
    -v "$REPO/bench/results/raw:/out" \
    "$IMG"
}
run default none default dsh-mnemon.json
run embedding fmg-bench embedding dsh-mnemon-embedding.json

(cd "$REPO" && python bench/score.py)
