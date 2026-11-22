"""Audience scenarios: check, end to end and without any model, that a memory never reaches an audience it is
not for, and that one person is one person on every host.

    python -B bench/audience/run.py                          # every scenario in bench/audience/scenarios
    python -B bench/audience/run.py owner-private-vs-group   # only the ones named (by id)
    python -B bench/audience/run.py --json out.json          # also write the results
    python -B bench/audience/run.py --list                   # the ids, one per line
    python -B bench/audience/run.py --dir DIR                # scenario files from another folder

A scenario is a JSON file (format and list: bench/audience/README.md). The runner turns its `setup` into a
configuration dict, builds a gateway with config.build_gateway over real SQLite files in a temporary folder,
and executes the `steps` in order. A step that asserts something is a probe; a probe that does not hold is
printed with its `why`, and the exit code is 1 when any probe failed. Tokens and the identity key are made up
here. Standard library only; no model, no network, no clock: the same scenario gives the same result.

Steps (`do`):
    remember         a client stores a text (as a speaker, in a tenant, with message keys)
    recall           a client recalls (or asks for an explained recall) and the answer is checked
    put_raw          a record goes straight into a store file: `direct` (no audience) or `legacy` (the SQL an
                     older version wrote)
    copy_store       the file of one store is copied to another store's file name
    reconfigure      the installation changes (a merge patch on the setup) and the gateway is rebuilt on the
                     same files; with `refuse` the new setup must not start
    restamp          the `restamp` command runs on a store, with the gateway stopped
    forget_restore   the forgetting service forgets what a store holds, and the named memories are restored
    files            a string must not occur (or must occur) in the bytes of any store file
    codes            the stand-in codes found in some stores are all the same, or all different
"""
import argparse
import contextlib
import io
import itertools
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest.mock as mock
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from feiyu_memgate import cli, config  # noqa: E402
from feiyu_memgate.core.tidy import index_grams, normalize  # noqa: E402
from feiyu_memgate.errors import ConfigError  # noqa: E402
from feiyu_memgate.forgetting.archive import Archive  # noqa: E402
from feiyu_memgate.forgetting.curve import Ebbinghaus  # noqa: E402
from feiyu_memgate.forgetting.engine import Forgetting  # noqa: E402
from feiyu_memgate.forgetting.ledger import Ledger  # noqa: E402
from feiyu_memgate.model import AUDIENCES  # noqa: E402

SCENARIOS = os.path.join(HERE, "scenarios")
DAY = 86400.0

# The protections a scenario may claim in `checks`; tests/test_bench_audience.py switches each one off.
PROTECTIONS = ("store_rule", "record_rule", "unstamped_is_owner", "stamp_at_write", "platform_rule", "only_for",
               "code_per_store", "raw_account_gone", "explain_owner_only", "names_owner_only")

IDENTITY_KEY = "scenario-identity-key-" + "k" * 32
CJK = re.compile("[一-鿿]")
SCENARIO_ID = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")

# the keys each step kind may carry, and the ones it must carry
EXPECTATIONS = ("status", "absent", "present", "never", "fields", "candidate_fields")
STEP_KEYS = {
    "remember": ({"do", "client", "as", "tenant_id", "text", "role", "keys", "status", "why"}, {"client", "text"}),
    "recall": ({"do", "client", "as", "tenant_id", "query", "mode", "limit", "why"} | set(EXPECTATIONS),
               {"client", "query", "why"}),
    "put_raw": ({"do", "store", "tenant_id", "text", "how"}, {"store", "text", "how"}),
    "copy_store": ({"do", "from", "to"}, {"from", "to"}),
    "reconfigure": ({"do", "patch", "refuse", "why"}, {"patch"}),
    "restamp": ({"do", "store", "tenant_id", "mode", "changed", "why"}, {"store", "mode"}),
    "forget_restore": ({"do", "store", "tenant_id", "texts", "why"}, {"store", "texts", "why"}),
    "files": ({"do", "lack", "have", "why"}, {"why"}),
    "codes": ({"do", "where", "expect", "why"}, {"where", "expect", "why"}),
}


