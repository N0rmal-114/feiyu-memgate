#!/usr/bin/env bash
# The final held-out run: every adapter ONE AT A TIME (so the timings are clean), then the scorer, the
# forgetting simulation (both backends) and the chart generator.
#
#     bash bench/run_all.sh                 # everything below, in this order
#     RENDER=1 bash bench/run_all.sh        # also render the charts to PNG at the end (about 20 minutes)
#     SKIP="lingshu openviking" bash bench/run_all.sh     # leave steps out (names as in the step list)
#
# Needs: Docker, Git Bash, python on the host, the internal network `fmg-bench` with the long-lived
# embedder container `fmg-bench-embedder` (bge-m3) AND the second one, `fmg-bench-embedder-light` (the four
# lighter models of the deployment tiers; make it with `bash bench/experiments/light_models/setup.sh`), and the
# images the adapters use (build.sh of each adapter; this
# script builds nothing itself). Three scripts (meow-memory, seekmemory, lingshu) assemble their build
# context from the unpacked third-party source and call `docker build` on it, which is a cache hit when the
# image is current; their source variables must be set (see bench/README.md, section "复现").
#
# Order, with the approximate time of each step on the reference machine:
#   sqlite, sqlite-broad, embedding, embedding-broad, hybrid, full (feiyu-memgate; the two "-broad" rows are the
#   same backends with `[recall] profile = "broad"`), openviking pair (its run.sh runs `openviking` and
#   `feiyu-memgate-openviking`), the four lighter embedding models (bge-small-zh, bge-base-zh, embeddinggemma,
#   qwen3-embedding, each with its preset, through the light embedder), meow-memory, seekmemory (three result files), dsh-mnemon (both modes),
#   memsearch, mem0 with MODE=verbatim, lingshu-dsh-memory (about 30 minutes: it paces its writes to respect
#   that plugin's own rate limit, so it runs last and alone), then score.py, forgetting_sim.py, charts.py.
#
# NOT re-run, on purpose (re-ingesting costs money): `mem0` with LLM extraction (bench/results/raw/mem0.json) and
# `graphiti` (graphiti.json) keep their existing result files; the scorer says their timings come from a
# loaded machine. No paid API call is made by this script.
#
# A failing step stops the run (set -e): fix the adapter invocation and run again with SKIP naming the steps
# that already finished.
set -euo pipefail
export MSYS_NO_PATHCONV=1

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${FMG_REPO:-$(cd "$HERE/.." && (cygpath -m "$(pwd)" 2>/dev/null || pwd))}"
cd "$HERE/.."
SKIP=" ${SKIP:-} "
EMBED_NET=fmg-bench
EMBED_URL="http://fmg-bench-embedder:11434/v1"
LIGHT_URL="http://fmg-bench-embedder-light:11434/v1"   # the light embedder: bench/experiments/light_models/setup.sh

step() {  # step <name> <command...>: run unless named in SKIP; print start and wall-clock time
  local name=$1; shift
  case "$SKIP" in *" $name "*) echo "== skip $name"; return 0 ;; esac
  echo "== $name: start $(date '+%F %T')"
  local t0=$SECONDS
  "$@"
  echo "== $name: done in $((SECONDS - t0)) s"
}

# python:3.12-slim on the embedder network: repository read-only, only the raw results writable.
in_docker() {  # in_docker <python args...>
  docker run --rm --network "$EMBED_NET" --cap-drop ALL --security-opt no-new-privileges:true \
    --memory 4g --cpus 4 \
    -v "$REPO:/repo:ro" -v "$REPO/bench/results/raw:/repo/bench/results/raw" \
    -w /repo -e PYTHONPATH=/repo/src -e PYTHONUTF8=1 -e PYTHONDONTWRITEBYTECODE=1 \
    python:3.12-slim python "$@"
}

fmg_sqlite()    { PYTHONUTF8=1 python bench/adapters/feiyu_memgate_sqlite.py; }
fmg_sqlite_broad() { PYTHONUTF8=1 python bench/adapters/feiyu_memgate_sqlite.py --profile broad; }
fmg_embedding() { in_docker bench/adapters/feiyu_memgate_embedding.py --embed-url "$EMBED_URL"; }
fmg_embedding_broad() {
  in_docker bench/adapters/feiyu_memgate_embedding.py --embed-url "$EMBED_URL" --profile broad --system-suffix broad
}
light() {  # light <model name at the endpoint> <suffix>
  in_docker bench/adapters/feiyu_memgate_embedding.py --embed-url "$LIGHT_URL" --model "$1" --system-suffix "$2"
}
light_small()   { light hf.co/CompendiumLabs/bge-small-zh-v1.5-gguf:F16 bge-small-zh; }
light_base()    { light hf.co/CompendiumLabs/bge-base-zh-v1.5-gguf:Q8_0 bge-base-zh; }
light_gemma()   { light embeddinggemma embeddinggemma; }
light_qwen()    { light qwen3-embedding:0.6b qwen3-embedding; }
fmg_hybrid()    { in_docker bench/adapters/feiyu_memgate_hybrid.py --embed-url "$EMBED_URL"; }
fmg_full()      { in_docker bench/adapters/feiyu_memgate_full.py --embed-url "$EMBED_URL"; }
openviking()    { bash bench/adapters/openviking/run.sh; }
meow()          { bash bench/adapters/meow-memory/run.sh; }
seekmemory()    { bash bench/adapters/seekmemory/run.sh; }
mnemon()        { bash bench/adapters/dsh-mnemon/run.sh; }
memsearch()     { bash bench/adapters/memsearch/run.sh; }
mem0_verbatim() { MODE=verbatim bash bench/adapters/mem0/run.sh; }
lingshu()       { bash bench/adapters/lingshu-dsh-memory/run.sh; }

step sqlite     fmg_sqlite
step sqlite-broad fmg_sqlite_broad
step embedding  fmg_embedding
step embedding-broad fmg_embedding_broad
step hybrid     fmg_hybrid
step full       fmg_full
step openviking openviking
step bge-small-zh light_small
step bge-base-zh  light_base
step embeddinggemma light_gemma
step qwen3-embedding light_qwen
step meow       meow
step seekmemory seekmemory
step mnemon     mnemon
step memsearch  memsearch
step mem0       mem0_verbatim
step lingshu    lingshu

score()        { PYTHONUTF8=1 python bench/score.py; }
forget_sqlite() { PYTHONUTF8=1 python bench/forgetting_sim.py --backend sqlite --out bench/results/forgetting; }
forget_hybrid() {
  docker run --rm --network "$EMBED_NET" --cap-drop ALL --security-opt no-new-privileges:true --memory 4g --cpus 4 \
    -v "$REPO:/repo:ro" -v "$REPO/bench/results/forgetting:/repo/bench/results/forgetting" \
    -w /repo -e PYTHONPATH=/repo/src -e PYTHONUTF8=1 -e PYTHONDONTWRITEBYTECODE=1 \
    python:3.12-slim python bench/forgetting_sim.py --backend hybrid --embed-url "$EMBED_URL" \
    --out bench/results/forgetting
}
charts()       { PYTHONUTF8=1 python bench/charts.py; }
render()       { bash bench/render_charts.sh; }

step score       score
step forget-sqlite forget_sqlite
step forget-hybrid forget_hybrid
step charts      charts
[ "${RENDER:-0}" = 1 ] && step render render
echo "== all done $(date '+%F %T')"
