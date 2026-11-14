#!/usr/bin/env bash
# What each tier costs: resident memory of the embedder with only that model loaded, query latency, ingest time
# and database size for the 160 development memories (bench/dev). Needs the two embedder containers (setup.sh).
#
#   REPO=/path/to/repo CACHE=/path/to/scratch bash bench/experiments/light_models/costs.sh
#
# For each light model the light container is restarted first, so only that model is ever loaded and nothing is
# left from the one before; then collect.py runs against it (3 warm-up requests, then the timed ingest and the
# timed queries) and `docker stats` is read right after. bge-m3 lives in the older container, which is not
# restarted: its number is what was resident after the model had been loaded and used for a while.
# Prefixes are the kept ones (docs: README.md), so query latency includes the instruction where there is one.
set -euo pipefail
export MSYS_NO_PATHCONV=1
REPO="${REPO:?set REPO to the repository directory}"
CACHE="${CACHE:?set CACHE to a scratch directory}"
LIGHT="${LIGHT:-fmg-bench-embedder-light}"
HEAVY="${HEAVY:-fmg-bench-embedder}"
NETWORK="${NETWORK:-fmg-bench}"
mkdir -p "$CACHE/cost"

run_collect() {  # run_collect <model key> <variant>
  docker run --rm --network "$NETWORK" -v "$REPO:/repo:ro" -v "$CACHE/cost:/cache" -w /repo \
    -e PYTHONPATH=/repo/src -e PYTHONUTF8=1 -e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim \
    python bench/experiments/light_models/collect.py --model-key "$1" --variant "$2" --cache /cache
}

stats() {  # stats <container>
  docker stats --no-stream --format '{{.Name}} memory {{.MemUsage}}' "$1"
}

echo "idle light container (no model loaded):"; stats "$LIGHT"
for pair in bge-small-zh:plain bge-base-zh:prefixed embeddinggemma:plain qwen3-embedding:prefixed; do
  docker restart "$LIGHT" >/dev/null; sleep 6
  echo "== ${pair%%:*}"
  run_collect "${pair%%:*}" "${pair##*:}"
  stats "$LIGHT"
  docker exec "$LIGHT" ollama ps | tail -n +2
done
echo "== bge-m3 (older container, not restarted)"
run_collect bge-m3 plain
stats "$HEAVY"
docker exec "$HEAVY" ollama ps | tail -n +2
