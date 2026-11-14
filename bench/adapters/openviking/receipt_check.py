"""One real session commit against OpenViking, to check receipt_refs() on the committed layout.

    python receipt_check.py      (inside the container started by receipt_check.sh; costs one LLM extraction)

Sends two short messages (no personal data), commits, waits for the extraction, then prints what
receipt_refs() returns, the session listing and the memory_diff.json it read. Needs OV_URL and /root.key.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.environ.get("FMG_SRC", "/repo/src"))
import ov_common as ov  # noqa: E402
from feiyu_memgate.backends.openviking import OpenVikingBackend  # noqa: E402


def main():
    ov.wait_health()
    key = ov.setup_accounts(open("/root.key", encoding="utf-8").read().strip(), ["t"])["t"]
    backend = OpenVikingBackend(ov.BASE, key, timeout=60.0, writable=True)
    receipt = backend.remember([
        {"role": "user", "content": "Remember this: my dog is called Biscuit and I walk him every morning at 7."},
        {"role": "assistant", "content": "Noted, Biscuit gets a 7 o'clock walk."},
    ])
    start, state = time.time(), None
    while time.time() - start < 240 and not state:
        time.sleep(3)
        state = backend.write_state(receipt)
    status = backend.session_status(receipt)
    print("write_state:", state, "after %d s" % (time.time() - start))
    print("llm_token_usage:", status.get("llm_token_usage"))
    print("receipt_refs:", backend.receipt_refs(receipt))
    code, listing = ov.call("GET", "/api/v1/fs/ls?uri=viking%3A%2F%2F~%2Fsessions%2F" + receipt
                            + "&recursive=true&simple=true&node_limit=1000", key)
    print("session listing:", code, json.dumps(listing["result"], indent=1))
    for uri in listing["result"]:
        if str(uri).endswith("memory_diff.json"):
            print(backend.read(uri))
    print("refs():", [u for u, _ in backend.refs()])


if __name__ == "__main__":
    main()
