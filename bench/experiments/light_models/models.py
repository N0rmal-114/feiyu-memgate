"""The five embedding models of the experiment and the prefixes their authors recommend.

`name` is the model name the embeddings endpoint knows; `endpoint` says which of the two benchmark embedder
containers serves it (see setup.sh). `recommended` is what the model card asks for; Ollama's template for all
of these models is the bare prompt, so the endpoint applies nothing by itself (checked with `ollama show
--modelfile`: TEMPLATE {{ .Prompt }}).

Sources of the recommendations:
  bge v1.5 (Chinese): the model card of BAAI/bge-*-zh-v1.5 gives the query instruction below for retrieval
      ("add the instruction to the query ... no instruction needs to be added to passages"); v1.5 also says
      it works without it.
  embeddinggemma: model card, prompts `task: search result | query: ` for queries and
      `title: none | text: ` for documents without a title.
  qwen3-embedding: model card, queries are `Instruct: <task>\nQuery:<query>` with the task
      `Given a web search query, retrieve relevant passages that answer the query`; documents have no prompt.
  bge-m3: its card asks for no instruction.
"""
HEAVY = "heavy"  # fmg-bench-embedder (bge-m3 lives there)
LIGHT = "light"  # fmg-bench-embedder-light

BGE_ZH_QUERY = "为这个句子生成表示以用于检索相关文章："

MODELS = [
    {"key": "bge-m3", "name": "bge-m3", "endpoint": HEAVY, "dim": 1024, "size_mb": 1200,
     "recommended": {"query_prefix": "", "document_prefix": ""}},
    {"key": "bge-small-zh", "name": "hf.co/CompendiumLabs/bge-small-zh-v1.5-gguf:F16", "endpoint": LIGHT,
     "dim": 512, "size_mb": 47.9,
     "recommended": {"query_prefix": BGE_ZH_QUERY, "document_prefix": ""}},
    {"key": "bge-base-zh", "name": "hf.co/CompendiumLabs/bge-base-zh-v1.5-gguf:Q8_0", "endpoint": LIGHT,
     "dim": 768, "size_mb": 110.0,
     "recommended": {"query_prefix": BGE_ZH_QUERY, "document_prefix": ""}},
    {"key": "embeddinggemma", "name": "embeddinggemma", "endpoint": LIGHT, "dim": 768, "size_mb": 621.9,
     "recommended": {"query_prefix": "task: search result | query: ", "document_prefix": "title: none | text: "}},
    {"key": "qwen3-embedding", "name": "qwen3-embedding:0.6b", "endpoint": LIGHT, "dim": 1024, "size_mb": 639.2,
     "recommended": {"query_prefix": "Instruct: Given a web search query, retrieve relevant passages that "
                                     "answer the query\nQuery:", "document_prefix": ""}},
]
BY_KEY = {m["key"]: m for m in MODELS}
ENDPOINTS = {HEAVY: "http://fmg-bench-embedder:11434/v1", LIGHT: "http://fmg-bench-embedder-light:11434/v1"}


def variants(model):
    """[("plain" | "prefixed", query_prefix, document_prefix)] to compare for one model."""
    rec = model["recommended"]
    out = [("plain", "", "")]
    if rec["query_prefix"] or rec["document_prefix"]:
        out.append(("prefixed", rec["query_prefix"], rec["document_prefix"]))
    return out
