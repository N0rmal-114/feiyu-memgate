#!/usr/bin/env bash
# Build the image (internet needed for pip). Pins graphiti-core 0.31.2 with the Kuzu extra.
set -euo pipefail
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")"
docker build -t fmg-bench-graphiti .