# ------------------------------------------------------------------------------ the format


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_all(folder=SCENARIOS):
    """[(path, scenario)] for every *.json file of `folder`, in file name order."""
    return [(os.path.join(folder, name), load(os.path.join(folder, name)))
            for name in sorted(os.listdir(folder)) if name.endswith(".json")]


def _text(value):
    return isinstance(value, str) and value.strip() != ""


def _strings(value):
    return isinstance(value, list) and all(_text(item) for item in value)


def validate(scenario, name=None):
    """The problems of a scenario file as a list of sentences (empty when it is well formed). `name` is the file
    name without .json, which the id must equal."""
    problems = []
    if not isinstance(scenario, dict):
        return ["the file must hold one JSON object"]
    unknown = set(scenario) - {"id", "title", "checks", "setup", "steps"}
    if unknown:
        problems.append("unknown key %s" % ", ".join(sorted(unknown)))
    sid = scenario.get("id")
    if not isinstance(sid, str) or SCENARIO_ID.fullmatch(sid) is None:
        problems.append("id must be lower case words joined by -")
    elif name is not None and sid != name:
        problems.append("id %s is not the file name %s" % (sid, name))
    title = scenario.get("title")
    if not _text(title) or CJK.search(title) is None:
        problems.append("title must be a Chinese text")
    checks = scenario.get("checks")
    if not isinstance(checks, list) or not checks or not all(item in PROTECTIONS for item in checks):
        problems.append("checks must be a non-empty list of protections: %s" % ", ".join(PROTECTIONS))
    problems.extend(_validate_setup(scenario.get("setup")))
    steps = scenario.get("steps")
    if not isinstance(steps, list) or not steps:
        problems.append("steps must be a non-empty list")
        return problems
    absent, present = set(), set()
    for number, step in enumerate(steps, 1):
        problems.extend("step %d: %s" % (number, problem) for problem in _validate_step(step))
        if isinstance(step, dict) and step.get("do") == "recall":
            absent.update(item for item in step.get("absent", ()) if isinstance(item, str))
            present.update(item for item in step.get("present", ()) if isinstance(item, str))
    for item in sorted(absent - present):
        problems.append("%r must be absent somewhere, but no probe expects it present for a client that may "
                        "see it" % item)
    return problems


def _validate_setup(setup):
    if not isinstance(setup, dict):
        return ["setup must be an object"]
    problems = []
    unknown = set(setup) - {"people", "stores", "tenants", "clients"}
    if unknown:
        problems.append("setup: unknown key %s" % ", ".join(sorted(unknown)))
    for section, allowed in (("stores", {"audience", "file"}), ("tenants", {"audience", "ids", "create"})):
        table = setup.get(section, {})
        if not isinstance(table, dict):
            problems.append("setup.%s must be an object" % section)
            continue
        for name, spec in table.items():
            if not isinstance(spec, dict) or set(spec) - allowed:
                problems.append("setup.%s.%s must be an object with only %s" % (section, name, ", ".join(sorted(allowed))))
            elif spec.get("audience") not in (None,) + AUDIENCES:
                problems.append("setup.%s.%s.audience must be one of %s" % (section, name, ", ".join(AUDIENCES)))
            elif "ids" in spec and not _strings(spec["ids"]):
                problems.append("setup.tenants.%s.ids must be a list of tenant ids" % name)
    if not isinstance(setup.get("clients"), dict) or not setup["clients"]:
        problems.append("setup.clients must name at least one client")
    elif any(not isinstance(spec, dict) or "token_env" in spec for spec in setup["clients"].values()):
        problems.append("setup.clients: each client is an object of policy keys, and the runner sets token_env")
    if "people" in setup and not isinstance(setup["people"], dict):
        problems.append("setup.people must be an object")
    return problems


