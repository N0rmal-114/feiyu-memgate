#!/usr/bin/env bash
# Assemble the docker build context from the read-only clone and build the image.
# Only TypeScript sources are copied (no node_modules, no docs, no assets); dependencies are installed
# inside `docker build` (network allowed there). Run from Git Bash on Windows.
# Needs MNEMON_SRC (see bench/README.md, section "复现").
set -euo pipefail
export MSYS_NO_PATHCONV=1

source "$(dirname "${BASH_SOURCE[0]}")/../_env.sh"
fmg_need_dir MNEMON_SRC "unpack the dsh-mnemon repository (github.com/omdsh-dev/dsh-mnemon, version 0.5.45, commit 1dec529) and set MNEMON_SRC to its root folder"
REPO=$FMG_REPO
WORK=$FMG_BENCH_HOME/work/mnemon
VENDOR=$MNEMON_SRC
ADAPTER=$REPO/bench/adapters/dsh-mnemon
CTX=$WORK/ctx
IMG=fmg-bench-mnemon

rm -rf "$CTX" && mkdir -p "$CTX/pkgs"
P=$VENDOR/plugins

# 1. Source package that owns the service (search, recall quality policy, runner) and the CLI process launcher.
mkdir -p "$CTX/pkgs/dsh-mnemon-source-memory-spaces"
cp -r "$P/dsh-mnemon-source-memory-spaces/src" "$CTX/pkgs/dsh-mnemon-source-memory-spaces/src"
rm -rf "$CTX/pkgs/dsh-mnemon-source-memory-spaces/src/client" "$CTX/pkgs/dsh-mnemon-source-memory-spaces/src/client.ts"
cp "$ADAPTER/stubs/memory-spaces.package.json" "$CTX/pkgs/dsh-mnemon-source-memory-spaces/package.json"

# 2. Default Provider (Mnemon Native driver).
mkdir -p "$CTX/pkgs/dsh-mnemon-provider-mnemon-native"
cp -r "$P/dsh-mnemon-provider-mnemon-native/src" "$CTX/pkgs/dsh-mnemon-provider-mnemon-native/src"
cp "$ADAPTER/stubs/native.package.json" "$CTX/pkgs/dsh-mnemon-provider-mnemon-native/package.json"

# 3. Default (Layered) strategy: only its retrieval module, which owns per-turn recall admission.
mkdir -p "$CTX/pkgs/dsh-mnemon-strategy-default-three-tier/src"
cp "$P/dsh-mnemon-strategy-default-three-tier/src/retrieval.ts" "$CTX/pkgs/dsh-mnemon-strategy-default-three-tier/src/retrieval.ts"
cp "$ADAPTER/stubs/strategy.package.json" "$CTX/pkgs/dsh-mnemon-strategy-default-three-tier/package.json"

# 4. Host-core stand-in "dsh-mnemon": real helper files from the root package, plus two barrel files.
#    (the root barrel imports the whole host; the services above only need these helpers)
D=$CTX/pkgs/dsh-mnemon
mkdir -p "$D/src/sdk" "$D/src/core"
cp "$VENDOR/src/sdk/input.ts" "$VENDOR/src/sdk/storage-lock.ts" "$D/src/sdk/"
cp "$VENDOR/src/core/definitions.ts" "$D/src/core/"
cp -r "$VENDOR/src/core/contracts" "$D/src/core/contracts"
cp "$ADAPTER/stubs/dsh-mnemon.package.json" "$D/package.json"
cp "$ADAPTER/stubs/extension-sdk.ts" "$ADAPTER/stubs/contracts.ts" "$D/src/"

cp "$ADAPTER/Dockerfile" "$ADAPTER/bench.mts" "$CTX/"
docker build -t "$IMG" "$CTX"
