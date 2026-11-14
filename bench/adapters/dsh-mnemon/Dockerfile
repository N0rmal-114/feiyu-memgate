# Benchmark image for dsh-mnemon (default backend: Mnemon Native = the `mnemon` CLI).
# Build context is assembled by build.sh (TypeScript sources of four packages + stand-in files).
FROM node:24-slim
WORKDIR /app
# The default backend binary, from its official npm package (pinned to the version current on 2026-11-14).
RUN npm install -g --no-audit --no-fund @mnemon-dev/mnemon@0.2.13 && mnemon --version
# Runtime deps of the plugin sources: schemastery (peer of the Source config) and tsx to run the .ts sources.
RUN npm init -y >/dev/null && npm install --no-audit --no-fund "@deepseek-ai/schemastery@3.19.0" tsx@4.20.6
COPY pkgs/ /app/node_modules/
COPY bench.mts /app/bench.mts
ENV HOME=/tmp/home TMPDIR=/tmp
CMD ["node", "--import", "tsx", "/app/bench.mts"]