def _validate_step(step):
    if not isinstance(step, dict) or step.get("do") not in STEP_KEYS:
        return ["do must be one of %s" % ", ".join(STEP_KEYS)]
    allowed, required = STEP_KEYS[step["do"]]
    problems = []
    if set(step) - allowed:
        problems.append("unknown key %s" % ", ".join(sorted(set(step) - allowed)))
    for key in sorted(required - set(step)):
        problems.append("%s needs %s" % (step["do"], key))
    kind = step["do"]
    if "why" in step and (not _text(step["why"]) or CJK.search(step["why"]) is None):
        problems.append("why must be a short Chinese text")
    if kind == "remember" and "status" in step and "why" not in step:
        problems.append("a remember that expects a status is a probe: it needs why")
    if kind == "reconfigure":
        if not isinstance(step.get("patch"), dict):
            problems.append("patch must be an object")
        if "refuse" in step and (not _text(step["refuse"]) or "why" not in step):
            problems.append("refuse must be a text the error message holds, and the step needs why")
    if kind == "restamp":
        if step.get("mode") not in ("missing", "all"):
            problems.append("mode must be missing or all")
        if "changed" in step and (isinstance(step["changed"], bool) or not isinstance(step["changed"], int)
                                  or "why" not in step):
            problems.append("changed must be a whole number, and the step needs why")
    if kind == "put_raw" and step.get("how") not in ("direct", "legacy"):
        problems.append("how must be direct or legacy")
    if kind == "recall":
        if step.get("mode", "recall") not in ("recall", "explain"):
            problems.append("mode must be recall or explain")
        if not any(key in step for key in EXPECTATIONS):
            problems.append("a recall must state what must hold: one of %s" % ", ".join(EXPECTATIONS))
        for key in ("absent", "present", "never"):
            if key in step and not _strings(step[key]):
                problems.append("%s must be a list of texts" % key)
        for key in ("fields", "candidate_fields"):
            if key in step and not (isinstance(step[key], dict) and all(isinstance(v, dict) for v in step[key].values())):
                problems.append("%s must map a text fragment to an object of fields" % key)
    if kind == "files":
        for key in ("lack", "have"):
            if key in step and not _strings(step[key]):
                problems.append("%s must be a list of texts" % key)
        if "lack" not in step and "have" not in step:
            problems.append("files needs lack or have")
    if kind == "forget_restore" and not _strings(step.get("texts")):
        problems.append("texts must be a list of the memories to restore")
    if kind == "codes":
        where = step.get("where")
        if not isinstance(where, list) or len(where) < 2 or not all(
                isinstance(item, dict) and _text(item.get("store")) for item in where):
            problems.append("where must list at least two stores ({store, tenant_id?})")
        if step.get("expect") not in ("all_same", "all_different"):
            problems.append("expect must be all_same or all_different")
    return problems


# ------------------------------------------------------------------------------ the configuration


def merge(base, patch):
    """`base` with the JSON merge patch `patch` applied (an object merges, null deletes, anything else
    replaces); neither argument is changed."""
    if not isinstance(patch, dict):
        return json.loads(json.dumps(patch))
    out = json.loads(json.dumps(base)) if isinstance(base, dict) else {}
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = merge(out.get(key), value)
    return out


def token_env(name):
    return "SCN_TOKEN_" + name.upper()


