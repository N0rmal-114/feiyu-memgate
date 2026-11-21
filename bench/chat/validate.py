"""Check the world files of the chat test set (bench/chat/README.md) against its construction rules.

    python -B bench/chat/validate.py bench/chat/dev [more folders or files ...]

Prints one line per problem and exits 1 when there is any. The rules are the ones that make the labels
checkable: a chain memory belongs to the cluster of a direct memory, an association remark does not
name its cluster, a follow-up cannot be understood without its history. A world may also carry multi-turn
`sessions`; their rules are in session_problems().
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
# Sessions (optional): a world that has them has exactly SESSIONS, each of SESSION_TURNS turns.
SESSIONS = 3
SESSION_TURNS = (6, 9)
SESSION_KINDS = ("open", "named", "continue", "idle", "return", "nextday")
USER_LENGTH = (1, 30)
ASSISTANT_LENGTH = (1, 50)


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
    if "sessions" in world:
        session_problems(world["sessions"], clusters, memories, bad)
    return out


def _terms(clusters, cid):
    """The terms of a cluster as a list of strings (a malformed cluster has none: its own check reports it)."""
    terms = clusters.get(cid, {}).get("terms") if isinstance(clusters.get(cid), dict) else None
    return [t for t in terms if isinstance(t, str) and t] if isinstance(terms, list) else []


def session_problems(sessions, clusters, memories, bad):
    """The rules of the multi-turn sessions of a world; every violation goes to bad() as one line."""
    if not isinstance(sessions, list):
        bad("sessions must be a list")
        return
    if len(sessions) != SESSIONS:
        bad("a world with sessions has exactly %d of them, found %d" % (SESSIONS, len(sessions)))
    seen = set()
    used = {}  # cluster -> the first session that has a turn about it
    for number, session in enumerate(sessions, 1):
        sid = session.get("id") if isinstance(session, dict) else None
        name = sid if isinstance(sid, str) and sid else "#%d" % number

        def flag(message, turn=None, name=name):
            bad("session %s%s: %s" % (name, "" if turn is None else " turn %d" % turn, message))

        if not isinstance(session, dict):
            flag("is not an object")
            continue
        if not isinstance(sid, str) or not sid:
            flag("needs an id")
        elif sid in seen:
            flag("id is used twice")
        seen.add(sid)
        turns = session.get("turns")
        if not isinstance(turns, list):
            flag("turns must be a list")
            continue
        if not SESSION_TURNS[0] <= len(turns) <= SESSION_TURNS[1]:
            flag("%d turns, a session has %d to %d" % (len(turns), SESSION_TURNS[0], SESSION_TURNS[1]))

        kinds = []
        about = []          # the cluster of each turn, None for none or for an unusable one
        conversation = set()  # clusters of the conversation so far
        before = set()      # clusters of the conversation before the current one
        nextday_at = None
        for index, turn in enumerate(turns, 1):
            if not isinstance(turn, dict):
                flag("is not an object", index)
                kinds.append(None)
                about.append(None)
                continue
            kind = turn.get("kind")
            if kind not in SESSION_KINDS:
                flag("unknown kind %r" % (kind,), index)
                kind = None
            kinds.append(kind)
            user, assistant = turn.get("user"), turn.get("assistant")
            if not isinstance(user, str) or not USER_LENGTH[0] <= len(user) <= USER_LENGTH[1]:
                flag("user must have %d to %d characters" % USER_LENGTH, index)
                user = ""
            if assistant is None:
                if index != len(turns):
                    flag("needs an assistant line (only the last turn may go without)", index)
            elif not isinstance(assistant, str) or not ASSISTANT_LENGTH[0] <= len(assistant) <= ASSISTANT_LENGTH[1]:
                flag("assistant must have %d to %d characters" % ASSISTANT_LENGTH, index)
                turn = dict(turn, assistant=None)

            cid = turn.get("cluster")
            if cid is not None and cid not in clusters:
                flag("unknown cluster %r" % (cid,), index)
                cid = None
            about.append(cid)
            labels = {}
            for key in ("direct", "also_ok"):
                value = turn.get(key)
                if not isinstance(value, list) or any(not isinstance(m, str) or m not in memories for m in value):
                    flag("%s must be a list of known memories" % key, index)
                    value = []
                labels[key] = value
            direct, also = labels["direct"], labels["also_ok"]
            if set(direct) & set(also):
                flag("a memory is in both direct and also_ok", index)
            if len(set(direct)) != len(direct) or len(set(also)) != len(also):
                flag("a memory is listed twice", index)

            # a new conversation starts here
            fresh = turn.get("new_conversation")
            gap = turn.get("gap_days")
            if fresh is not None and fresh is not True:
                flag("new_conversation is true or absent", index)
            if fresh is True:
                if not isinstance(gap, int) or isinstance(gap, bool) or gap < 1:
                    flag("a new conversation needs gap_days, an integer of at least 1", index)
                if index == 1:
                    flag("the first turn cannot start a new conversation", index)
                else:
                    before, conversation = conversation, set()
            elif gap is not None:
                flag("gap_days only goes with new_conversation", index)
            if fresh is True and kind != "nextday":
                flag("only the nextday turn starts a new conversation", index)
            if kind == "nextday":
                if fresh is not True:
                    flag("a nextday turn must carry new_conversation", index)
                if nextday_at is None:
                    nextday_at = index

            members = [m for m in direct if m in memories]
            outside = [m for m in members if memories[m].get("cluster") != cid]
            terms = _terms(clusters, cid) if cid else []
            named = any(term in user for term in terms)
            previous = about[-2] if index > 1 else None
            if kind == "idle":
                if cid is not None or direct or also:
                    flag("an idle turn has no cluster and no memories", index)
            elif kind is not None:
                if cid is None:
                    flag("a %s turn needs a cluster" % kind, index)
                else:
                    if kind in ("open", "continue", "return", "nextday") and named:
                        flag("the line names the cluster %s" % cid, index)
                    if kind == "named":
                        if not named:
                            flag("the line names no term of the cluster %s" % cid, index)
                        if not direct or outside:
                            flag("direct must be non-empty and all of cluster %s" % cid, index)
                    elif kind == "open":
                        facts = [m for m in memories if memories[m].get("cluster") == cid and memories[m].get("role") == "fact"]
                        if direct != facts:
                            flag("direct must be exactly the fact of cluster %s" % cid, index)
                    else:
                        if not 1 <= len(direct) <= 2 or outside:
                            flag("direct must be 1 or 2 memories of cluster %s" % cid, index)
                    if kind == "continue" and (index == 1 or previous != cid):
                        flag("the turn before must be about the same cluster %s" % cid, index)
                    if kind == "return":
                        if cid not in conversation:
                            flag("cluster %s did not appear earlier in this conversation" % cid, index)
                        if index > 1 and previous == cid:
                            flag("the turn before must have another cluster or none", index)
                    if kind == "nextday" and cid not in before:
                        flag("cluster %s did not appear in the conversation before" % cid, index)
            if cid is not None:
                conversation.add(cid)
                if used.setdefault(cid, name) != name:
                    flag("cluster %s is also used by session %s" % (cid, used[cid]), index)

        for kind in ("open", "continue", "idle", "return"):
            if kind not in kinds:
                flag("has no %s turn" % kind)
        if kinds.count("nextday") != 1:
            flag("needs exactly one nextday turn, has %d" % kinds.count("nextday"))
        if kinds and kinds[0] not in ("open", "named"):
            flag("the first turn must be open or named", 1)
        if len({c for c in about if c}) < 2:
            flag("needs at least two different clusters")
        if nextday_at is not None:
            if len(turns) - nextday_at > 2:
                flag("at most two turns may follow the nextday turn", nextday_at + 3)
            for index in range(nextday_at + 1, len(turns) + 1):
                if kinds[index - 1] not in ("continue", "idle", "named", "open"):
                    flag("after the nextday turn only continue, idle, named or open", index)

        # a scripted reply must not hand the name of the topic to a later lookup
        for index, turn in enumerate(turns, 1):
            line = turn.get("assistant") if isinstance(turn, dict) else None
            if not isinstance(line, str):
                continue
            for later in range(index, len(turns) + 1):
                cid = about[later - 1]
                hit = [t for t in _terms(clusters, cid) if t in line] if cid else []
                if hit:
                    flag("the assistant line contains the term %r of cluster %s (turn %d)" % (hit[0], cid, later), index)
                    break


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
