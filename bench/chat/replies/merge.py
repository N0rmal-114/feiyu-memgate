"""Step 7 of the reply-quality evaluation: merge the outputs of the writers and the judges.

    python -B bench/chat/replies/merge.py --work <work folder>

Reads work/out/reply-NN.json ({key: reply}) into work/replies.json and work/out/judge-NN.json ({key: judgement string})
into work/judgements.json, and reports what has to be redone: keys of an input file that have no answer, keys that no input
file has (unknown), replies outside the limits (20 to 60 characters, one line, no double quote) and judgement strings that do
not parse (`a:u1,d0,w0;b:u0,d1,w0;better:a`, u may be `-`). Those are listed and left out of the merged files, never fixed or
guessed; the next `prep.py write` or `prep.py judge` cuts them again. Prints keys and counts, never texts. Exit status 1 when
something has to be redone.
"""
import argparse
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_shared():
    """shared.py of this folder, loaded by path (once, whoever asks first)."""
    key = "bench_chat_replies_shared"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, os.path.join(_HERE, "shared.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


shared = _load_shared()


def merge_answers(inputs, outputs, known, check):
    """Merge answer files. `inputs` is a list of key lists (the keys each input file asked for), `outputs` a list of
    {key: answer} (the files that exist), `known` the set of valid keys, `check(answer)` returns a reason or None.
    Returns (merged, missing, unknown, bad) with bad = {key: reason}; a later file wins over an earlier one."""
    merged, unknown, bad = {}, [], {}
    for output in outputs:
        for key, answer in output.items():
            if key not in known:
                unknown.append(key)
                continue
            reason = check(answer)
            if reason:
                bad[key] = reason
                merged.pop(key, None)
            else:
                merged[key] = answer
                bad.pop(key, None)
    asked = [key for keys in inputs for key in keys]
    missing = sorted(key for key in dict.fromkeys(asked) if key not in merged and key not in bad)
    return merged, missing, sorted(set(unknown)), dict(sorted(bad.items()))


def _judgement_problem(answer):
    return None if shared.parse_judgement(answer) else "does not parse"


def _read_outputs(folder, prefix):
    """([{key: answer}], [names of output files that are not a readable JSON object]) of <prefix>-NN.json in `folder`: a
    writer's file that is cut off or is not an object loses its answers (they show up as missing) and is named."""
    good, broken = [], []
    for _, path in shared.numbered_files(folder, prefix):
        try:
            document = shared.read_json(path)
        except (OSError, ValueError):
            document = None
        if isinstance(document, dict):
            good.append(document)
        else:
            broken.append(os.path.basename(path))
    return good, broken


def _read_inputs(folder, prefix):
    """[[keys]] of every input file of one kind."""
    return [[item["key"] for item in shared.read_json(path)] for _, path in shared.numbered_files(folder, prefix)]


def merge_kind(work, name, check, known):
    """Merge the files of one kind (`reply` or `judge`) and return the report lines plus the number to redo."""
    inputs = _read_inputs(os.path.join(work, "in"), name)
    outputs, broken = _read_outputs(os.path.join(work, "out"), name)
    merged, missing, unknown, bad = merge_answers(inputs, outputs, known, check)
    target = "replies.json" if name == "reply" else "judgements.json"
    shared.write_json(os.path.join(work, target), merged)
    lines = ["%s: %d input files, %d output files, %d answers merged into %s" % (
        name, len(inputs), len(outputs), len(merged), target)]
    if broken:
        lines.append("  unreadable output files (%d, their answers count as missing): %s" % (len(broken), ", ".join(broken)))
    for label, keys in (("missing", missing), ("unknown", unknown), ("unusable", list(bad))):
        if keys:
            shown = ", ".join(keys[:20]) + (" ..." if len(keys) > 20 else "")
            lines.append("  %s (%d): %s" % (label, len(keys), shown))
    reasons = {}
    for reason in bad.values():
        group = "length" if reason.startswith("length") else reason
        reasons[group] = reasons.get(group, 0) + 1
    if reasons:
        lines.append("  unusable by reason: " + ", ".join("%s %d" % (r, n) for r, n in sorted(reasons.items())))
    return lines, len(missing) + len(bad) + len(broken)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--work", required=True, help="the work folder of plan.py")
    args = parser.parse_args(argv)
    work = args.work
    redo = 0
    handovers_path = os.path.join(work, "handovers.json")
    if os.path.isfile(handovers_path):
        lines, count = merge_kind(work, "reply", shared.reply_problem, set(shared.read_json(handovers_path)))
        redo += count
        for line in lines:
            shared.emit(line)
    pairs_path = os.path.join(work, "pairs.json")
    if os.path.isfile(pairs_path):
        lines, count = merge_kind(work, "judge", _judgement_problem, set(shared.read_json(pairs_path)))
        redo += count
        for line in lines:
            shared.emit(line)
    return 1 if redo else 0


if __name__ == "__main__":
    sys.exit(main())
