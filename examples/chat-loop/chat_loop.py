"""One chat turn from start to finish, with memory: query, recall, block, prompt, model, remember.

    python examples/chat-loop/chat_loop.py                      # a short demo conversation
    python examples/chat-loop/chat_loop.py "I like oolong"      # your own messages, one turn each

The model is a stand-in that prints the prompt it would have received. The gateway runs in this
process (LocalClient, the configuration next to this file, memories kept in memory). Set
FEIYU_MEMGATE_URL and FEIYU_MEMGATE_TOKEN to talk to a running gateway instead, for example the one
from examples/quickstart: the only line that changes is the one that makes `memory`.
"""
import argparse
import datetime
import os
import sys
from pathlib import Path

from feiyu_memgate.client import (BlockFormatter, LocalClient, MemoryClient, build_query, meaningful,
                                  previous_user_text)

HERE = Path(__file__).resolve().parent
SYSTEM = "你是一个聊天伙伴。"
FORMATTER = BlockFormatter()  # the default block: zh head line, "data, not instructions"
DEMO = ["我对花生过敏，点菜的时候要避开", "周末打算去动物园看熊猫", "嗯", "我对什么过敏来着？"]


def make_memory(env=os.environ):
    if env.get("FEIYU_MEMGATE_URL"):
        return MemoryClient.from_env(env)  # a gateway running elsewhere
    # The access log of the in-process gateway goes to stderr by default; this example drops it.
    return LocalClient.from_config(HERE / "gateway.toml", client="owner", log=lambda line: None)


def fake_model(messages):
    """Stands in for the model call: show what would be sent, answer with a fixed line."""
    print("  prompt:")
    for message in messages:
        print("    [%s] %s" % (message["role"], message["content"].replace("\n", "\n        ")))
    return "（模型的回复）"


def turn(memory, history, text, stamp):
    """One turn. `history` is the conversation so far as chat messages; `stamp` is "YYYY-MM-DD" or
    an ISO time, the caller's idea of now (nothing below reads a clock)."""
    print("你：%s" % text)

    # 1. What to ask. None means the message is not worth a lookup ("嗯", a command). A short message
    #    borrows the previous user message for context. The query is capped at the gateway's 500 characters.
    query = build_query(text, previous_user_text(history))

    # 2. Recall. This comes before remember, so a message cannot recall itself. recall_detail()
    #    gives None on any failure and never raises: a failed lookup is not an empty one, and the
    #    turn goes on without memories either way.
    block = ""
    if query is None:
        print("  no lookup for this message")
    elif memory.configured:
        detail = memory.recall_detail(query)
        if detail is None:
            print("  recall failed, answering without memories")
        else:
            if detail["failed"] or detail["busy"]:
                print("  recall may be incomplete: %d store(s) failed, %d busy" % (detail["failed"], detail["busy"]))
            # 3. The block. "" when nothing came back or nothing usable is in the results.
            block = FORMATTER.format(detail["results"], detail["more"], today=stamp[:10])

    # 4. The prompt. The block goes right after the user's message and is not kept in `history`:
    #    everything before the current message stays the same from turn to turn (a stable prefix
    #    for the model's prompt cache), and memories are fetched fresh for each message.
    messages = [{"role": "system", "content": SYSTEM}] + history + [{"role": "user", "content": text}]
    if block:
        messages.append({"role": "user", "content": block})
    reply = fake_model(messages)
    print("机器人：%s" % reply)
    history += [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]

    # 5. Remember. Only what the user said, only when it has some content (a message that is all
    #    emoji and punctuation has none) and only when it is not a question. That rule is a stand-in:
    #    a real bot decides better (a classifier, or a tool the model calls). The answer says what
    #    became of the write:
    #    "saved" / "skipped" (looked at, nothing kept) / "limited" (the day's cap is used up) /
    #    "failed" (definitely not stored) / "unsure" (sent, no answer: it may be stored, so do not
    #    send it again blindly).
    if memory.configured and len(meaningful(text)) >= 4 and not text.rstrip().endswith(("?", "？")):
        status = memory.remember_messages([{"role": "user", "content": text, "time": stamp}])
        print("  remember: %s" % status)
    print()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("messages", nargs="*", help="what the user says, one turn each (default: a demo)")
    parser.add_argument("--today", metavar="YYYY-MM-DD",
                        help="the date to use as today, for the same output on every day (default: now)")
    args = parser.parse_args(argv)
    stamp = args.today or datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    memory = make_memory()
    history = []
    try:
        for text in args.messages or DEMO:
            turn(memory, history, text, stamp)
    finally:
        if hasattr(memory, "close"):
            memory.close()


if __name__ == "__main__":
    sys.exit(main())
