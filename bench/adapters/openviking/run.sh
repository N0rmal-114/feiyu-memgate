#!/usr/bin/env bash
# From scratch: start a throw-away OpenViking, run the adapters (the direct one, feiyu-memgate with the gateway's
# defaults, feiyu-memgate with --profile precise) and the capability check of feiyu-memgate's openviking
# backend, tear it down.
#   run.sh        the held-out benchmark (bench/data)
#   run.sh dev    only the development-set candidate lists (bench/dev) for bench/dev_eval.py; nothing is written
#                 into bench/results/raw
# Needs: the OpenViking image by digest (already local), python:3.12-slim, the internal network fmg-bench
# with the embedder container fmg-bench-embedder. Nothing is published on the host.
set -euo pipefail
export MSYS_NO_PATHCONV=1
# Paths come from this script's place; docker on Windows needs the drive-letter form (pwd -W).
source "$(dirname "${BASH_SOURCE[0]}")/../_env.sh"
REPO=$FMG_REPO
# Scratch space (config with the generated key, key file, raw outputs) lives next to the repository, not in it.
WORK="${WORK:-$FMG_BENCH_HOME/work/openviking}"
IMAGE="ghcr.io/volcengine/openviking@sha256:57d3fd8873dbb78e5ccfdc57481006e6fbe765390ea18cd4a6b2f62da8aa104d"
OV=fmg-bench-openviking
VOL=fmg-bench-openviking-data

teardown() {
  docker rm -f "$OV" >/dev/null 2>&1 || true
  docker volume rm "$VOL" >/dev/null 2>&1 || true
}
trap teardown EXIT
teardown

# A fresh random root key per run, kept outside the repository; the config is rendered from the template.
mkdir -p "$WORK/run" "$WORK/out"
rm -f "$WORK/run/keys.json" "$WORK/out/"*.json
KEY="ovbench-$(python -c 'import secrets;print(secrets.token_hex(16))' 2>/dev/null || date +%s%N)"
sed "s/__ROOT_KEY__/$KEY/" "$REPO/bench/adapters/openviking/ov.conf.template" > "$WORK/ov.conf"

docker volume create "$VOL" >/dev/null
docker run -d --name "$OV" --network fmg-bench \
  --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 512 --memory 6g \
  -e OV_ROOT_API_KEY="$KEY" -e OPENVIKING_WITH_BOT=0 -e OPENVIKING_WEB_STUDIO_DIR=/nonexistent \
  -e LITELLM_LOCAL_MODEL_COST_MAP=True \
  -e NO_PROXY=fmg-bench-embedder,localhost,127.0.0.1 -e no_proxy=fmg-bench-embedder,localhost,127.0.0.1 \
  -v "$VOL:/app/.openviking/data" -v "$WORK/ov.conf:/app/.openviking/ov.conf:ro" \
  "$IMAGE" >/dev/null

# Adapters run in a container on the same internal network: repository read-only, key/result dirs writable.
runpy() {
  docker run --rm --network fmg-bench --cap-drop ALL --security-opt no-new-privileges:true \
    --memory 2g --cpus 2 --read-only --tmpfs /tmp:rw,size=64m \
    -e PYTHONPATH=/repo/src -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONUTF8=1 "$@"
}
RUN_ARGS=(-v "$REPO:/repo:ro" -v "$WORK/run:/work" -v "$WORK/out:/out")
A=/repo/bench/adapters/openviking

runpy -e OV_ROOT_API_KEY="$KEY" "${RUN_ARGS[@]}" python:3.12-slim python $A/setup.py
if [ "${1:-}" = "dev" ]; then
  runpy "${RUN_ARGS[@]}" python:3.12-slim python $A/dev_candidates.py --out /out
  cp "$WORK/out/openviking-dev-candidates.json" "$WORK/"
  echo "done; candidate lists: $WORK/openviking-dev-candidates.json (python bench/dev_eval.py --backend openviking --candidates <that file> --search)"
  exit 0
fi
runpy "${RUN_ARGS[@]}" python:3.12-slim python $A/openviking_direct.py --out /out
runpy "${RUN_ARGS[@]}" python:3.12-slim python $A/feiyu_memgate_openviking.py --out /out
runpy "${RUN_ARGS[@]}" python:3.12-slim python $A/feiyu_memgate_openviking.py --out /out --profile precise
runpy "${RUN_ARGS[@]}" python:3.12-slim python $A/capabilities.py --out /out

mkdir -p "$REPO/bench/results/raw"
cp "$WORK/out/openviking.json" "$WORK/out/openviking-unfiltered.json" "$WORK/out/feiyu-memgate-openviking.json" \
   "$WORK/out/feiyu-memgate-openviking-precise.json" "$REPO/bench/results/raw/"
cp "$WORK/out/capabilities.json" "$WORK/"
echo "done; capability report of the openviking backend: $WORK/capabilities.json"
