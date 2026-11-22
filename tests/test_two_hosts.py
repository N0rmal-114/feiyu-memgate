"""Two hosts on one gateway: the configuration of examples/two-hosts, used as it is written.

DSH and a QQ bot (AstrBot's aiocqhttp) talk to one gateway. The identity table knows the owner by two accounts,
so what either host stores is the owner's, whichever host wrote it; the group client keeps what is said in a
group in that group's own store. What must hold is written by hand below (the people, the accounts, the
expected statuses), and the stores are real SQLite files that the last test reads byte by byte.
"""
import logging
import tempfile
import unittest
from pathlib import Path

from feiyu_memgate import config
from feiyu_memgate.client import LocalClient
from tests.test_examples import ROOT, _move_paths, keep_sys_path

EXAMPLE = ROOT / "examples" / "two-hosts" / "gateway.toml"
OWNER_DSH, OWNER_QQ = "dsh:local", "aiocqhttp:10001"
STRANGER = "aiocqhttp:30003"            # in no one's list
STRANGER_ID = "30003"
GROUP_A, GROUP_B = "10001", "20002"     # two registered groups
TEA = "owner likes oolong tea after dinner"
BOOKS = "owner reads detective novels on weekends"
CHAT = "the book club meets on friday"
LINE = "zebra quartz marker about the harbour lantern"


def tokens(cfg):
    env = {client["token_env"]: "two-hosts-token-%d-" % i + "x" * 32
           for i, client in enumerate(cfg["clients"].values())}
    env["GATEWAY_IDENTITY_KEY"] = "two-hosts-identity-key-" + "k" * 32
    return env


class TwoHostsCase(unittest.TestCase):
    def setUp(self):
        keep_sys_path(self)
        logging.getLogger("feiyu_memgate.client").addHandler(logging.NullHandler())  # refusals are expected here
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        cfg = config.load(EXAMPLE)
        for section in ("stores", "tenants"):
            for name, spec in cfg[section].items():
                cfg[section][name] = _move_paths(spec, str(self.tmp))
        env = tokens(cfg)
        self.assertEqual(sorted(cfg["clients"]), ["chat_group", "chat_owner", "dsh"])
        self.dsh = LocalClient.from_config(cfg, client="dsh", env=env, log=lambda line: None)
        self.closed = False
        self.addCleanup(self.close)
        self.gw = self.dsh.gateway
        for group in (GROUP_A, GROUP_B):  # create = false: a group has a store only when it was registered
            self.gw.tenants["group"].add(group)
        self.chat = self.dsh.for_client("chat_owner")
        self.group = self.dsh.for_client(None, "chat_group")

    def close(self):
        if not self.closed:
            self.closed = True
            self.dsh.close()

    @property
    def owner_store(self):
        return self.gw.stores["private"]

    def group_store(self, group):
        return self.gw.tenants["group"].store(group)

    @staticmethod
    def texts(results):
        return [r["text"] for r in results]

    @staticmethod
    def speakers(store):
        return sorted(record["fields"].get("speaker") for record in store.records())

    def ask(self, name, body, tenant_id=None):
        """The gateway's own answer to a recall (status and body), past the client's soft failures."""
        body = dict(body, query=body.get("query", "oolong tea dinner"), limit=5)
        if tenant_id is not None:
            body["tenant_id"] = tenant_id
        return self.gw.recall(name, body)

    def tell(self, name, speaker, text, tenant_id=None, **message):
        body = {"messages": [dict({"role": "user", "content": text, "speaker": speaker}, **message)]}
        if tenant_id is not None:
            body["tenant_id"] = tenant_id
        return self.gw.remember(name, body)


