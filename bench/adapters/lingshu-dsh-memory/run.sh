#!/usr/bin/env bash
# Build the image (network allowed) and run the adapter with --network none.
# Run from anywhere under Git Bash on Windows. Needs LINGSHU_PKG (see bench/README.md, section "复现").
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../_env.sh"
fmg_need_dir LINGSHU_PKG "unpack the npm package @furongjun1999/dsh-memory version 0.10.1 (the folder that holds its package.json and lib/) and set LINGSHU_PKG to it"
REPO=$FMG_REPO
PKG_SRC=$LINGSHU_PKG   # read-only: it is only copied
CTX="${LINGSHU_CTX:-$FMG_BENCH_HOME/work/lingshu/ctx}"
IMAGE=fmg-bench-lingshu

rm -rf "$CTX" && mkdir -p "$CTX/pkg"
cp -r "$PKG_SRC"/. "$CTX/pkg/"
cp "$REPO/bench/adapters/lingshu-dsh-memory/Dockerfile" "$REPO/bench/adapters/lingshu-dsh-memory/lingshu_bench.mjs" "$CTX/"

MSYS_NO_PATHCONV=1 docker build -t "$IMAGE" "$CTX"

mkdir -p "$REPO/bench/results/raw"
MSYS_NO_PATHCONV=1 docker run --rm --name fmg-bench-lingshu-run \
  --network none --cap-drop ALL --security-opt no-new-privileges:true --memory 4g --cpus 4 \
  --tmpfs /work:rw,exec,uid=1000,gid=1000,size=1g \
  -v "$REPO/bench/data:/bench/data:ro" \
  -v "$REPO/bench/results/raw:/out" \
  "$IMAGE"
