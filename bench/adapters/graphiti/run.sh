#!/usr/bin/env bash
# Run the Graphiti adapter. Needs the image (build.sh), the fmg-bench network with the embedder, and the
# DeepSeek key env file (never printed). The graph lives in the docker volume fmg-bench-graphiti-db and the
# run resumes from it (a re-run does NOT spend money again once all target episodes are ingested).
#   FRESH=1      wipe the volume first (re-ingests everything, spends the budget again)
#   LIMIT_NEW=8  ingest at most 8 new episodes this run (probe)
#   EXTRAS=all|K ingest all 240, or the 127 expect/stale memories plus the first K others in date order
#   ANSWER=1     (default) also run the questions and write bench/results/raw/graphiti.json
#   KEEP_DB=1    keep the volume afterwards (default: removed after a run that wrote the result)
set -euo pipefail
export MSYS_NO_PATHCONV=1
source "$(dirname "${BASH_SOURCE[0]}")/../_env.sh"
REPO=$FMG_REPO
fmg_need_llm_env   # this adapter makes paid API calls
NAME=fmg-bench-graphiti-run
VOL=fmg-bench-graphiti-db
EXTRAS=${EXTRAS:-all}
ANSWER=${ANSWER:-1}
[ "${FRESH:-0}" = 1 ] && docker volume rm -f "$VOL" >/dev/null 2>&1 || true
docker volume create "$VOL" >/dev/null
mkdir -p "$REPO/bench/results/raw"
ARGS=(--extras "$EXTRAS")
[ -n "${LIMIT_NEW:-}" ] && ARGS+=(--limit-new "$LIMIT_NEW")
[ "$ANSWER" = 1 ] && ARGS+=(--answer)
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker create --name "$NAME" --cap-drop ALL --security-opt no-new-privileges:true --memory 4g --cpus 4 \
  --read-only --tmpfs /tmp \
  --env-file "$FMG_LLM_ENV_FILE" \
  -e PYTHONUNBUFFERED=1 ${PRIOR_SPEND_CNY:+-e PRIOR_SPEND_CNY=$PRIOR_SPEND_CNY} ${PRICE_IN:+-e PRICE_IN=$PRICE_IN} ${PRICE_OUT:+-e PRICE_OUT=$PRICE_OUT} \
  -v "$REPO/bench/data:/data:ro" \
  -v "$REPO/bench/results/raw:/out" \
  -v "$VOL:/db" \
  fmg-bench-graphiti "${ARGS[@]}" >/dev/null
docker network connect fmg-bench "$NAME"
docker start -a "$NAME"
docker rm -f "$NAME" >/dev/null
if [ "$ANSWER" = 1 ]; then
  (cd "$REPO" && python bench/score.py)
  [ "${KEEP_DB:-0}" = 1 ] || docker volume rm -f "$VOL" >/dev/null
fi
