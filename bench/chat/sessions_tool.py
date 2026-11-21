"""Authoring helper for the multi-turn sessions of a chat world (bench/chat/README.md).

    python -B bench/chat/sessions_tool.py show <world.json>
    python -B bench/chat/sessions_tool.py set <world.json> <sessions.json>

`show` prints what an author needs to write sessions: the persona, every cluster with its key, entity and
terms and its memories, then the fillers. It leaves out the world's cases and any sessions it already has, so
that nobody copies them. `set` puts a JSON list of three sessions into the world under `sessions` (replacing
a list that is there), writes the file back in its own style and runs the validator on it: the problems are
printed and the exit status is 1 when there are any. The file is written either way, so the author can fix
the sessions and run it again. Which split a world belongs to is the caller's business.
"""
import importlib.util
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _validator():
    """validate.py next to this file, loaded by path (bench/external has a module of that name too)."""
    spec = importlib.util.spec_from_file_location("bench_chat_validate_for_sessions", os.path.join(_HERE, "validate.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def emit(text):
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def show(path):
    world = load(path)
    emit("world: %s" % world.get("world"))
    emit("persona: %s" % world.get("persona"))
    memories = world.get("memories", {})
    for cid, cluster in world.get("clusters", {}).items():
        emit("")
        emit("%s  entity: %s  terms: %s" % (cid, cluster.get("entity"), ", ".join(cluster.get("terms", []))))
        for mid in sorted(memories):
            memory = memories[mid]
            if memory.get("cluster") == cid:
                emit("  %s  %-7s %s  %s" % (mid, memory.get("role"), memory.get("date"), memory.get("text")))
    emit("")
    emit("fillers")
    for mid in sorted(memories):
        if memories[mid].get("role") == "filler":
            emit("  %s  %s  %s" % (mid, memories[mid].get("date"), memories[mid].get("text")))
    return 0


def write_world(path, world):
    """The style of the world files: indent 1, characters as they are, LF, one final newline."""
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(world, ensure_ascii=False, indent=1) + "\n")


def set_sessions(path, sessions_path):
    world = load(path)
    sessions = load(sessions_path)
    if not isinstance(sessions, list):
        emit("%s: the file must hold a JSON list of sessions" % os.path.basename(sessions_path))
        return 2
    world.pop("sessions", None)
    world["sessions"] = sessions  # last, after cases
    write_world(path, world)
    problems = _validator().problems_of(path)
    for line in problems:
        emit(line)
    turns = sum(len(s.get("turns", [])) for s in sessions if isinstance(s, dict) and isinstance(s.get("turns"), list))
    emit("%s: %d sessions, %d turns, %d problem(s)" % (world.get("world", os.path.basename(path)), len(sessions), turns,
                                                     len(problems)))
    return 1 if problems else 0


def main(arguments):
    if len(arguments) == 2 and arguments[0] == "show":
        return show(arguments[1])
    if len(arguments) == 3 and arguments[0] == "set":
        return set_sessions(arguments[1], arguments[2])
    emit("usage: sessions_tool.py show <world.json> | set <world.json> <sessions.json>")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
