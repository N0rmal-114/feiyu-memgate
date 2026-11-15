#!/bin/sh
# Installs this package into a DSH-like plugin profile with pnpm 11.28.5, the way the DSH plugin
# manager does (pnpm add of the package into a hoisted profile). Runs inside node:24-slim.
#
# usage: install-profile.sh <source dir> <profile dir> <mode>
#   mode "lean":     the manual pnpm-workspace.yaml settings the README documents (no GPU or
#                    foreign-architecture packages, install script denied)
#   mode "default":  the settings DSH writes, plus the install script approved, which is the state
#                    after "Allow these scripts and retry" on the Plugins page
set -eu
SRC=$1
PROFILE=$2
MODE=$3
npm i -g pnpm@11.28.5 >/dev/null 2>&1
mkdir -p /tmp/pack
cd /tmp/pack
npm pack "$SRC" --silent >/dev/null
TGZ=$(ls /tmp/pack/dsh-feiyu-memgate-embed-*.tgz)
rm -rf "$PROFILE"
mkdir -p "$PROFILE"
cd "$PROFILE"
printf '{"name":"dsh-profile","private":true,"dependencies":{}}\n' > package.json
printf 'packages:\n  - .\n\nnodeLinker: hoisted\nautoInstallPeers: false\n' > pnpm-workspace.yaml
if [ "$MODE" = lean ]; then
  cat >> pnpm-workspace.yaml <<'EOF'
ignoredOptionalDependencies:
  - "@node-llama-cpp/linux-x64-cuda"
  - "@node-llama-cpp/linux-x64-cuda-ext"
  - "@node-llama-cpp/linux-x64-vulkan"
  - "@node-llama-cpp/linux-arm64"
  - "@node-llama-cpp/linux-armv7l"
  - "@node-llama-cpp/win-x64-cuda"
  - "@node-llama-cpp/win-x64-cuda-ext"
  - "@node-llama-cpp/win-x64-vulkan"
  - "@node-llama-cpp/win-arm64"
  - "@node-llama-cpp/mac-arm64-metal"
allowBuilds:
  node-llama-cpp: false
EOF
else
  printf 'allowBuilds:\n  node-llama-cpp: true\n' >> pnpm-workspace.yaml
fi
pnpm add "$TGZ" > /tmp/install.log 2>&1 || { tail -n 20 /tmp/install.log; exit 1; }
grep -v '^Progress' /tmp/install.log | tail -n 6
echo "node_modules: $(du -sm node_modules | cut -f1) MB"
ls node_modules/@node-llama-cpp