class OneOwnerTwoHosts(TwoHostsCase):
    def test_what_either_host_stores_is_the_owners_and_either_host_recalls_it(self):
        self.assertEqual(self.dsh.remember_status(TEA, speaker=OWNER_DSH), "saved")
        self.assertEqual(self.chat.remember_status(BOOKS, speaker=OWNER_QQ), "saved")
        # both records belong to the owner, written by whichever host
        self.assertEqual(self.speakers(self.owner_store), ["owner", "owner"])
        # the chat client recalls what DSH stored, and DSH what the chat stored
        from_chat = self.chat.recall("oolong tea after dinner", speaker=OWNER_QQ)
        self.assertEqual(self.texts(from_chat), [TEA])
        self.assertEqual(from_chat[0]["fields"], {"speaker": "owner"})
        from_dsh = self.dsh.recall("detective novels weekends", speaker=OWNER_DSH)
        self.assertEqual(self.texts(from_dsh), [BOOKS])
        self.assertEqual(from_dsh[0]["fields"], {"speaker": "owner"})
        # a recall names the person too: the same answer from the two accounts of the owner
        self.assertEqual(self.texts(self.dsh.recall("oolong tea after dinner", speaker=OWNER_DSH)),
                         self.texts(from_chat))

    def test_the_owner_memory_never_reaches_the_group_client(self):
        self.dsh.remember_status(TEA, speaker=OWNER_DSH)
        self.chat.remember_status(BOOKS, speaker=OWNER_QQ)
        # a control: the group's own memory is found by the same client, so an empty answer means something
        self.assertEqual(self.group.remember_status(CHAT, tenant_id=GROUP_A, speaker=STRANGER), "saved")
        self.assertEqual(self.texts(self.group.recall("book club friday", tenant_id=GROUP_A, speaker=STRANGER)), [CHAT])
        for group in (GROUP_A, GROUP_B):
            for speaker in (OWNER_QQ, STRANGER, "aiocqhttp:20002", None):
                for query in ("oolong tea after dinner", "detective novels weekends", "owner"):
                    reply = self.ask("chat_group", {"query": query, "speaker": speaker}
                                     if speaker else {"query": query}, group)
                    self.assertEqual(reply.status, 200, (group, speaker))
                    self.assertEqual(reply.body["results"], [], (group, speaker, query))
                    detail = self.group.recall_detail(query, tenant_id=group, speaker=speaker)
                    self.assertEqual(detail["results"], [])

    def test_a_stranger_is_refused_by_the_owners_chat_client_and_nothing_is_stored(self):
        self.dsh.remember_status(TEA, speaker=OWNER_DSH)
        before = len(list(self.owner_store.records()))
        self.assertEqual(self.ask("chat_owner", {"speaker": STRANGER}).status, 403)
        self.assertEqual(self.tell("chat_owner", STRANGER, LINE).status, 403)
        self.assertIsNone(self.chat.recall_detail("oolong tea after dinner", speaker=STRANGER))
        self.assertEqual(self.chat.remember_status(LINE, speaker=STRANGER), "failed")
        # a request that names no speaker at all is refused as well: the client speaks only for the owner
        self.assertEqual(self.ask("chat_owner", {}).status, 403)
        self.assertEqual(self.tell("chat_owner", None, LINE).status, 403)
        records = list(self.owner_store.records())
        self.assertEqual(len(records), before)
        self.assertNotIn(LINE, [record["text"] for record in records])

    def test_a_host_may_name_only_its_own_platform(self):
        self.assertEqual(self.ask("dsh", {"speaker": OWNER_QQ}).status, 403)
        self.assertEqual(self.tell("dsh", OWNER_QQ, LINE).status, 403)
        self.assertEqual(self.ask("chat_owner", {"speaker": OWNER_DSH}).status, 403)
        self.assertEqual(self.tell("chat_owner", OWNER_DSH, LINE).status, 403)
        self.assertEqual(self.dsh.remember_status(LINE, speaker=OWNER_QQ), "failed")
        self.assertEqual(self.chat.remember_status(LINE, speaker=OWNER_DSH), "failed")
        self.assertEqual(list(self.owner_store.records()), [])
        # and each is let in with its own account
        self.assertEqual(self.ask("dsh", {"speaker": OWNER_DSH}).status, 200)
        self.assertEqual(self.ask("chat_owner", {"speaker": OWNER_QQ}).status, 200)


class GroupSpeakers(TwoHostsCase):
    def test_a_member_nobody_listed_gets_a_stand_in_code_that_depends_on_the_group(self):
        for group in (GROUP_A, GROUP_A, GROUP_B):
            self.assertEqual(self.group.remember_status(CHAT + " " + group, tenant_id=group, speaker=STRANGER,
                                                        origin="said"), "saved")
        in_a, in_b = self.speakers(self.group_store(GROUP_A)), self.speakers(self.group_store(GROUP_B))
        self.assertEqual(len(in_a), 2)
        self.assertEqual(in_a[0], in_a[1])                  # the same member in the same group: the same code
        self.assertEqual(len(in_b), 1)
        self.assertNotEqual(in_a[0], in_b[0])               # the same member in another group: another code
        for code in (in_a[0], in_b[0]):
            self.assertRegex(code, r"anon:[0-9a-f]{16}")
        self.assertEqual(self.speakers(self.owner_store), [])

    def test_the_raw_account_is_in_neither_store_file(self):
        for group in (GROUP_A, GROUP_B):
            self.assertEqual(self.group.remember_status(CHAT, tenant_id=group, speaker=STRANGER), "saved")
            self.assertEqual(self.group.remember_status(CHAT + " again", tenant_id=group, speaker=STRANGER),
                             "saved")
        self.assertEqual(self.dsh.remember_status(TEA, speaker=OWNER_DSH), "saved")
        self.close()  # the stores are closed: what was written is in the files
        files = [path for path in self.tmp.rglob("*") if path.is_file()]
        self.assertTrue(any(path.name == GROUP_A + ".db" for path in files))
        self.assertTrue(any(path.name == "private.db" for path in files))
        for path in files:
            data = path.read_bytes()
            self.assertNotIn(STRANGER.encode("utf-8"), data, path.name)
            self.assertNotIn(STRANGER_ID.encode("utf-8"), data, path.name)
        group_bytes = (self.tmp / "groups" / (GROUP_A + ".db")).read_bytes()
        self.assertIn(b"anon:", group_bytes)

    def test_the_owner_speaking_in_a_group_is_recorded_there_as_the_owner_and_stays_there(self):
        self.assertEqual(self.group.remember_status(BOOKS, tenant_id=GROUP_A, speaker=OWNER_QQ), "saved")
        self.assertEqual(self.speakers(self.group_store(GROUP_A)), ["owner"])
        (record,) = list(self.group_store(GROUP_A).records())
        self.assertEqual(record["fields"].get("audience"), "group")   # group-level: shown to the group, no wider
        # the group's client reads it back, in that group only
        self.assertEqual(self.texts(self.group.recall("detective novels weekends", tenant_id=GROUP_A,
                                                      speaker=OWNER_QQ)), [BOOKS])
        self.assertEqual(self.group.recall("detective novels weekends", tenant_id=GROUP_B, speaker=OWNER_QQ), [])
        # and the owner's own store never sees it, nor do the owner's clients find it
        self.assertEqual(list(self.owner_store.records()), [])
        self.assertEqual(self.dsh.recall("detective novels weekends", speaker=OWNER_DSH), [])
        self.assertEqual(self.chat.recall("detective novels weekends", speaker=OWNER_QQ), [])


if __name__ == "__main__":
    unittest.main()
