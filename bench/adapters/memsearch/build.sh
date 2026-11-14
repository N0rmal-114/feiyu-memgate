#!/usr/bin/env bash
# Build the image from a copy of the memsearch checkout (read-only source, copied to scratch).
# Needs MEMSEARCH_SRC (see bench/README.md, section "复现").
set -euo pipefail
export MSYS_NO_PATHCONV=1

source "$(dirname "${BASH_SOURCE[0]}")/../_env.sh"
fmg_need_dir MEMSEARCH_SRC "check out github.com/zilliztech/memsearch at commit 369ae1da48b7 (version 0.4.23) and set MEMSEARCH_SRC to its root folder"
WORK=$FMG_BENCH_HOME/work/memsearch
SRC=$MEMSEARCH_SRC
ADAPTER=$FMG_REPO/bench/adapters/memsearch

rm -rf "$WORK/ctx" && mkdir -p "$WORK/ctx/src"
cp -r "$SRC/src" "$SRC/pyproject.toml" "$SRC/README.md" "$SRC/LICENSE" "$WORK/ctx/src/"
find "$WORK/ctx" -name __pycache__ -type d -prune -exec rm -rf {} +
cp "$ADAPTER/Dockerfile" "$ADAPTER/run_memsearch.py" "$WORK/ctx/"
docker build -t fmg-bench-memsearch "$WORK/ctx"
