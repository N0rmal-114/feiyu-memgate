FROM python:3.12-slim
# openai 3.x (pulled in unpinned by graphiti-core) no longer depends on httpx, which graphiti-core imports
RUN pip install --no-cache-dir "graphiti-core[kuzu]==0.31.2" httpx
WORKDIR /app
COPY run_graphiti.py /app/run_graphiti.py
ENTRYPOINT ["python", "-I", "/app/run_graphiti.py"]
