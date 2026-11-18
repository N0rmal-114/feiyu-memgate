"""Check the world files of the chat test set (bench/chat/README.md) against its construction rules.

    python -B bench/chat/validate.py bench/chat/dev [more folders or files ...]

Prints one line per problem and exits 1 when there is any. The rules are the ones that make the labels
checkable: a chain memory belongs to the cluster of a direct memory, an association remark does not
name its cluster, a follow-up cannot be understood without its history.
"""
import datetime
import json
import os
import sys

TYPES = ("remark-assoc", "remark-named", "follow-up", "quiet")
ROLES = ("fact", "episode", "filler")
# Per world: how many memories, clusters and cases of each type a complete world has.
MEMORIES = 60
CLUSTERS = (12, 18)
CASES = {"remark-assoc": 12, "remark-named": 6, "follow-up": 8, "quiet": 4}
TEXT_LENGTH = (10, 70)
FOLLOW_UP_MAX = 12  # characters of the last message of a follow-up


def problems_of(path):
    out = []

    def bad(message):
        out.append("%s: %s" % (os.path.basename(path), message))

    try:
        with open(path, encoding="utf-8") as handle:
            world = json.load(handle)
    except (OSError, ValueError) as error:
        return ["%s: cannot read (%s)" % (os.path.basename(path), type(error).__name__)]
    for key in ("world", "persona", "clusters", "memories", "cases"):
        if key not in world:
            bad("missing %r" % key)
    if out:
        return out
    clusters, memories, cases = world["clusters"], world["memories"], world["cases"]

    if len(memories) != MEMORIES:
        bad("%d memories, expected %d" % (len(memories), MEMORIES))
    if not CLUSTERS[0] <= len(clusters) <= CLUSTERS[1]:
        bad("%d clusters, expected %d to %d" % (len(clusters), CLUSTERS[0], CLUSTERS[1]))
    for cid, cluster in clusters.items():
        terms = cluster.get("terms")
        if not cluster.get("entity") or not isinstance(terms, list) or not terms or not all(isinstance(t, str) and t for t in terms):
            bad("cluster %s needs an entity and a non-empty list of terms" % cid)

    texts = {}
    by_cluster = {}
    for mid, memory in memories.items():
        text, date, cid, role = memory.get("text"), memory.get("date"), memory.get("cluster"), memory.get("role")
        if not isinstance(text, str) or not TEXT_LENGTH[0] <= len(text) <= TEXT_LENGTH[1]:
            bad("memory %s: text must have %d to %d characters" % (mid, TEXT_LENGTH[0], TEXT_LENGTH[1]))
            continue
        if text in texts:
            bad("memory %s repeats the text of %s" % (mid, texts[text]))
        texts[text] = mid
        try:
            datetime.date.fromisoformat(date)
        except (TypeError, ValueError):
            bad("memory %s: bad date" % mid)
        if role not in ROLES:
            bad("memory %s: role must be one of %s" % (mid, ", ".join(ROLES)))
        if role == "filler":
            if cid is not None:
                bad("memory %s: a filler has no cluster" % mid)
            continue
        if cid not in clusters:
            bad("memory %s: unknown cluster %r" % (mid, cid))
            continue
        by_cluster.setdefault(cid, []).append(mid)
        if not any(term in text for term in clusters[cid]["terms"]):
            bad("memory %s does not mention any term of its cluster %s" % (mid, cid))
    for cid in clusters:
        roles = [memories[mid]["role"] for mid in by_cluster.get(cid, [])]
        if roles.count("fact") != 1 or not 1 <= roles.count("episode") <= 3:
            bad("cluster %s needs exactly one fact and one to three episodes" % cid)

    counts = dict.fromkeys(TYPES, 0)
    seen = set()
    for case in cases:
        cid_ = case.get("id")
        kind = case.get("type")
        if kind not in TYPES:
            bad("case %s: unknown type %r" % (cid_, kind))
            continue
        counts[kind] += 1
        if cid_ in seen:
            bad("case id %s is used twice" % cid_)
        seen.add(cid_)
        message = case.get("message")
        if not isinstance(message, str) or not message.strip():
            bad("case %s: empty message" % cid_)
            continue
        labels = {}
        for key in ("direct", "chain", "also_ok"):
            value = case.get(key, [])
            if not isinstance(value, list) or any(mid not in memories for mid in value):
                bad("case %s: %s names an unknown memory" % (cid_, key))
                value = []
            labels[key] = value
        direct, chain, also = labels["direct"], labels["chain"], labels["also_ok"]
        if len(set(direct + chain + also)) != len(direct + chain + also):
            bad("case %s: a memory appears in two of direct, chain, also_ok" % cid_)
        history = case.get("history", [])
        if not isinstance(history, list) or any(
                not isinstance(turn, dict) or turn.get("role") not in ("user", "assistant")
                or not isinstance(turn.get("content"), str) or not turn["content"].strip() for turn in history):
            bad("case %s: history must be a list of {role, content}" % cid_)
            history = []
        direct_clusters = {memories[mid].get("cluster") for mid in direct}
        if kind == "quiet":
            if direct or chain or also:
                bad("case %s: a quiet case has no labels" % cid_)
        elif not direct:
            bad("case %s: no direct memory" % cid_)
        if kind == "remark-assoc":
            if history:
                bad("case %s: a remark has no history" % cid_)
            if not case.get("why"):
                bad("case %s: an association needs its reason in 'why'" % cid_)
            for mid in direct:
                cluster = clusters.get(memories[mid].get("cluster"))
                if cluster and any(term in message for term in cluster["terms"]):
                    bad("case %s: the remark names the cluster of %s" % (cid_, mid))
            for mid in chain:
                if memories[mid].get("cluster") not in direct_clusters or memories[mid].get("role") != "episode":
                    bad("case %s: chain memory %s is not an episode of a direct memory's cluster" % (cid_, mid))
        if kind == "remark-named":
            if history:
                bad("case %s: a remark has no history" % cid_)
            named = [c for c in direct_clusters if c in clusters and any(t in message for t in clusters[c]["terms"])]
            if not named:
                bad("case %s: the remark names no cluster of its direct memories" % cid_)
        if kind == "follow-up":
            if len(history) < 2 or history[-1]["role"] != "assistant":
                bad("case %s: a follow-up needs at least two turns of history ending with the assistant" % cid_)
            if len(message) > FOLLOW_UP_MAX:
                bad("case %s: the last message of a follow-up has at most %d characters" % (cid_, FOLLOW_UP_MAX))
            for mid in direct:
                cluster = clusters.get(memories[mid].get("cluster"))
                if cluster and any(term in message for term in cluster["terms"]):
                    bad("case %s: the last message names the cluster of %s" % (cid_, mid))
    for kind, wanted in CASES.items():
        if counts[kind] != wanted:
            bad("%d cases of type %s, expected %d" % (counts[kind], kind, wanted))
    return out


def world_files(arguments):
    for argument in arguments:
        if os.path.isdir(argument):
            for name in sorted(os.listdir(argument)):
                if name.endswith(".json"):
                    yield os.path.join(argument, name)
        else:
            yield argument


def main(arguments):
    files = list(world_files(arguments))
    if not files:
        print("no world files")
        return 1
    problems = []
    for path in files:
        problems.extend(problems_of(path))
    for line in problems:
        sys.stdout.buffer.write((line + "\n").encode("utf-8"))
    print("%d world file(s), %d problem(s)" % (len(files), len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
