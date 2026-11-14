#!/usr/bin/env bash
# Optional, costs a few thousandths of a CNY: one real session commit (LLM extraction) to check receipt_refs().
# The key file is passed with --env-file and ends up only in the server container, never in a host file.
# Usage: FMG_LLM_ENV_FILE=/path/to/file-defining-DEEPSEEK_API_KEY bash receipt_check.sh   (see bench/adapters/_env.sh)
set -euo pipefail
export MSYS_NO_PATHCONV=1
source "$(dirname "${BASH_SOURCE[0]}")/../_env.sh"
fmg_need_llm_env
REPO=$FMG_REPO
WORK="${WORK:-$FMG_BENCH_HOME/work/openviking-receipt}"
IMAGE="ghcr.io/volcengine/openviking@sha256:57d3fd8873dbb78e5ccfdc57481006e6fbe765390ea18cd4a6b2f62da8aa104d"
OV=fmg-bench-openviking-llm
VOL=fmg-bench-openviking-llm-data
teardown() { docker rm -f "$OV" >/dev/null 2>&1 || true; docker volume rm "$VOL" >/dev/null 2>&1 || true; }
trap teardown EXIT
teardown
mkdir -p "$WORK"
python -c 'import secrets;print("ovbench-"+secrets.token_hex(16))' > "$WORK/root.key"
docker volume create "$VOL" >/dev/null
# Created on the default bridge (the LLM needs the internet), then also joined to fmg-bench (the embedder).
docker create --name "$OV" --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 512 --memory 6g \
  --env-file "$FMG_LLM_ENV_FILE" -e OV_ROOT_API_KEY="$(cat "$WORK/root.key")" -e OPENVIKING_WITH_BOT=0 \
  -e OPENVIKING_WEB_STUDIO_DIR=/nonexistent -e LITELLM_LOCAL_MODEL_COST_MAP=True \
  -e NO_PROXY=fmg-bench-embedder,localhost,127.0.0.1 -e no_proxy=fmg-bench-embedder,localhost,127.0.0.1 \
  -v "$VOL:/app/.openviking/data" -v "$REPO/bench/adapters/openviking/ov.conf.template:/t/ov.conf.template:ro" \
  -v "$REPO/bench/adapters/openviking/genconf_llm.py:/g/genconf_llm.py:ro" --entrypoint sh "$IMAGE" \
  -c 'python3 /g/genconf_llm.py; exec openviking-entrypoint' >/dev/null
docker network connect fmg-bench "$OV"
docker start "$OV" >/dev/null
docker run --rm --network fmg-bench --cap-drop ALL --security-opt no-new-privileges:true --memory 2g --read-only \
  --tmpfs /tmp:rw,size=64m -e OV_URL=http://$OV:1933 -e PYTHONPATH=/repo/src -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONUTF8=1 \
  -v "$REPO:/repo:ro" -v "$WORK/root.key:/root.key:ro" python:3.12-slim python /repo/bench/adapters/openviking/receipt_check.py
