#!/usr/bin/env bash
# Nothing to build: OpenViking runs from the image that is already local, by digest (never pulled here),
# and the adapters run in python:3.12-slim with the repository mounted read-only.
set -euo pipefail
docker image inspect "ghcr.io/volcengine/openviking@sha256:57d3fd8873dbb78e5ccfdc57481006e6fbe765390ea18cd4a6b2f62da8aa104d" >/dev/null
docker image inspect python:3.12-slim >/dev/null 2>&1 || docker pull python:3.12-slim
echo "images ready"
