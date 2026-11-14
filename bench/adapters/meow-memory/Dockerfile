# Benchmark image for meow-memory. Build context is assembled by run.sh:
#   package/   the published npm package (the unpacked npm package, copied from MEOW_MEMORY_PKG)
#   stubs/     host peer stand-ins
#   bench.mjs  the adapter
FROM node:24-slim
WORKDIR /app
# Host peer deps: schemastery is a real npm package (installed from the package's own peerDependencies
# range); dsh-llm is a DSH-internal package and is stubbed.
RUN npm init -y >/dev/null && npm install --no-audit --no-fund "@deepseek-ai/schemastery@^3.18.1"
COPY stubs/dsh-llm /app/node_modules/@deepseek-ai/dsh-llm
COPY package /app/node_modules/meow-memory
COPY bench.mjs /app/bench.mjs
ENV MEOW_WS=/tmp/ws HOME=/tmp/home
CMD ["node", "/app/bench.mjs", "/data", "/out/meow-memory.json"]