def make_config(setup, data):
    """(configuration dict, environment) for a setup; the store files live under the folder `data`."""
    cfg = {"stores": {}, "clients": {}}
    env = {"SCN_IDENTITY_KEY": IDENTITY_KEY}
    for name, spec in setup.get("stores", {}).items():
        entry = {"type": "sqlite", "path": store_path(data, spec.get("file", name))}
        if "audience" in spec:
            entry["audience"] = spec["audience"]
        cfg["stores"][name] = entry
    for name, spec in setup.get("tenants", {}).items():
        entry = {"type": "directory", "path": os.path.join(data, "tenants", name).replace("\\", "/"),
                 "id_pattern": "[0-9]{5,12}", "create": spec.get("create", False)}
        if "audience" in spec:
            entry["audience"] = spec["audience"]
        cfg.setdefault("tenants", {})[name] = entry
    for name, spec in setup["clients"].items():
        entry = dict(spec, token_env=token_env(name))
        entry.setdefault("rate_limit", 100000)
        if entry.get("write") is not None:
            entry.setdefault("daily_writes", 100000)
        cfg["clients"][name] = entry
        env[token_env(name)] = ("scenario-token-%s-" % name).ljust(40, "0")
    if setup.get("people"):
        cfg["identity"] = {"type": "table", "hash_key_env": "SCN_IDENTITY_KEY", "people": setup["people"]}
    return cfg, env


def store_path(data, file):
    return os.path.join(data, "stores", file + ".db").replace("\\", "/")


_BARE = re.compile(r"[A-Za-z0-9_-]+")


def _toml_key(key):
    return key if _BARE.fullmatch(key) else json.dumps(key)


def _toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return "{" + ", ".join("%s = %s" % (_toml_key(k), _toml_value(v)) for k, v in value.items()) + "}"


def to_toml(cfg):
    """The configuration dict written as TOML (tables, strings, numbers, booleans and lists)."""
    lines = []

    def table(path, values):
        plain = {k: v for k, v in values.items() if not isinstance(v, dict)}
        if path and (plain or not any(isinstance(v, dict) for v in values.values())):
            lines.append("")
            lines.append("[%s]" % ".".join(_toml_key(part) for part in path))
        for key, value in plain.items():
            lines.append("%s = %s" % (_toml_key(key), _toml_value(value)))
        for key, value in values.items():
            if isinstance(value, dict):
                table(path + [key], value)

    table([], cfg)
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------ the run


class Clock:
    """A settable wall clock for the forgetting service."""

    def __init__(self):
        self.now = 1_700_000_000.0

    def __call__(self):
        return self.now


