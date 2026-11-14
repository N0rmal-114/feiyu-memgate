#!/usr/bin/env bash
# Reproduce the mem0 result files. Assumes the image exists (bash build.sh).
#   MODE=verbatim bash run.sh   -> bench/results/raw/mem0-verbatim.json  (no LLM, network fmg-bench only)
#   MODE=infer    bash run.sh   -> bench/results/raw/mem0.json           (DeepSeek, budgeted, paid)
#   MODE=both (default) runs both.  Extra env for infer: LIMIT=N (dry run: LIMIT=10 DRY=1), BUDGET_CNY (default 3),
#   RATE_IN / RATE_IN_CACHED / RATE_OUT (CNY per 1M tokens, used for the budget stop).
set -euo pipefail
export MSYS_NO_PATHCONV=1
MODE="${MODE:-both}"
source "$(dirname "${BASH_SOURCE[0]}")/../_env.sh"
REPO=$FMG_REPO
IMG=fmg-bench-mem0
ENVFILE=$FMG_LLM_ENV_FILE   # only the paid infer mode reads it
SAFE=(--cap-drop ALL --security-opt no-new-privileges:true --memory 4g --cpus 4 --read-only --tmpfs /tmp:exec,size=2g)
MOUNTS=(-v "$REPO/bench/data:/data:ro" -v "$REPO/bench/results/raw:/out")

verbatim() {
  docker run --rm --name fmg-bench-mem0-verbatim --network fmg-bench "${SAFE[@]}" "${MOUNTS[@]}" \
    "$IMG" --mode verbatim --out /out/mem0-verbatim.json
}

infer() {  # needs the internet (LLM) and the embedder: create on the bridge, then also join fmg-bench
  fmg_need_llm_env
  local extra=(--budget-cny "${BUDGET_CNY:-3}")
  [ -n "${LIMIT:-}" ] && extra+=(--limit "$LIMIT")
  [ -n "${DRY:-}" ] && extra+=(--dry)
  [ -n "${RATE_IN:-}" ] && extra+=(--rate-in "$RATE_IN" --rate-out "$RATE_OUT" --rate-in-cached "${RATE_IN_CACHED:-$RATE_IN}")
  docker rm -f fmg-bench-mem0-infer >/dev/null 2>&1 || true
  docker create --name fmg-bench-mem0-infer --env-file "$ENVFILE" "${SAFE[@]}" "${MOUNTS[@]}" \
    "$IMG" --mode infer --out /out/mem0.json "${extra[@]}" >/dev/null
  docker network connect fmg-bench fmg-bench-mem0-infer
  docker start -a fmg-bench-mem0-infer
  docker rm -f fmg-bench-mem0-infer >/dev/null
}

case "$MODE" in
  verbatim) verbatim ;;
  infer) infer ;;
  both) verbatim; infer ;;
  *) echo "MODE must be verbatim, infer or both" >&2; exit 2 ;;
esac
(cd "$REPO" && python bench/score.py)
