#!/usr/bin/env bash
# Build the benchmark image (network needed: pip, spaCy model, BM25 model are fetched here only).
set -euo pipefail
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")"
docker build -t fmg-bench-mem0 .
