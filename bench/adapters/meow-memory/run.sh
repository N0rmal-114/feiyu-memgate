#!/usr/bin/env bash
# Build the image from an assembled context, run the benchmark offline, copy the result into the repo.
set -euo pipefail
export MSYS_NO_PATHCONV=1
source "$(dirname "${BASH_SOURCE[0]}")/../_env.sh"
fmg_need_dir MEOW_MEMORY_PKG "unpack the npm package meow-memory version 0.32.6 (the folder that holds its package.json and lib/) and set MEOW_MEMORY_PKG to it"
REPO=$FMG_REPO
WORK="$FMG_BENCH_HOME/work/meow-memory"
SRC_PKG=$MEOW_MEMORY_PKG   # read-only: it is only copied
ADAPTER="$REPO/bench/adapters/meow-memory"

rm -rf "$WORK/ctx" "$WORK/out"
mkdir -p "$WORK/ctx" "$WORK/out"
cp -r "$SRC_PKG" "$WORK/ctx/package"
cp -r "$ADAPTER/stubs" "$WORK/ctx/stubs"
cp "$ADAPTER/bench.mjs" "$ADAPTER/Dockerfile" "$WORK/ctx/"

docker build -t fmg-bench-meow-memory "$WORK/ctx"

# Offline run; data read-only; root fs read-only with a tmpfs for the SQLite workspace.
docker run --rm --name fmg-bench-meow-memory-run --network none \
  --cap-drop ALL --security-opt no-new-privileges:true --memory 4g --cpus 4 \
  --read-only --tmpfs /tmp:rw,size=256m \
  -v "$REPO/bench/data:/data:ro" -v "$WORK/out:/out" \
  fmg-bench-meow-memory

cp "$WORK/out/meow-memory.json" "$REPO/bench/results/raw/meow-memory.json"
