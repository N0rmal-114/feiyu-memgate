#!/usr/bin/env bash
# Build the image from a copy of the SeekMemory clone and run the three modes with no network.
# Run from Git Bash on Windows. Needs SEEKMEMORY_SRC (see bench/README.md, section "复现").
set -euo pipefail
export MSYS_NO_PATHCONV=1

source "$(dirname "${BASH_SOURCE[0]}")/../_env.sh"
fmg_need_dir SEEKMEMORY_SRC "check out github.com/NeverENG/SeekMemory at commit 794ec5d and set SEEKMEMORY_SRC to its root folder"
REPO=$FMG_REPO
WORK=$FMG_BENCH_HOME/work/seekmemory
VENDOR=$SEEKMEMORY_SRC
ADAPTER=$REPO/bench/adapters/seekmemory
IMG=fmg-bench-seekmemory

rm -rf "$WORK/ctx" && mkdir -p "$WORK/ctx/seekmemory"
for d in adapters app core maintain perceive recall services store; do cp -r "$VENDOR/$d" "$WORK/ctx/seekmemory/$d"; done
find "$WORK/ctx" -name __pycache__ -type d -prune -exec rm -rf {} +
cp "$ADAPTER/Dockerfile" "$ADAPTER/run_seekmemory.py" "$WORK/ctx/"
docker build -t "$IMG" "$WORK/ctx"

mkdir -p "$REPO/bench/results/raw"
run() {  # run <container suffix> <args...>
  local n=$1; shift
  docker run --rm --name "fmg-bench-seekmemory-$n" --network none --cap-drop ALL \
    --security-opt no-new-privileges:true --memory 4g --cpus 4 --read-only --tmpfs /tmp \
    -v "$REPO/bench/data:/data:ro" \
    -v "$REPO/bench/results/raw:/out" \
    "$IMG" --data /data "$@"
}
run main  --out /out/seekmemory.json
run aged  --out /out/seekmemory-aged.json --name seekmemory-aged --aged-days 365
run aged2 --out /out/seekmemory-aged-detail.json --name seekmemory-aged-detail --aged-days 365 --level detail

(cd "$REPO" && python bench/score.py)
