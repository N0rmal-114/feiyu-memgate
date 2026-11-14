"""A chat loop that remembers, in the same process as the gateway or over HTTP.

The only line that changes between the two modes is the one that makes `memory`.
Every call after it is the same, because LocalClient and MemoryClient have one interface
and neither raises: a failed call gives [] / None / "failed", and the bot carries on.

    python examples/embedded/bot.py
"""
import os
import sys
from pathlib import Path

from feiyu_memgate.client import LocalClient, MemoryClient, format_block

HERE = Path(__file__).resolve().parent


def make_memory(env=os.environ):
    if env.get("FEIYU_MEMGATE_URL"):
        return MemoryClient.from_env(env)                              # a gateway running elsewhere
    return LocalClient.from_config(HERE / "gateway.toml", client="owner")  # a gateway in this process


def reply_to(memory, text):
    """One turn: recall, answer (a stand-in for the model call), remember."""
    block = format_block(memory.recall(text)) if memory.configured else ""
    answer = "I remember:\n%s" % block if block else "Nothing about that yet."
    if memory.configured:
        memory.remember("The user said: " + text)
    return answer


def main():
    # Relative store paths in the configuration are relative to the working directory.
    os.chdir(HERE)
    os.makedirs("data", exist_ok=True)
    memory = make_memory()
    try:
        for line in sys.stdin:
            if line.strip():
                print(reply_to(memory, line.strip()))
    finally:
        if hasattr(memory, "close"):
            memory.close()


if __name__ == "__main__":
    main()
