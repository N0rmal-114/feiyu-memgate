"""Adapter: feiyu-memgate with its hybrid backend (sqlite keyword store + embedding vector store, fused).

    python bench/adapters/feiyu_memgate_hybrid.py [--out DIR] [--embed-url URL] [--model NAME]
                                                  [--system-suffix SUFFIX] [--option KEY=JSON ...]

Writes <out>/feiyu-memgate-hybrid.json (the contract is bench/CONTRACT.md; default out is
bench/results/raw); with --system-suffix small-zh the system is feiyu-memgate-hybrid-small-zh. --option sets
an option of the embedding child (the hybrid's own options stay at their defaults). Same driving as feiyu_memgate_embedding.py (which this file reuses), with a
HybridBackend of two in-memory children: sqlite (IDF-weighted gram containment) and embedding (bge-m3
through the embeddings endpoint). Both get every memory under the memory id as ref (one put_many call);
find fuses them by probabilistic OR of the children's scores.

Every setting is a documented default (the hybrid's default_min_score 0.70 and default_relative_ratio 0.85,
the embedding child's floor 0.40 / ceil 0.80), chosen on the development sets examples/eval and bench/dev with bench/dev_eval.py, never on
bench/data.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feiyu_memgate_embedding as base  # noqa: E402

SYSTEM = "feiyu-memgate-hybrid"
MODE = ("verbatim ingest, hybrid backend (sqlite keyword + embedding bge-m3, probabilistic-OR fusion), "
        "default recall stages, no LLM")
HOW = ("config.build_gateway(dict); HybridBackend.put_many([(text, id, date)]) once for all memories (written to "
       "both children under the memory id); Gateway.recall('owner', {'query': q}) for deployed; "
       "HybridBackend.find(query, 10) for ranked")


def store_spec(embed_url, model, options=None):
    return {"type": "hybrid", "children": [
        {"type": "sqlite", "path": ":memory:"},
        base.store_spec(embed_url, model, options),
    ]}


def notes():
    return [
        "Backend is the hybrid backend: a sqlite keyword child (IDF-weighted containment of character bigrams and "
        "CJK single characters) and an embedding child (bge-m3, cosine mapped with floor 0.40 and ceil 0.80); "
        "fused score = 1 - prod(1 - score_i) with both weights 1.0. Defaults chosen on the development sets, not on this data.",
        "ranked scores are the fused relevance values, not comparable across systems.",
        "deployed uses the default recall stages (min_score with the hybrid's default 0.70, relative_score with its "
        "default ratio 0.85, fill_text, drop_oversize, redact, dedupe, budget) and the default limit of 5; ms times "
        "that Gateway.recall call only (it asks both children, one after the other).",
    ]


def main(argv=None):
    args = base.parse_args(__doc__, argv)
    options = base.parse_options(args.option)
    notes_list = notes()
    if args.model != base.DEFAULT_MODEL or options or args.system_suffix:
        floor, ceil = base.effective(base.store_spec(args.embed_url, args.model, options))[:2]
        notes_list[0] = ("Backend is the hybrid backend: a sqlite keyword child and an embedding child (model %s, cosine "
                         "mapped with floor %.2f and ceil %.2f, the embedding backend's values for this model); fused "
                         "score = 1 - prod(1 - score_i) with both weights 1.0. Defaults chosen on the development "
                         "sets, not on this data." % (args.model, floor, ceil))
    mode = MODE if args.model == base.DEFAULT_MODEL else MODE.replace("bge-m3", args.model)
    if args.profile == "broad":
        notes_list.append("Broad profile: [recall] profile = 'broad': min_score 0.40 and ratio 0.65 (the hybrid's "
                          "broad_min_score and broad_relative_ratio) instead of 0.70 and 0.85.")
        mode += ", broad recall profile"
    path = base.run_system(store_spec(args.embed_url, args.model, options), args.out,
                           base.system_name(SYSTEM, args.system_suffix), mode, HOW, notes_list, profile=args.profile)
    print("wrote " + path)


if __name__ == "__main__":
    main()
