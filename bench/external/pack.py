"""The per-question result files are large (ids and scores of every question), so the repository keeps them
gzip-compressed: results/<dataset>/<config>.json.gz. The runners and the scorer work on the plain .json files.

    python -I bench/external/pack.py            # .json -> .json.gz (after a run, before committing)
    python -I bench/external/pack.py --unpack   # .json.gz -> .json (after cloning, before score.py / report.py)

Only the per-question files are packed; *.metrics.json, latency-summary.json and same-stores-*.json stay as they are.
Packing is deterministic (no timestamp or file name in the gzip header), so an unchanged result gives an unchanged .gz.
Unpacking restores the bytes that were packed. A file is rewritten only when its content would change.
"""
import argparse
import gzip
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")


def packable(name):
    return (name.endswith(".json") and not name.endswith(".metrics.json")
            and not name.startswith(("latency", "same-stores", "summary")))


def compress(data):
    return gzip.compress(data, compresslevel=9, mtime=0)


def write_if_changed(path, data):
    if os.path.exists(path):
        with open(path, "rb") as f:
            if f.read() == data:
                return False
    with open(path, "wb") as f:
        f.write(data)
    return True


def pack(root=RESULTS):
    """Compress every per-question file under root; returns the number of .gz files written."""
    written = 0
    for folder, _, names in sorted(os.walk(root)):
        for name in sorted(names):
            if packable(name):
                path = os.path.join(folder, name)
                with open(path, "rb") as f:
                    data = f.read()
                target = path + ".gz"
                if os.path.exists(target):
                    with gzip.open(target, "rb") as f:
                        if f.read() == data:
                            continue
                written += write_if_changed(target, compress(data))
    return written


def unpack(root=RESULTS):
    """Restore every .json from its .json.gz under root; returns the number of .json files written."""
    written = 0
    for folder, _, names in sorted(os.walk(root)):
        for name in sorted(names):
            if name.endswith(".json.gz") and packable(name[:-3]):
                with gzip.open(os.path.join(folder, name), "rb") as f:
                    data = f.read()
                written += write_if_changed(os.path.join(folder, name[:-3]), data)
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--unpack", action="store_true")
    parser.add_argument("--results", default=RESULTS)
    args = parser.parse_args(argv)
    n = unpack(args.results) if args.unpack else pack(args.results)
    print("%s %d file(s)" % ("unpacked" if args.unpack else "packed", n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
