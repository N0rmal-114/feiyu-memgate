FROM python:3.12-slim
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    MEM0_TELEMETRY=False MEM0_DIR=/tmp/mem0dir FASTEMBED_CACHE_PATH=/opt/fastembed
# mem0ai[nlp] (spaCy: BM25 lemmatization and entity boosts) and fastembed (Qdrant BM25 sparse vectors) are the
# documented extras behind the hybrid search of the current pipeline. The spaCy model and the BM25 model are
# fetched at BUILD time (network allowed); the benchmark itself runs without internet.
RUN pip install "mem0ai[nlp]==2.2.1" fastembed ollama \
 && python -m spacy download en_core_web_sm \
 && python -c "from fastembed import SparseTextEmbedding as S; list(S(model_name='Qdrant/bm25').embed(['warm']))" \
 && pip freeze | grep -iE "^(mem0|qdrant|openai|ollama|fastembed|spacy|en-core)" > /opt/versions.txt \
 && cat /opt/versions.txt
WORKDIR /app
COPY run_mem0.py /app/run_mem0.py
ENV HF_HUB_OFFLINE=1
ENTRYPOINT ["python", "-I", "/app/run_mem0.py"]
