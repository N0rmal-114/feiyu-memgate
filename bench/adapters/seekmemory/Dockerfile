# Build context is assembled by run.sh: ./seekmemory/ (copy of the kernel sources) and ./run_seekmemory.py.
# SeekMemory's runtime needs only the standard library (its only manifest, requirements-dev.txt, lists
# contract-test tools that are not used here), so no pip install step is needed.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 SEEKMEMORY_DIR=/app/seekmemory SEEKMEMORY_COMMIT=794ec5d
WORKDIR /app
COPY seekmemory /app/seekmemory
COPY run_seekmemory.py /app/run_seekmemory.py
RUN useradd -r -u 10001 bench
USER bench
ENTRYPOINT ["python", "-I", "/app/run_seekmemory.py"]
