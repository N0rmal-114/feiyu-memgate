"""Create the benchmark account and its three users on the throw-away OpenViking; keys go to /work/keys.json.

    OV_ROOT_API_KEY=... python setup.py

Three users so that each system ingests into its own space: `owner` (direct search API), `gate`
(feiyu-memgate with the gateway's defaults) and `gateprecise` (feiyu-memgate with the precise profile pinned). The key file lives in the work directory outside the repository.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ov_common as ov  # noqa: E402


def main():
    ov.wait_health()
    keys = ov.setup_accounts(os.environ["OV_ROOT_API_KEY"], ["owner", "gate", "gateprecise"])
    with open("/work/keys.json", "w", encoding="utf-8") as f:
        json.dump(keys, f)
    print("account and users created: %s" % ", ".join(sorted(keys)))


if __name__ == "__main__":
    main()