class Run:
    """One scenario, executed in a temporary folder."""

    def __init__(self, scenario, folder):
        self.scenario = scenario
        self.folder = folder
        self.data = os.path.join(folder, "data").replace("\\", "/")
        self.setup = json.loads(json.dumps(scenario["setup"]))
        self.gateway = None
        self.env = {}
        self.cfg = {}
        self.probes = 0
        self.failed = []

    # -- bookkeeping

    def note(self, number, step, ok, detail=""):
        """Count a step that asserts (or any step that failed) and keep the failure."""
        if ok and "why" not in step:
            return
        self.probes += 1
        if not ok:
            self.failed.append({"step": number, "do": step["do"], "why": step.get("why", "（准备步骤失败）"),
                                "detail": detail})

    # -- the gateway

    def build(self, setup=None):
        cfg, env = make_config(self.setup if setup is None else setup, self.data)
        gateway = config.build_gateway(cfg, env=env, log=lambda line: None)
        self.gateway, self.cfg, self.env = gateway, cfg, env
        try:
            for name, spec in (self.setup if setup is None else setup).get("tenants", {}).items():
                for tenant_id in spec.get("ids", ()):
                    gateway.tenants[name].add(tenant_id)
        except Exception:
            self.stop()
            raise
        return gateway

    def stop(self):
        if self.gateway is not None:
            gateway, self.gateway = self.gateway, None
            gateway.close()

    def restart(self):
        self.stop()
        self.build()

    # -- locations

    def backend(self, step):
        name, tenant_id = step["store"], step.get("tenant_id")
        if tenant_id is None:
            return self.gateway.stores[name]
        return self.gateway.tenant_store(name, tenant_id)

    def path_of(self, step):
        name, tenant_id = step["store"], step.get("tenant_id")
        if tenant_id is None:
            return self.cfg["stores"][name]["path"]
        return os.path.join(self.cfg["tenants"][name]["path"], tenant_id + ".db")

    # -- steps

    def execute(self, number, step):
        getattr(self, "do_" + step["do"])(number, step)

    def message(self, step):
        message = {"role": step.get("role", "user"), "content": step["text"]}
        if "as" in step:
            message["speaker"] = step["as"]
        message.update(step.get("keys", {}))
        return message

    def request(self, step, body):
        if step.get("tenant_id") is not None:
            body["tenant_id"] = step["tenant_id"]
        if "as" in step and step["do"] == "recall":
            body["speaker"] = step["as"]
        return body

    def do_remember(self, number, step):
        body = self.request(step, {"messages": [self.message(step)]})
        reply = self.gateway.admit(step["client"], "remember", None)
        if reply is None:
            reply = self.gateway.remember(step["client"], body)
        wanted = step.get("status", 200)
        self.note(number, step, reply.status == wanted, "answer %s, expected %s" % (reply.status, wanted))

    def do_recall(self, number, step):
        explain = step.get("mode", "recall") == "explain"
        body = self.request(step, {"query": step["query"]})
        if "limit" in step:
            body["limit"] = step["limit"]
        action = "explain" if explain else "recall"
        reply = self.gateway.admit(step["client"], action, None)
        if reply is None:
            call = self.gateway.explain if explain else self.gateway.recall
            reply = call(step["client"], body)
        problems = check_answer(step, reply)
        self.note(number, step, not problems, "; ".join(problems))

    def do_put_raw(self, number, step):
        if step["how"] == "direct":
            self.backend(step).put(step["text"])
        else:
            self.legacy_row(self.path_of(step), step["text"])

    @staticmethod
    def legacy_row(path, text):
        """A row as the versions before record fields wrote it: the four columns and the gram index."""
        db = sqlite3.connect(path, timeout=10.0)
        try:
            ref = "legacy-%d" % db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            db.execute("INSERT OR REPLACE INTO memories (ref, text, created_at, date) VALUES (?, ?, ?, ?)",
                       (ref, text, 1000, None))
            db.executemany("INSERT OR IGNORE INTO grams (gram, ref) VALUES (?, ?)",
                           [(gram, ref) for gram in index_grams(normalize(text), True)])
            db.commit()
        finally:
            db.close()

    def do_copy_store(self, number, step):
        source = self.cfg["stores"][step["from"]]["path"]
        self.stop()
        target = store_path(self.data, step["to"])
        shutil.copyfile(source, target)
        self.build()

    def do_reconfigure(self, number, step):
        changed = merge(self.setup, step["patch"])
        self.stop()
        refused = None
        try:
            self.build(changed)
        except ConfigError as error:
            refused = str(error)
        if "refuse" in step:
            if refused is None:
                self.note(number, step, False, "the gateway started with the new setup")
            else:
                self.note(number, step, step["refuse"] in refused, "refused, but the message lacks %r" % step["refuse"])
        elif refused is not None:
            self.note(number, step, False, "the new setup was refused: %s" % refused)
        if "refuse" not in step and refused is None:
            self.setup = changed
        self.restart()

    def do_restamp(self, number, step):
        self.stop()
        path = os.path.join(self.folder, "conf", "gateway.toml")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(to_toml(self.cfg))
        argv = ["restamp", "--config", path, "--store", step["store"]]
        if step.get("tenant_id") is not None:
            argv += ["--tenant", step["tenant_id"]]
        argv.append("--missing" if step["mode"] == "missing" else "--all")
        out = io.StringIO()
        path_before = list(sys.path)
        try:
            with mock.patch.dict(os.environ, self.env), contextlib.redirect_stdout(out):
                code = cli.main(argv)
        finally:
            sys.path[:] = path_before
        self.restart()
        found = re.search(r"里 (\d+) 条", out.getvalue())
        if code != 0 or found is None:
            self.note(number, step, False, "restamp exited with %s" % code)
        elif "changed" in step:
            self.note(number, step, int(found.group(1)) == step["changed"],
                      "%s records changed, expected %s" % (found.group(1), step["changed"]))

    def do_forget_restore(self, number, step):
        store = self.backend(step)
        key = step["store"] if step.get("tenant_id") is None else "%s:%s" % (step["store"], step["tenant_id"])
        refs = {record["text"]: record["ref"] for record in store.records()}
        missing = [text for text in step["texts"] if text not in refs]
        if missing:
            self.note(number, step, False, "not stored before forgetting: %s" % missing[0])
            return
        clock = Clock()
        engine = Forgetting(Ledger(":memory:"), Archive(":memory:"), Ebbinghaus(half_life_days=1), stores=(key,),
                            max_forget_fraction=1.0, audiences=self.gateway.audiences, wall=clock)
        try:
            engine.sweep({key: store})  # the first sweep only starts the management of what is there
            clock.now += 3650 * DAY
            engine.sweep({key: store}, force=True)
            still = [text for text in step["texts"] if any(r["text"] == text for r in store.records())]
            restored = [engine.restore(key, refs[text], store) for text in step["texts"]]
        finally:
            engine.close()
        ok = not still and all(restored)
        self.note(number, step, ok, "forgotten: %s, restored: %s" % (not still, all(restored)))

    def do_files(self, number, step):
        self.stop()  # closing the stores puts everything into the files
        blobs = []
        for folder, _, names in os.walk(self.data):
            for name in sorted(names):
                with open(os.path.join(folder, name), "rb") as f:
                    blobs.append((name, f.read()))
        self.build()
        problems = []
        for item in step.get("lack", ()):
            problems.extend("%r is in %s" % (item, name) for name, blob in blobs if item.encode("utf-8") in blob)
        for item in step.get("have", ()):
            if not any(item.encode("utf-8") in blob for _, blob in blobs):
                problems.append("%r is in no file" % item)
        self.note(number, step, not problems, "; ".join(problems))

    def do_codes(self, number, step):
        per_store = []
        for place in step["where"]:
            store = self.backend(place)
            speakers = {r["fields"].get("speaker") for r in store.records()}
            per_store.append({s for s in speakers if isinstance(s, str) and s.startswith("anon:")})
        problems = []
        if any(len(codes) != 1 for codes in per_store):
            problems.append("each store should hold exactly one stand-in code, found %s" % [len(c) for c in per_store])
        else:
            distinct = len({next(iter(codes)) for codes in per_store})
            if step["expect"] == "all_different" and distinct != len(per_store):
                problems.append("the stores share a stand-in code")
            if step["expect"] == "all_same" and distinct != 1:
                problems.append("the stores hold different codes")
        self.note(number, step, not problems, "; ".join(problems))


