# Image for the lingshu dsh-memory benchmark adapter. Network is needed only while building.
FROM node:24-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/pkg
# The published package (lib/ is already built in the tarball). Dependencies come from its own package.json;
# --ignore-scripts skips its "prepare" build step (needs dev dependencies and Python build scripts).
COPY pkg/ /app/pkg/
RUN npm install --omit=dev --ignore-scripts --no-audit --no-fund

COPY lingshu_bench.mjs /app/lingshu_bench.mjs

# Runtime: everything the plugin writes goes under /work (tmpfs in run.sh); never into the package dir.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    HOME=/work/home \
    MDCG_ROOT=/work/mdcg \
    LINGSHU_PKG=/app/pkg \
    BENCH_DATA=/bench/data \
    BENCH_OUT=/out/lingshu-dsh-memory.json

USER node
CMD ["sh", "-c", "mkdir -p /work/home /work/mdcg && exec node /app/lingshu_bench.mjs"]
