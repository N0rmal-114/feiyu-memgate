"""Adapter: feiyu-memgate with its hybrid backend (sqlite keyword store + embedding vector store, fused).

    python bench/adapters/feiyu_memgate_hybrid.py [--out DIR] [--embed-url URL] [--model NAME]
                                                  [--system-suffix SUFFIX] [--option KEY=JSON ...] [--profile precise]

Writes <out>/feiyu-memgate-hybrid.json (the contract is bench/CONTRACT.md; default out is
bench/results/raw); with --system-suffix small-zh the system is feiyu-memgate-hybrid-small-zh. --option sets
an option of the embedding child (the hybrid's own options stay at their defaults). Without --profile the gateway's
defaults run (broad profile); --profile precise pins the precise lines and writes feiyu-memgate-hybrid-precise.json. Same driving as feiyu_memgate_embedding.py (which this file reuses), with a
HybridBackend of two in-memory children: sqlite (IDF-weighted gram containment) and embedding (bge-m3
through the embeddings endpoint). Both get every memory under the memory id as ref (one put_many call);
find fuses them by probabilistic OR of the children's scores.

Every setting is a documented default (the hybrid's broad lines 0.40 / 0.65 by default, its precise lines
default_min_score 0.70 and default_relative_ratio 0.85 with --profile precise, the embedding child's floor 0.40 /
ceil 0.80), chosen on the development sets examples/eval and bench/dev with bench/dev_eval.py, never on
bench/data.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feiyu_memgate_embedding as base  # noqa: E402

SYSTEM = "feiyu-memgate-hybrid"
MODE = ("verbatim ingest, hybrid backend (sqlite keyword + embedding bge-m3, probabilistic-OR fusion), "
        "default recall stages (broad profile), no LLM")
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
        None,  # the stage note, filled in by stage_notes()
    ]


def stage_notes(notes_list, profile):
    """Fill the stage note (the third one) of `notes()`: which lines the deployed recall used."""
    from feiyu_memgate.backends.hybrid import HybridBackend
    notes_list[2] = base.stage_note(profile, base.lines_of(HybridBackend), "the hybrid's") + \
        " The recall call asks both children, one after the other."
    return notes_list


def main(argv=None):
    args = base.parse_args(__doc__, argv)
    options = base.parse_options(args.option)
    notes_list = stage_notes(notes(), args.profile)
    if args.model != base.DEFAULT_MODEL or options or args.system_suffix:
        floor, ceil = base.effective(base.store_spec(args.embed_url, args.model, options))[:2]
        notes_list[0] = ("Backend is the hybrid backend: a sqlite keyword child and an embedding child (model %s, cosine "
                         "mapped with floor %.2f and ceil %.2f, the embedding backend's values for this model); fused "
                         "score = 1 - prod(1 - score_i) with both weights 1.0. Defaults chosen on the development "
                         "sets, not on this data." % (args.model, floor, ceil))
    mode = MODE if args.model == base.DEFAULT_MODEL else MODE.replace("bge-m3", args.model)
    if args.profile == "precise":
        notes_list.append("Precise profile: [recall] profile = 'precise': min_score 0.70 and ratio 0.85 (the hybrid's "
                          "default_min_score and default_relative_ratio, the precise lines) instead of the broad 0.40 "
                          "and 0.65 of the plain run.")
        mode = mode.replace("default recall stages (broad profile)", "default recall stages") + \
            ", precise recall profile"
    path = base.run_system(store_spec(args.embed_url, args.model, options), args.out,
                           base.system_name(SYSTEM, args.system_suffix, args.profile), mode, HOW, notes_list,
                           profile=args.profile)
    print("wrote " + path)


if __name__ == "__main__":
    main()