def check_answer(step, reply):
    """The sentences that say how a reply breaks what the step states (empty when it holds)."""
    problems = []
    wanted = step.get("status", 200)
    if reply.status != wanted:
        problems.append("answer %s, expected %s" % (reply.status, wanted))
    body = reply.body if isinstance(reply.body, dict) else {}
    dump = json.dumps(body, ensure_ascii=False, sort_keys=True)
    results = body.get("results") or []
    texts = [item.get("text", "") for item in results if isinstance(item, dict)]
    for item in step.get("absent", ()):
        if item in dump:
            problems.append("%r reached the answer" % item)
    for item in step.get("never", ()):
        if item in dump:
            problems.append("%r occurs in the answer" % item)
    for item in step.get("present", ()):
        if not any(item in text for text in texts):
            problems.append("%r is missing from the answer" % item)
    for fragment, wanted_fields in step.get("fields", {}).items():
        rows = [item for item in results if isinstance(item, dict) and fragment in item.get("text", "")]
        if not rows:
            problems.append("no result holds %r to look at its fields" % fragment)
        elif rows[0].get("fields") != wanted_fields:
            problems.append("fields of %r are %s" % (fragment, rows[0].get("fields")))
    for fragment, wanted_fields in step.get("candidate_fields", {}).items():
        rows = [item for item in body.get("candidates") or [] if isinstance(item, dict)
                and fragment in item.get("text", "")]
        if not rows:
            problems.append("no candidate holds %r to look at its fields" % fragment)
        else:
            have = rows[0].get("fields") or {}
            if any(have.get(key) != value for key, value in wanted_fields.items()):
                problems.append("candidate fields of %r are %s" % (fragment, have))
    return problems


