# Build context is assembled by build.sh: ./src/ (copy of the scanned memsearch checkout without .git,
# tests, docs, assets) and ./run_memsearch.py. Dependencies come from PyPI at build time only.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 PIP_NO_CACHE_DIR=1 HOME=/tmp
COPY src /src
RUN pip install /src && rm -rf /src
COPY run_memsearch.py /app/run_memsearch.py
RUN useradd -r -u 10001 bench
USER bench
WORKDIR /tmp
ENTRYPOINT ["python", "-I", "/app/run_memsearch.py"]
