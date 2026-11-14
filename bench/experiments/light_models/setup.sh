#!/usr/bin/env bash
# Sets up the light embedder used by the light-model experiment and by the held-out run.
#
#   bash bench/experiments/light_models/setup.sh [pull|serve|check|all]      (default: all)
#
# What it makes: a docker volume with four embedding models and a container that serves them on the
# internal network `fmg-bench` (no internet) at http://fmg-bench-embedder-light:11434/v1, CPU only, one model
# loaded at a time. The first step needs the internet and uses a throw-away container on the default bridge
# network; the serving container never has it. It does not touch any other container, network or volume
# (bge-m3 stays in the older `fmg-bench-embedder`, which is the top tier of the experiment).
#
# Needs: docker, and the network `fmg-bench` (created for the benchmark: `docker network create --internal fmg-bench`).
set -euo pipefail

# On Git Bash for Windows the shell would rewrite /models/... into a Windows path inside -e values.
export MSYS_NO_PATHCONV=1

# Same image as the existing embedder (its tag is `ollama/ollama`; pin the digest of the one you use:
# `docker inspect fmg-bench-embedder --format '{{.Config.Image}}'`).
IMAGE="${IMAGE:-$(docker inspect fmg-bench-embedder --format '{{.Config.Image}}')}"
VOLUME="${VOLUME:-fmg-bench-models-light}"
CONTAINER="${CONTAINER:-fmg-bench-embedder-light}"
NETWORK="${NETWORK:-fmg-bench}"
MEM_LIMIT="${MEM_LIMIT:-5g}"
CPUS="${CPUS:-6}"

# Models. Ollama pulls a GGUF repository on Hugging Face as hf.co/<owner>/<repo>:<quantisation>.
#  - bge-small-zh-v1.5: GGUF conversion by CompendiumLabs of the BAAI model, F16 (47.9 MB), 512 dims.
#  - bge-base-zh-v1.5: same publisher, Q8_0 (110 MB), 768 dims. The F16 file of this repository (204 MB) is
#    rejected by this Ollama build ("failed to read key-value pairs": the file lists bos/eos token ids twice;
#    Q8_0 and Q4_K_M do not), so the highest-precision file that loads is used.
#  - embeddinggemma: Ollama library, 768 dims, 621 MB (bf16).
#  - qwen3-embedding:0.6b: Ollama library, 1024 dims, 639 MB (Q8_0).
MODELS=(
  "hf.co/CompendiumLabs/bge-small-zh-v1.5-gguf:F16"
  "hf.co/CompendiumLabs/bge-base-zh-v1.5-gguf:Q8_0"
  "embeddinggemma"
  "qwen3-embedding:0.6b"
)

pull() {
  docker volume create "$VOLUME" >/dev/null
  local pulls=""
  for m in "${MODELS[@]}"; do pulls+="ollama pull '$m'; "; done
  # default bridge network (internet), the volume writable, only `ollama pull` runs
  docker run --rm --name "${CONTAINER}-pull" -v "$VOLUME:/models" -e OLLAMA_MODELS=/models/models \
    --entrypoint sh "$IMAGE" -c "ollama serve >/tmp/serve.log 2>&1 & sleep 4; $pulls ollama list"
}

serve() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  # internal network only, the volume read-only, CPU only (no --gpus), one model in memory at a time
  docker run -d --name "$CONTAINER" --network "$NETWORK" -m "$MEM_LIMIT" --cpus "$CPUS" \
    -v "$VOLUME:/models:ro" -e OLLAMA_MODELS=/models/models -e OLLAMA_NOPRUNE=1 -e OLLAMA_KEEP_ALIVE=60m \
    -e OLLAMA_NUM_PARALLEL=2 -e OLLAMA_MAX_LOADED_MODELS=1 -e OLLAMA_HOST=0.0.0.0:11434 \
    "$IMAGE" serve >/dev/null
  sleep 6
}

check() {
  # every model must answer on the OpenAI-compatible route with its expected dimension
  local expect=(512 768 768 1024)
  for i in "${!MODELS[@]}"; do
    docker run --rm --network "$NETWORK" python:3.12-slim python -c "
import json, sys, urllib.request
model, dims = sys.argv[1], int(sys.argv[2])
req = urllib.request.Request('http://$CONTAINER:11434/v1/embeddings',
    json.dumps({'model': model, 'input': ['你好，世界']}).encode(), {'Content-Type': 'application/json'})
got = len(json.load(urllib.request.urlopen(req, timeout=180))['data'][0]['embedding'])
print(model, got, 'ok' if got == dims else 'WRONG, expected %d' % dims)
sys.exit(0 if got == dims else 1)" "${MODELS[$i]}" "${expect[$i]}"
  done
}

case "${1:-all}" in
  pull) pull ;;
  serve) serve ;;
  check) check ;;
  all) pull; serve; check ;;
  *) echo "usage: $0 [pull|serve|check|all]" >&2; exit 2 ;;
esac