@contextlib.contextmanager
def counted_ids():
    """uuid.uuid4 as a counter for the length of a run: the refs the stores give to memories (and so the bytes of
    their files) are then the same in every run."""
    counter = itertools.count(1)
    lock = threading.Lock()

    def fake():
        with lock:
            number = next(counter)
        return uuid.UUID(int=number)

    with mock.patch.object(uuid, "uuid4", fake):
        yield


def run_scenario(scenario):
    """Execute one scenario: {"id", "title", "checks", "probes", "failed": [{"step", "do", "why", "detail"}]}.
    A step that raises is a failed probe, and the run goes on."""
    with counted_ids(), tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        run = Run(scenario, folder)
        try:
            run.build()
            for number, step in enumerate(scenario["steps"], 1):
                try:
                    run.execute(number, step)
                except Exception as error:  # noqa: BLE001 - a step that raises is a failed probe, the run goes on
                    run.note(number, dict(step, why=step.get("why", "（准备步骤失败）")), False,
                             "%s: %s" % (type(error).__name__, error))
                    if run.gateway is None:
                        with contextlib.suppress(Exception):
                            run.build()
        finally:
            run.stop()
    return {"id": scenario["id"], "title": scenario["title"], "checks": scenario["checks"], "probes": run.probes,
            "failed": run.failed}


# ------------------------------------------------------------------------------ the command


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the audience scenarios.")
    parser.add_argument("ids", nargs="*", help="scenario ids (default: all)")
    parser.add_argument("--dir", default=SCENARIOS, help="folder of scenario files")
    parser.add_argument("--json", metavar="FILE", help="write the results here")
    parser.add_argument("--list", action="store_true", help="print the scenario ids and stop")
    args = parser.parse_args(argv)
    found = load_all(args.dir)
    if args.list:
        for _, scenario in found:
            print(scenario["id"])
        return 0
    bad = 0
    for path, scenario in found:
        for problem in validate(scenario, os.path.splitext(os.path.basename(path))[0]):
            print("MALFORMED %s: %s" % (os.path.basename(path), problem))
            bad += 1
    if bad:
        return 1
    chosen = [scenario for _, scenario in found if not args.ids or scenario["id"] in args.ids]
    unknown = sorted(set(args.ids) - {scenario["id"] for scenario in chosen})
    if unknown:
        print("no such scenario: %s" % ", ".join(unknown))
        return 2
    results = [run_scenario(scenario) for scenario in chosen]
    for result in results:
        for failure in result["failed"]:
            print("FAIL %s step %d (%s): %s [%s]" % (result["id"], failure["step"], failure["do"], failure["why"],
                                                     failure["detail"]))
    width = max([len(result["id"]) for result in results] + [8])
    print("%-*s  %6s  %6s" % (width, "scenario", "probes", "failed"))
    for result in results:
        print("%-*s  %6d  %6d" % (width, result["id"], result["probes"], len(result["failed"])))
    total = sum(result["probes"] for result in results)
    failed = sum(len(result["failed"]) for result in results)
    print("%-*s  %6d  %6d" % (width, "total", total, failed))
    if args.json:
        with open(args.json, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"scenarios": results, "probes": total, "failed": failed}, f, ensure_ascii=False, indent=2)
            f.write("\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
