"""The gateway knows people: one table of people and their accounts, resolved before any guard or store.

A host reports who speaks as an account (`aiocqhttp:10001`). With an `[identity]` table the gateway turns it
into the person's name, or into a keyed stand-in code for an account nobody listed, and it refuses a client
that names accounts of a platform it does not own. What each rule must do is written by hand below (the
accounts, the codes computed with hmac in the test itself, the expected statuses), not by the code under test.
Without a table nothing changes: those tests hold literal expected dicts.
"""
import contextlib
import hashlib
import hmac
import http.client
import io
import json
import os
import threading
import unittest
from unittest import mock

from feiyu_memgate import cli, config, registry
from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.core.recall import KEEP, RecallStage
from feiyu_memgate.core.write import RedactGuard
from feiyu_memgate.errors import ConfigError
from feiyu_memgate.gateway import server as gateway_server
from feiyu_memgate.gateway.app import Gateway
from feiyu_memgate.identity import IdentityProvider, TableIdentity
from feiyu_memgate.model import ClientPolicy, platform_of, valid_account, valid_person, valid_platform
from tests.test_audience import GROUP_ID, MemTenants
from tests.test_request_fields import Look, Spy

KEY = "identity-key-for-tests-".ljust(40, "k")
OTHER_KEY = "another-identity-key-".ljust(40, "z")
OWNER_DSH, OWNER_QQ = "dsh:local", "aiocqhttp:10001"
AMY_QQ = "aiocqhttp:20002"
STRANGER, STRANGER2 = "aiocqhttp:30003", "aiocqhttp:30004"
ACCOUNTS = (OWNER_DSH, OWNER_QQ, AMY_QQ, STRANGER, STRANGER2)
SAID = "zebra quartz marker about oolong tea"
OTHER = "violet harbor lantern near the river"
PEOPLE = {"owner": {"owner": True, "accounts": [OWNER_DSH, OWNER_QQ]}, "amy": {"accounts": [AMY_QQ]}}
BOTH = ("dsh", "aiocqhttp")


def table(**options):
    options.setdefault("people", PEOPLE)
    options.setdefault("hash_key", KEY)
    return TableIdentity(**options)


def expected_code(account, scope, key=KEY):
    """The stand-in code, worked out here from the rule in the specification."""
    mac = hmac.new(key.encode("utf-8"), (scope + "\n" + account).encode("utf-8"), hashlib.sha256)
    return "anon:" + mac.hexdigest()[:16]


class Case(unittest.TestCase):
    def setUp(self):
        self.lines = []

    def gateway(self, stores, clients, identity=None, **options):
        tokens = {name: ("tok-id-%s-" % name).ljust(40, "0") for name in clients}
        gw = Gateway(stores, clients, tokens, identity=identity, log=self.lines.append, **options)
        self.addCleanup(gw.close)
        return gw

    @staticmethod
    def say(gw, client, *messages, **body):
        return gw.remember(client, dict(body, messages=[dict({"role": "user", "content": SAID}, **m)
                                                         for m in messages]))

    @staticmethod
    def fields_of(store):
        return [record["fields"] for record in store.records()]

    def assertNoAccount(self, *things):
        text = " ".join(str(thing) for thing in things)
        for account in ACCOUNTS:
            self.assertNotIn(account, text)
            self.assertNotIn(account.partition(":")[2], text)
        self.assertNotIn(KEY, text)


# ---------------------------------------------------------------- accounts, people

class ShapeTests(unittest.TestCase):
    def test_accounts(self):
        good = ("dsh:local", "aiocqhttp:10001", "a:b", "0x:1", "p.q-r_s:id:with:colons", "tg:" + "9" * 61,
                "dsh:名字", "x" * 32 + ":1")
        bad = ("", "dsh", "dsh:", ":local", "DSH:local", "-a:1", "_a:1", "a b:1", "dsh:a b", "dsh:a\tb", "dsh:a\n",
               "dsh:a\x00b", "dsh:a\x7fb", "dsh:a b", "dsh:a　b", "x" * 33 + ":1", "tg:" + "9" * 62,
               5, None, ["a:b"], b"a:b", "dsh：local")
        for value in good:
            with self.subTest(good=value):
                self.assertTrue(valid_account(value))
        for value in bad:
            with self.subTest(bad=value):
                self.assertFalse(valid_account(value))
        self.assertEqual(len("tg:" + "9" * 61), 64)

    def test_platform_of_is_the_text_before_the_first_colon(self):
        self.assertEqual(platform_of("aiocqhttp:10001"), "aiocqhttp")
        self.assertEqual(platform_of("a:b:c"), "a")

    def test_platforms_and_people(self):
        for value in ("a", "dsh", "a.b-c_d", "0", "x" * 32):
            self.assertTrue(valid_platform(value), value)
        for value in ("", "A", "-a", "a b", "a:b", "x" * 33, 5, None):
            self.assertFalse(valid_platform(value), value)
        for value in ("owner", "amy", "a-b_c", "0", "x" * 32):
            self.assertTrue(valid_person(value), value)
        for value in ("", "Amy", "-a", "a.b", "a b", "a:b", "x" * 33, "self", "anon", 5, None):
            self.assertFalse(valid_person(value), value)


class TableTests(unittest.TestCase):
    def refused(self, needle, **options):
        with self.assertRaises(ConfigError) as caught:
            table(**options)
        message = str(caught.exception)
        self.assertIn(needle, message)
        for account in ACCOUNTS + ("dsh:a b", "dsh:", "NoPlatform"):
            self.assertNotIn(account, message)
        self.assertNotIn(KEY, message)
        return message

    @staticmethod
    def people(**changes):
        people = {name: dict(spec, accounts=list(spec["accounts"])) for name, spec in PEOPLE.items()}
        people.update(changes)
        return people

    def test_a_name_that_does_not_match(self):
        for name in ("Amy", "-amy", "a b", "a:b", "aiocqhttp:10001", "x" * 33, ""):
            with self.subTest(name=name):
                self.refused("identity.people (person 3): a name is", people=self.people(**{name: {"accounts": []}}))

    def test_a_reserved_name(self):
        for name in ("self", "anon"):
            with self.subTest(name=name):
                self.refused("identity.people.%s: the names self and anon are reserved" % name,
                             people=self.people(**{name: {"accounts": []}}))

    def test_an_account_that_does_not_match(self):
        for account in ("NoPlatform", "dsh:", "dsh:a b", "dsh:a\x01", "tg:" + "9" * 62, "UP:1", "", 5, None):
            with self.subTest(account=account):
                self.refused("identity.people.amy.accounts: entry 2 is not an account",
                             people=self.people(amy={"accounts": [AMY_QQ, account]}))

    def test_an_account_listed_for_two_people_or_twice(self):
        self.refused("an account is listed for both owner and amy",
                     people=self.people(amy={"accounts": [OWNER_QQ]}))
        self.refused("an account is listed twice for amy", people=self.people(amy={"accounts": [AMY_QQ, AMY_QQ]}))

    def test_exactly_one_owner(self):
        self.refused("exactly one person must have owner = true (found 0)",
                     people={"amy": {"accounts": [AMY_QQ]}})
        self.refused("exactly one person must have owner = true (found 2)",
                     people=self.people(amy={"owner": True, "accounts": [AMY_QQ]}))
        self.refused("exactly one person must have owner = true (found 0)",
                     people=self.people(owner={"owner": False, "accounts": [OWNER_DSH]}))

    def test_the_shape_of_the_table(self):
        self.refused("identity.people must list the people", people={})
        self.refused("identity.people must list the people", people=None)
        self.refused("identity.people must list the people", people=[OWNER_DSH])
        self.refused("[identity.people.amy] must be a table", people=self.people(amy=OWNER_QQ))
        self.refused("identity.people.amy: unknown setting mail (known: owner, accounts)",
                     people=self.people(amy={"accounts": [], "mail": "x"}))
        self.refused("identity.people.amy.owner must be true or false",
                     people=self.people(amy={"owner": "yes", "accounts": []}))
        self.refused("identity.people.amy.accounts must be a list", people=self.people(amy={"accounts": AMY_QQ}))

    def test_unknown_settings_and_a_bad_key(self):
        self.refused("identity: unknown setting colour", colour="red")
        self.refused("identity.hash_key_env must name", hash_key=5)
        self.refused("identity.hash_key_env must name", hash_key="")

    def test_a_person_without_accounts_is_allowed(self):
        found = table(people=self.people(guest={"accounts": []}))
        self.assertEqual(found.people()["guest"], 0)

    def test_resolve_owner_people(self):
        found = table()
        self.assertEqual(found.resolve(OWNER_DSH), "owner")
        self.assertEqual(found.resolve(OWNER_QQ), "owner")
        self.assertEqual(found.resolve(AMY_QQ), "amy")
        self.assertIsNone(found.resolve(STRANGER))
        for odd in ("", "owner", "AIOCQHTTP:10001", "aiocqhttp:10001 ", None, 5, ["x"], {}):
            self.assertIsNone(found.resolve(odd), odd)
        self.assertEqual(found.owner, "owner")
        self.assertEqual(found.people(), {"owner": 2, "amy": 1})
        self.assertEqual(list(found.people()), ["owner", "amy"])

    def test_people_holds_no_account(self):
        self.assertNoAccounts(str(table().people()))

    def assertNoAccounts(self, text):
        for account in ACCOUNTS:
            self.assertNotIn(account, text)

    def test_the_key_and_what_it_allows(self):
        self.assertTrue(table().has_key)
        self.assertTrue(table().ready)
        self.assertFalse(table(hash_key=None).has_key)
        self.assertFalse(table(hash_key=None).ready)
        self.assertTrue(table(hash_key="short").has_key)
        self.assertFalse(table(hash_key="short").ready)
        with self.assertRaises(ValueError):
            table(hash_key=None).code(STRANGER, "s")

    def test_it_is_a_registered_identity_provider(self):
        self.assertIn("identity", registry.KINDS)
        self.assertIs(registry.resolve("identity", "table"), TableIdentity)
        self.assertTrue(issubclass(TableIdentity, IdentityProvider))
        made = registry.create("identity", {"type": "table", "people": PEOPLE, "hash_key": KEY})
        self.assertEqual(made.owner, "owner")


class CodeTests(unittest.TestCase):
    def test_the_code_is_the_keyed_hash_of_scope_and_account(self):
        found = table()
        for account in (STRANGER, STRANGER2, "x:y"):
            for scope in ("s", "private", "group:10001"):
                with self.subTest(account=account, scope=scope):
                    self.assertEqual(found.code(account, scope), expected_code(account, scope))

    def test_same_input_same_code_and_anything_else_another(self):
        found = table()
        code = found.code(STRANGER, "s")
        self.assertEqual(found.code(STRANGER, "s"), code)
        self.assertEqual(table().code(STRANGER, "s"), code)
        self.assertNotEqual(found.code(STRANGER, "t"), code)
        self.assertNotEqual(found.code(STRANGER2, "s"), code)
        self.assertNotEqual(table(hash_key=OTHER_KEY).code(STRANGER, "s"), code)
        self.assertNotEqual(found.code("aiocqhttp:3000", "s3"), found.code("aiocqhttp:30003"[:-1], "s3" + "0"))

    def test_the_scope_and_account_are_framed(self):
        # "ab" + "\n" + "c:d" and "a" + "\n" + "bc:d" differ; so do the scopes "a" and "a\n"
        found = table()
        self.assertNotEqual(found.code("c:d", "ab"), found.code("bc:d", "a"))

    def test_the_shape(self):
        found = table()
        for account in ACCOUNTS:
            code = found.code(account, "s")
            self.assertRegex(code, r"\Aanon:[0-9a-f]{16}\Z")
            self.assertNotIn(account, code)
            self.assertNotIn(account.partition(":")[2], code)


# ---------------------------------------------------------------- remember

class RememberTests(Case):
    def setup(self, clients=None, identity=None, store=None, guards=None, **options):
        store = store if store is not None else Spy()
        clients = clients or {"c": ClientPolicy("c", write="s", recall=("s",), platforms=BOTH,
                                                fields=("speaker", "about"))}
        gw = self.gateway({"s": store}, clients, identity or table(), guards=guards, **options)
        return gw, store

    def test_a_listed_speaker_is_stored_as_the_persons_name(self):
        gw, store = self.setup()
        self.assertEqual(self.say(gw, "c", {"speaker": AMY_QQ}, {"speaker": OWNER_DSH, "content": OTHER}).status, 200)
        self.assertEqual([f["speaker"] for f in self.fields_of(store)], ["amy", "owner"])
        records = list(store.records())
        self.assertEqual(records[0]["fields"], {"audience": "owner", "speaker": "amy"})
        item = gw.recall("c", {"query": SAID}).body["results"][0]  # an owner client that lists the field
        self.assertEqual(item["fields"], {"speaker": "amy"})

    def test_one_person_two_accounts_one_name(self):
        gw, store = self.setup()
        self.say(gw, "c", {"speaker": OWNER_DSH}, {"speaker": OWNER_QQ, "content": OTHER})
        self.assertEqual([f["speaker"] for f in self.fields_of(store)], ["owner", "owner"])

    def test_an_unlisted_speaker_is_stored_as_its_code_for_that_store(self):
        gw, store = self.setup()
        self.assertEqual(self.say(gw, "c", {"speaker": STRANGER}, {"speaker": STRANGER2, "content": OTHER}).status,
                         200)
        self.assertEqual([f["speaker"] for f in self.fields_of(store)],
                         [expected_code(STRANGER, "s"), expected_code(STRANGER2, "s")])
        self.assertNotEqual(*[f["speaker"] for f in self.fields_of(store)])

    def test_the_same_account_has_a_different_code_in_two_stores(self):
        one, two = InMemoryBackend(), InMemoryBackend()
        gw = self.gateway({"one": one, "two": two},
                          {"a": ClientPolicy("a", write="one", platforms=BOTH),
                           "b": ClientPolicy("b", write="two", platforms=BOTH)}, table())
        self.say(gw, "a", {"speaker": STRANGER})
        self.say(gw, "b", {"speaker": STRANGER})
        first, second = self.fields_of(one)[0]["speaker"], self.fields_of(two)[0]["speaker"]
        self.assertEqual((first, second), (expected_code(STRANGER, "one"), expected_code(STRANGER, "two")))
        self.assertNotEqual(first, second)

    def test_a_tenant_store_scopes_by_provider_and_tenant(self):
        provider = MemTenants()
        provider.stores["10002"] = InMemoryBackend()
        gw = self.gateway({}, {"t": ClientPolicy("t", tenants="g", write="@tenant", public=True, audience="group",
                                                 platforms=BOTH)}, table(), tenants={"g": provider})
        self.assertEqual(self.say(gw, "t", {"speaker": STRANGER}, tenant_id=GROUP_ID).status, 200)
        self.assertEqual(self.say(gw, "t", {"speaker": STRANGER}, tenant_id="10002").status, 200)
        self.assertEqual(self.fields_of(provider.stores[GROUP_ID])[0]["speaker"], expected_code(STRANGER, "g:10001"))
        self.assertEqual(self.fields_of(provider.stores["10002"])[0]["speaker"], expected_code(STRANGER, "g:10002"))

    def test_the_platform_rule_for_speaker_and_about(self):
        cases = (("a client without platforms", ClientPolicy("c", write="s"), OWNER_DSH),
                 ("a platform of another client", ClientPolicy("c", write="s", platforms=("dsh",)), OWNER_QQ),
                 ("a platform nobody listed", ClientPolicy("c", write="s", platforms=BOTH), "tg:1"))
        for label, policy, account in cases:
            for key in ("speaker", "about"):
                with self.subTest(case=label, key=key):
                    gw, store = self.setup({"c": policy})
                    reply = self.say(gw, "c", {key: account})
                    self.assertEqual((reply.status, reply.body), (403, {"error": "forbidden", "code": "forbidden"}))
                    self.assertEqual(store.refs(), [])
        gw, store = self.setup({"c": ClientPolicy("c", write="s", platforms=("dsh",))})
        self.assertEqual(self.say(gw, "c", {"speaker": OWNER_DSH, "about": OWNER_DSH}).status, 200)

    def test_a_bad_account_is_a_bad_request(self):
        gw, store = self.setup()
        for key in ("speaker", "about"):
            for bad in ("amy", "aiocqhttp:", "AIOCQHTTP:1", "aiocqhttp:a b", 5, ["a:b"], "x" * 65):
                with self.subTest(key=key, bad=bad):
                    reply = self.say(gw, "c", {"content": "fine"}, {key: bad})
                    self.assertEqual((reply.status, reply.body["code"]), (400, "bad_messages"))
        self.assertEqual(store.refs(), [])

    def test_about_someone_else_is_for_an_owner_level_client_only(self):
        owner_level = ClientPolicy("c", write="s", platforms=BOTH, fields=("about",))
        group_level = ClientPolicy("c", write="s", platforms=BOTH, public=True, audience="group")
        gw, store = self.setup({"c": owner_level})
        self.assertEqual(self.say(gw, "c", {"speaker": AMY_QQ, "about": OWNER_DSH}).status, 200)
        self.assertEqual(self.say(gw, "c", {"speaker": AMY_QQ, "about": STRANGER}).status, 200)
        self.assertEqual(self.say(gw, "c", {"about": AMY_QQ}).status, 200)  # no speaker: someone else too
        self.assertEqual([f["about"] for f in self.fields_of(store)], ["owner", expected_code(STRANGER, "s"), "amy"])
        gw, store = self.setup({"c": group_level})
        for message in ({"speaker": AMY_QQ, "about": OWNER_DSH}, {"speaker": AMY_QQ, "about": STRANGER},
                        {"about": AMY_QQ}, {"role": "assistant", "about": AMY_QQ}):
            with self.subTest(message=message):
                reply = self.say(gw, "c", message)
                self.assertEqual((reply.status, reply.body["code"]), (403, "forbidden"))
        self.assertEqual(store.refs(), [])

    def test_about_oneself_is_for_every_client(self):
        gw, store = self.setup({"c": ClientPolicy("c", write="s", platforms=BOTH, public=True, audience="group")})
        self.assertEqual(self.say(gw, "c", {"speaker": AMY_QQ, "about": AMY_QQ}).status, 200)
        self.assertEqual(self.say(gw, "c", {"speaker": STRANGER, "about": STRANGER}).status, 200)
        self.assertEqual(self.fields_of(store), [{"audience": "owner", "speaker": "amy", "about": "amy"},
                                                  {"audience": "owner", "speaker": expected_code(STRANGER, "s"),
                                                   "about": expected_code(STRANGER, "s")}])

    def test_about_is_never_guessed(self):
        gw, store = self.setup()
        self.say(gw, "c", {"speaker": AMY_QQ})
        self.assertNotIn("about", self.fields_of(store)[0])

    def test_the_assistant_is_self(self):
        look = Look()
        gw, store = self.setup(guards=[look])
        self.say(gw, "c", {"role": "assistant", "speaker": STRANGER}, {"role": "assistant", "content": "second"},
                 {"role": "assistant", "speaker": OWNER_DSH, "content": "third"})
        self.assertEqual([m["speaker"] for m in look.seen[0]], ["self"] * 3)
        self.assertEqual([m["fields"]["speaker"] for m in store.received[0]], ["self"] * 3)  # the store gets them

    def test_the_assistants_account_is_not_checked_against_the_platforms(self):
        gw, store = self.setup({"c": ClientPolicy("c", write="s")})  # no platforms: no user speaker allowed
        self.assertEqual(self.say(gw, "c", {"role": "assistant", "speaker": "tg:1"}).status, 200)
        self.assertEqual(self.say(gw, "c", {"role": "assistant"}).status, 200)
        self.assertEqual(self.say(gw, "c", {}).status, 200)  # a user message without a speaker is fine
        self.assertEqual([[m.get("speaker") for m in sent] for sent in store.received], [["self"], ["self"], [None]])
        self.assertEqual([m["fields"].get("speaker") for sent in store.received for m in sent], ["self", "self", None])

    def test_a_user_message_without_a_speaker_has_none(self):
        look = Look()
        gw, store = self.setup(guards=[look])
        self.say(gw, "c", {})
        self.assertEqual(look.seen, [[{"role": "user", "content": SAID, "fields": {"audience": "owner"}}]])

    def test_a_store_that_keeps_no_fields_still_gets_the_resolved_speaker_in_the_message(self):
        from tests.test_request_fields import NoFields
        look = Look()
        store = NoFields()
        gw, _ = self.setup(store=store, guards=[look])
        self.say(gw, "c", {"speaker": AMY_QQ, "about": OWNER_DSH})
        self.assertEqual(look.seen, [[{"role": "user", "content": SAID, "speaker": "amy"}]])
        self.assertEqual(self.fields_of(store), [{}])

    def test_the_resolved_speaker_goes_with_the_other_fields(self):
        gw, store = self.setup()
        self.say(gw, "c", {"speaker": AMY_QQ, "channel": "kitchen", "kind": "plan", "origin": "tool"})
        self.assertEqual(self.fields_of(store), [{"audience": "owner", "speaker": "amy", "channel": "kitchen",
                                                  "origin": "tool", "kind": "plan"}])

    def test_only_for(self):
        policy = ClientPolicy("c", write="s", platforms=BOTH, only_for=("amy",))
        gw, store = self.setup({"c": policy})
        self.assertEqual(self.say(gw, "c", {"speaker": AMY_QQ}).status, 200)
        self.assertEqual(self.say(gw, "c", {"speaker": AMY_QQ}, {"role": "assistant"}).status, 200)  # self is fine
        self.assertEqual(len(store.refs()), 2)  # the assistant's line is not one this store keeps
        for label, messages in (("another person", [{"speaker": OWNER_DSH}]),
                                ("an unlisted account", [{"speaker": STRANGER}]),
                                ("no speaker", [{}]),
                                ("a mixed request", [{"speaker": AMY_QQ}, {"speaker": OWNER_DSH, "content": OTHER}]),
                                ("a mixed request, unlisted last", [{"speaker": AMY_QQ},
                                                                    {"speaker": STRANGER, "content": OTHER}]),
                                ("a mixed request, none last", [{"speaker": AMY_QQ}, {"content": OTHER}])):
            with self.subTest(case=label):
                reply = self.say(gw, "c", *messages)
                self.assertEqual((reply.status, reply.body), (403, {"error": "forbidden", "code": "forbidden"}))
        self.assertEqual(len(store.refs()), 2)  # nothing of a refused request was written

    def test_only_for_several_people(self):
        policy = ClientPolicy("c", write="s", platforms=BOTH, only_for=("amy", "owner"))
        gw, store = self.setup({"c": policy})
        self.assertEqual(self.say(gw, "c", {"speaker": AMY_QQ}, {"speaker": OWNER_QQ, "content": OTHER}).status, 200)
        self.assertEqual(self.say(gw, "c", {"speaker": STRANGER}).status, 403)

    def test_the_raw_account_reaches_no_guard_no_store_and_no_log(self):
        look, spy = Look(), Spy()
        gw, _ = self.setup(store=spy, guards=[RedactGuard(), look])
        reply = self.say(gw, "c", {"speaker": AMY_QQ, "about": AMY_QQ}, {"speaker": STRANGER, "content": OTHER},
                         {"speaker": OWNER_DSH, "about": STRANGER2, "content": "third thing said"},
                         {"role": "assistant", "speaker": STRANGER2, "content": "reply"})
        self.assertEqual(reply.status, 200)
        self.assertNoAccount(look.seen, spy.received, list(spy.records()), reply, self.lines)

    def test_a_refused_request_leaks_no_account_either(self):
        gw, store = self.setup({"c": ClientPolicy("c", write="s", platforms=("dsh",))})
        reply = self.say(gw, "c", {"speaker": OWNER_QQ})
        self.assertEqual(reply.status, 403)
        self.assertNoAccount(reply, self.lines)

    def test_a_guard_that_breaks_leaks_no_account(self):
        class Boom(Look):
            def before(self, messages, ctx):
                raise RuntimeError(messages[0]["speaker"])

        gw, store = self.setup(guards=[Boom()])
        reply = self.say(gw, "c", {"speaker": STRANGER})
        self.assertEqual(reply.status, 500)
        self.assertNoAccount(reply, self.lines)

    def test_over_http_the_access_log_holds_no_account(self):
        gw, store = self.setup()
        server = gateway_server.make_server(gw, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(5)

        self.addCleanup(stop)
        token = ("tok-id-c-").ljust(40, "0")

        def post(path, body):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            try:
                conn.request("POST", path, body=json.dumps(body).encode(),
                             headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
                response = conn.getresponse()
                return response.status, response.read().decode("utf-8")
            finally:
                conn.close()

        status, text = post("/v1/remember", {"messages": [{"role": "user", "content": SAID, "speaker": STRANGER,
                                                           "about": STRANGER2}]})
        self.assertEqual(status, 200, text)
        status, text = post("/v1/recall", {"query": SAID, "speaker": STRANGER})
        self.assertEqual(status, 200, text)
        status, text = post("/v1/recall", {"query": SAID, "speaker": "not an account"})
        self.assertEqual((status, json.loads(text)["code"]), (400, "bad_speaker"))
        self.assertEqual(post("/v1/remember", {"messages": [{"role": "user", "content": SAID,
                                                             "speaker": "tg:" + STRANGER}]})[0], 403)
        self.assertTrue(any("POST /v1/remember 200" in line for line in self.lines), self.lines)
        self.assertNoAccount(self.lines, text)


class RedactionOrderTests(Case):
    def test_accounts_are_resolved_before_the_public_redactor_runs(self):
        phone_a, phone_b, phone_amy = "qq:13800138000", "qq:13900139000", "qq:13700137000"
        people = {"owner": {"owner": True, "accounts": ["dsh:local"]}, "amy": {"accounts": [phone_amy]}}
        look, store = Look(), InMemoryBackend()
        gw = self.gateway({"s": store}, {"p": ClientPolicy("p", write="s", public=True, audience="public",
                                                           platforms=("qq",))},
                          table(people=people), audiences={"s": "public"}, guards=[RedactGuard(), look])
        reply = self.say(gw, "p", {"speaker": phone_a}, {"speaker": phone_b, "content": OTHER},
                         {"speaker": phone_amy, "content": "third thing said"})
        self.assertEqual(reply.status, 200)
        speakers = [f["speaker"] for f in self.fields_of(store)]
        self.assertEqual(speakers, [expected_code(phone_a, "s"), expected_code(phone_b, "s"), "amy"])
        self.assertEqual(len(set(speakers)), 3)
        self.assertNotIn("[REDACTED]", speakers)
        # what the guard saw after the redactor: the resolved values, not a masked account
        self.assertEqual([m["speaker"] for m in look.seen[0]], speakers)
        for number in ("13800138000", "13900139000", "13700137000"):
            self.assertNoAccount(self.lines)
            self.assertNotIn(number, str(look.seen) + str(list(store.records())))


# ---------------------------------------------------------------- recall and explain

class Watch(RecallStage):
    """A stage that remembers the `person` of every request it sees."""

    def __init__(self):
        self.seen = []

    def prepare(self, candidates, ctx):
        self.seen.append(ctx.person)
        return candidates

    def admit(self, candidate, ctx):
        return KEEP


class AskTests(Case):
    def setup(self, policy=None, identity=None, **options):
        self.watch = Watch()
        store = InMemoryBackend()
        store.put(SAID, ref="a", fields={"audience": "owner"})
        policy = policy or ClientPolicy("c", recall=("s",), write="s", platforms=BOTH, explain=True)
        gw = self.gateway({"s": store}, {"c": policy}, identity or table(), stages=[self.watch], **options)
        return gw

    def test_bad_speaker(self):
        gw = self.setup()
        for bad in ("amy", "aiocqhttp:", "AIOCQHTTP:1", "a b:1", 5, ["a:b"], "x" * 65, True, {}):
            for method in (gw.recall, gw.explain):
                with self.subTest(bad=bad, method=method.__name__):
                    reply = method("c", {"query": SAID, "speaker": bad})
                    self.assertEqual((reply.status, reply.body), (400, {"error": "bad speaker",
                                                                         "code": "bad_speaker"}))
        self.assertEqual(self.watch.seen, [])

    def test_null_is_no_speaker(self):
        gw = self.setup()
        self.assertEqual(gw.recall("c", {"query": SAID, "speaker": None}).status, 200)
        self.assertEqual(self.watch.seen, [None])

    def test_the_platform_rule(self):
        gw = self.setup(ClientPolicy("c", recall=("s",), explain=True, platforms=("dsh",)))
        for method in (gw.recall, gw.explain):
            with self.subTest(method=method.__name__):
                refused = method("c", {"query": SAID, "speaker": OWNER_QQ})
                self.assertEqual((refused.status, refused.body), (403, {"error": "forbidden", "code": "forbidden"}))
                self.assertEqual(method("c", {"query": SAID, "speaker": OWNER_DSH}).status, 200)
        gw = self.setup(ClientPolicy("c", recall=("s",)))  # no platforms: no speaker at all
        self.assertEqual(gw.recall("c", {"query": SAID, "speaker": OWNER_DSH}).status, 403)
        self.assertEqual(gw.recall("c", {"query": SAID}).status, 200)

    def test_only_for(self):
        gw = self.setup(ClientPolicy("c", recall=("s",), explain=True, platforms=BOTH, only_for=("amy",)))
        for method in (gw.recall, gw.explain):
            with self.subTest(method=method.__name__):
                self.assertEqual(method("c", {"query": SAID, "speaker": AMY_QQ}).status, 200)
                for body in ({"query": SAID}, {"query": SAID, "speaker": OWNER_DSH},
                             {"query": SAID, "speaker": STRANGER}, {"query": SAID, "speaker": None}):
                    self.assertEqual(method("c", body).status, 403, body)

    def test_the_stage_sees_the_resolved_person(self):
        gw = self.setup()
        gw.recall("c", {"query": SAID, "speaker": AMY_QQ})
        gw.recall("c", {"query": SAID, "speaker": OWNER_DSH})
        gw.recall("c", {"query": SAID})
        gw.explain("c", {"query": SAID, "speaker": AMY_QQ})
        self.assertEqual(self.watch.seen, ["amy", "owner", None, "amy"])

    def test_an_unlisted_speaker_is_its_code_for_the_store_the_client_writes_to_else_reads(self):
        gw = self.setup()
        gw.recall("c", {"query": SAID, "speaker": STRANGER})
        reader = self.setup(ClientPolicy("c", recall=("s",), platforms=BOTH))
        reader.recall("c", {"query": SAID, "speaker": STRANGER})
        self.assertEqual(self.watch.seen, [expected_code(STRANGER, "s")])  # the second gateway has its own stage
        self.assertEqual(self.watch.seen[0], table().code(STRANGER, "s"))

    def test_a_tenant_clients_person_is_scoped_by_its_tenant(self):
        watch = Watch()
        gw = self.gateway({}, {"t": ClientPolicy("t", tenants="g", recall=("@tenant",), public=True,
                                                 audience="group", platforms=BOTH)}, table(),
                          tenants={"g": MemTenants()}, stages=[watch])
        self.assertEqual(gw.recall("t", {"query": SAID, "tenant_id": GROUP_ID, "speaker": STRANGER}).status, 200)
        self.assertEqual(watch.seen, [expected_code(STRANGER, "g:" + GROUP_ID)])

    def test_with_no_key_and_no_store_to_scope_by_an_unlisted_speaker_is_anon(self):
        gw = self.gateway({}, {"c": ClientPolicy("c", recall=(), platforms=BOTH)}, table(hash_key=None),
                          stages=[self.setup_watch()])
        self.assertEqual(gw.recall("c", {"query": SAID, "speaker": STRANGER}).status, 200)
        self.assertEqual(gw.recall("c", {"query": SAID, "speaker": AMY_QQ}).status, 200)
        self.assertEqual(self.watch.seen, ["anon", "amy"])
        reader = self.gateway({"s": InMemoryBackend()}, {"c": ClientPolicy("c", recall=("s",), platforms=BOTH)},
                              table(hash_key=None), stages=[self.setup_watch()])
        reader.recall("c", {"query": SAID, "speaker": STRANGER})
        self.assertEqual(self.watch.seen, ["anon"])

    def setup_watch(self):
        self.watch = Watch()
        return self.watch

    def test_used_carries_no_speaker_and_is_not_covered_by_only_for(self):
        gw = self.setup(ClientPolicy("c", recall=("s",), platforms=BOTH, only_for=("amy",)))
        reply = gw.used("c", {"used": [{"source": "s", "id": "a"}]})
        self.assertEqual((reply.status, reply.body), (200, {"status": "ok", "recorded": 0}))

    def test_the_answer_is_what_it_was(self):
        gw = self.setup()
        plain = gw.recall("c", {"query": SAID})
        named = gw.recall("c", {"query": SAID, "speaker": AMY_QQ})
        self.assertEqual(plain.body, named.body)
        self.assertEqual(set(named.body), {"results", "more", "failed", "busy"})

    def test_a_person_in_a_log_line_never_holds_the_account(self):
        gw = self.setup()
        gw.recall("c", {"query": SAID, "speaker": STRANGER})
        gw.recall("c", {"query": SAID, "speaker": "bad speaker"})
        self.assertNoAccount(self.lines)


# ---------------------------------------------------------------- the acceptance case

class TwoPlatformsTests(Case):
    def test_one_person_on_two_platforms_is_one_person_and_a_client_keeps_to_its_platform(self):
        store = InMemoryBackend()
        gw = self.gateway({"s": store}, {
            "dsh": ClientPolicy("dsh", write="s", platforms=("dsh",)),
            "qq": ClientPolicy("qq", write="s", platforms=("aiocqhttp",)),
            "reader": ClientPolicy("reader", recall=("s",), fields=("speaker",), platforms=BOTH)}, table())
        self.assertEqual(self.say(gw, "dsh", {"speaker": OWNER_DSH, "content": SAID}).status, 200)
        self.assertEqual(self.say(gw, "qq", {"speaker": OWNER_QQ, "content": OTHER}).status, 200)
        self.assertEqual([f["speaker"] for f in self.fields_of(store)], ["owner", "owner"])
        for text in (SAID, OTHER):
            item = gw.recall("reader", {"query": text, "speaker": OWNER_DSH}).body["results"][0]
            self.assertEqual((item["text"], item["fields"]), (text, {"speaker": "owner"}))
        # each client may only speak for accounts of its own platform
        self.assertEqual(self.say(gw, "qq", {"speaker": OWNER_DSH, "content": "forged thing"}).status, 403)
        self.assertEqual(self.say(gw, "dsh", {"speaker": OWNER_QQ, "content": "forged thing"}).status, 403)
        self.assertEqual(len(store.refs()), 2)
        self.assertEqual(self.say(gw, "qq", {"speaker": AMY_QQ, "content": "amy said so"}).status, 200)
        self.assertEqual(self.fields_of(store)[-1]["speaker"], "amy")


# ---------------------------------------------------------------- without a table

class WithoutIdentityTests(Case):
    def setup(self, **client):
        look, spy = Look(), Spy()
        gw = self.gateway({"s": spy}, {"o": ClientPolicy("o", write="s", recall=("s",), **client)}, None,
                          guards=[look])
        return gw, spy, look

    def test_speaker_is_handed_to_the_guards_and_not_stored_and_about_is_dropped(self):
        gw, spy, look = self.setup()
        reply = gw.remember("o", {"messages": [{"role": "user", "content": SAID, "speaker": "aiocqhttp:10001",
                                                "about": "aiocqhttp:20002", "channel": "kitchen"}]})
        self.assertEqual(reply.status, 200)
        expected = [{"role": "user", "content": SAID, "speaker": "aiocqhttp:10001",
                     "fields": {"audience": "owner", "channel": "kitchen"}}]
        self.assertEqual(look.seen, [expected])
        self.assertEqual(spy.received, [expected])
        self.assertEqual(self.fields_of(spy), [{"audience": "owner", "channel": "kitchen"}])
        self.assertEqual(set(reply.body), {"status", "receipt"})

    def test_a_speaker_that_is_no_account_is_still_fine(self):
        gw, spy, look = self.setup()
        for speaker in ("mia", "a b", "x" * 64, "DSH:1"):
            self.assertEqual(gw.remember("o", {"messages": [{"role": "user", "content": SAID, "speaker": speaker}]})
                             .status, 200, speaker)
        self.assertEqual(gw.remember("o", {"messages": [{"role": "user", "content": SAID, "speaker": "x" * 65}]})
                         .body, {"error": "bad messages", "code": "bad_messages"})

    def test_an_assistant_keeps_the_speaker_it_was_given(self):
        gw, spy, look = self.setup()
        gw.remember("o", {"messages": [{"role": "assistant", "content": SAID, "speaker": "bot"}]})
        self.assertEqual(look.seen, [[{"role": "assistant", "content": SAID, "speaker": "bot",
                                       "fields": {"audience": "owner"}}]])

    def test_about_must_still_be_a_short_text(self):
        gw, spy, look = self.setup()
        for bad in (5, ["x"], "x" * 65):
            reply = gw.remember("o", {"messages": [{"role": "user", "content": SAID, "about": bad}]})
            self.assertEqual((reply.status, reply.body["code"]), (400, "bad_messages"))
        self.assertEqual(spy.refs(), [])

    def test_a_recall_ignores_a_speaker_in_its_body(self):
        watch = Watch()
        store = InMemoryBackend()
        store.put(SAID, ref="a", fields={"audience": "owner"})
        gw = self.gateway({"s": store}, {"o": ClientPolicy("o", recall=("s",))}, None, stages=[watch])
        expected = gw.recall("o", {"query": SAID})
        for speaker in ("aiocqhttp:10001", "anything", 5, ["x"], None):
            with self.subTest(speaker=speaker):
                reply = gw.recall("o", {"query": SAID, "speaker": speaker})
                self.assertEqual((reply.status, reply.body), (expected.status, expected.body))
        self.assertEqual(watch.seen, [None] * 6)
        self.assertEqual(expected.body["results"][0]["id"], "a")
        self.assertEqual(set(expected.body["results"][0]), {"id", "source", "text", "score"})


# ---------------------------------------------------------------- configuration

ENV = {"IDK": KEY, "TOKEN_C": "tok-id-config-".ljust(40, "0"), "TOKEN_R": "tok-id-reader-".ljust(40, "0")}


def identity_cfg(**changes):
    spec = {"type": "table", "hash_key_env": "IDK", "people": PEOPLE}
    spec.update(changes)
    return {key: value for key, value in spec.items() if value is not None}


def cfg(identity=None, client=None, **extra):
    out = {"stores": {"s": {"type": "inmemory"}},
           "clients": {"c": dict({"token_env": "TOKEN_C", "write": "s", "recall": ["s"], "platforms": ["dsh"]},
                                 **(client or {}))}}
    if identity is not False:
        out["identity"] = identity if identity is not None else identity_cfg()
    out.update(extra)
    return out


class ConfigTests(Case):
    def build(self, config_dict, env=None):
        gw = config.build_gateway(config_dict, env=dict(ENV if env is None else env), log=self.lines.append)
        self.addCleanup(gw.close)
        return gw

    def refused(self, config_dict, needle, env=None):
        with self.assertRaises(ConfigError) as caught:
            config.build_gateway(config_dict, env=dict(ENV if env is None else env), log=self.lines.append)
        message = str(caught.exception)
        self.assertIn(needle, message)
        self.assertNoAccount(message)
        return message

    def test_the_section_builds_a_gateway_with_a_provider(self):
        gw = self.build(cfg())
        self.assertIsInstance(gw.identity, TableIdentity)
        self.assertEqual(gw.identity.owner, "owner")
        self.assertEqual(gw.identity.people(), {"owner": 2, "amy": 1})
        self.assertEqual(gw.clients["c"].platforms, ("dsh",))
        self.assertEqual(self.say(gw, "c", {"speaker": OWNER_DSH}).status, 200)
        self.assertEqual([f["speaker"] for f in self.fields_of(gw.stores["s"])], ["owner"])

    def test_a_gateway_without_the_section_has_no_provider(self):
        gw = self.build(cfg(False, client={"platforms": None}))
        self.assertIsNone(gw.identity)

    def test_the_two_policy_keys_are_known(self):
        self.assertIn("platforms", config._POLICY_KEYS)
        self.assertIn("only_for", config._POLICY_KEYS)
        self.assertIn("identity", config._TOP_KEYS)
        self.assertEqual(config.client_policy("c", {}).platforms, ())
        self.assertEqual(config.client_policy("c", {}).only_for, ())
        policy = config.client_policy("c", {"platforms": ["dsh", "aiocqhttp"], "only_for": ["amy"]})
        self.assertEqual((policy.platforms, policy.only_for), (("dsh", "aiocqhttp"), ("amy",)))
        self.assertEqual([name for name in ClientPolicy.__dataclass_fields__][-2:], ["platforms", "only_for"])

    def test_bad_policy_values(self):
        for key, bad in (("platforms", ["DSH"]), ("platforms", "dsh"), ("platforms", [1]), ("platforms", ["a:b"]),
                         ("only_for", ["Amy"]), ("only_for", "amy"), ("only_for", ["self"]), ("only_for", [None])):
            with self.subTest(key=key, bad=bad):
                with self.assertRaises(ConfigError) as caught:
                    config.client_policy("c", {key: bad})
                self.assertIn("clients.c.%s must be a list of" % key, str(caught.exception))

    def test_a_setting_nothing_would_enforce(self):
        for key, value in (("platforms", ["dsh"]), ("only_for", ["amy"])):
            with self.subTest(key=key):
                self.refused(cfg(False, client={"platforms": None, key: value}),
                             "clients.c.%s needs an [identity] section" % key)
        self.refused(cfg(False), "clients.c.platforms needs an [identity] section")
        self.build(cfg(False, client={"platforms": []}))  # an empty list sets nothing

    def test_only_for_naming_nobody_in_the_table(self):
        self.refused(cfg(client={"only_for": ["amy", "bob"]}), "clients.c.only_for names a person who is not in "
                                                                "[identity.people]")
        self.build(cfg(client={"only_for": ["amy", "owner"]}))

    def test_a_bad_table_is_refused_by_the_loader_too(self):
        bad_people = dict(PEOPLE, amy={"accounts": [OWNER_QQ]})
        self.refused(cfg(identity_cfg(people=bad_people)), "an account is listed for both owner and amy")
        self.refused(cfg(identity_cfg(people={"amy": {"accounts": [AMY_QQ]}})), "exactly one person")
        self.refused(cfg(identity_cfg(people={"self": {"owner": True, "accounts": []}})), "are reserved")
        self.refused(cfg(identity_cfg(colour="red")), "identity: unknown setting colour")
        self.refused(cfg(identity_cfg(people=None)), "identity.people must list the people")

    def test_the_key_is_not_given_in_the_file(self):
        message = self.refused(cfg(identity_cfg(hash_key=KEY)), "identity: unknown setting hash_key")
        self.assertNotIn(KEY, message)

    def test_the_type(self):
        self.refused(cfg(identity_cfg(type="nowhere")), "no identity piece named 'nowhere' (known: table)")
        self.refused(cfg(identity_cfg(type=None)), "a identity needs a type")
        with self.assertRaises(ConfigError):
            config.build_gateway(dict(cfg(), identity=["x"]), env=dict(ENV), log=self.lines.append)

    def test_the_key_when_a_client_may_write(self):
        self.refused(cfg(), "identity.hash_key_env: environment variable IDK is not set", env={"TOKEN_C": ENV["TOKEN_C"]})
        self.refused(cfg(identity_cfg(hash_key_env=None)), "identity.hash_key_env: a client may write")
        self.refused(cfg(), "identity.hash_key_env: a client may write, so the key", env=dict(ENV, IDK="short-key"))
        self.refused(cfg(), "at least 32 characters", env=dict(ENV, IDK="change-me"))
        self.refused(cfg(), "identity.hash_key_env: a client may write", env=dict(ENV, IDK="x" * 31))
        self.build(cfg(), env=dict(ENV, IDK="x" * 32))
        self.refused(cfg(identity_cfg(hash_key_env=5)), "identity.hash_key_env must name an environment variable")

    def test_the_key_is_optional_when_no_client_may_write(self):
        readers = {"clients": {"r": {"token_env": "TOKEN_R", "recall": ["s"], "platforms": ["dsh"]}}}
        for env in (dict(ENV), {"TOKEN_R": ENV["TOKEN_R"]}, dict(ENV, IDK="short")):
            gw = self.build(dict(cfg(), **readers), env=env)
            self.assertEqual(gw.identity.has_key, env.get("IDK") == "short" or env.get("IDK") == KEY)
        gw = self.build(dict(cfg(identity_cfg(hash_key_env=None)), **readers))
        self.assertFalse(gw.identity.has_key)

    def test_a_gateway_built_in_code_has_the_same_rules(self):
        store = {"s": InMemoryBackend()}
        writer = ClientPolicy("w", write="s", platforms=("dsh",))
        with self.assertRaises(ConfigError):
            Gateway(store, {"w": writer}, {}, identity=table(hash_key="short"), log=self.lines.append)
        with self.assertRaises(ConfigError):
            Gateway(store, {"w": writer}, {}, identity=table(hash_key=None), log=self.lines.append)
        for policy in (ClientPolicy("w", platforms=("dsh",)), ClientPolicy("w", only_for=("amy",))):
            with self.assertRaises(ConfigError) as caught:
                Gateway(store, {"w": policy}, {}, log=self.lines.append)
            self.assertIn("needs an [identity] section", str(caught.exception))
        for policy in (ClientPolicy("w", platforms=("DSH",)), ClientPolicy("w", only_for=("bob",)),
                       ClientPolicy("w", only_for=("self",)), ClientPolicy("w", platforms="dsh")):
            with self.assertRaises(ConfigError):
                Gateway(store, {"w": policy}, {}, identity=table(), log=self.lines.append)
        Gateway(store, {"r": ClientPolicy("r", recall=("s",))}, {}, identity=table(hash_key=None),
                log=self.lines.append).close()

    def test_a_provider_written_by_someone_else(self):
        class Everyone(IdentityProvider):
            owner = "boss"

            def resolve(self, account):
                return "boss" if account == "dsh:me" else None

            def code(self, account, scope):
                return "anon:" + "0" * 16

            def people(self):
                return {"boss": 1}

        gw = self.gateway({"s": InMemoryBackend()}, {"c": ClientPolicy("c", write="s", platforms=("dsh",))},
                          Everyone())
        self.assertEqual(self.say(gw, "c", {"speaker": "dsh:me"}, {"speaker": "dsh:you", "content": OTHER}).status,
                         200)
        self.assertEqual([f["speaker"] for f in self.fields_of(gw.stores["s"])], ["boss", "anon:" + "0" * 16])


# ---------------------------------------------------------------- check

def run(*argv, env=None):
    out = io.StringIO()
    with mock.patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out), \
            contextlib.redirect_stderr(io.StringIO()):
        try:
            code = cli.main(list(argv))
        except SystemExit as exit_:
            code = exit_.code
    return code, out.getvalue()


class CheckTests(Case):
    def lines_of(self, config_dict, env=None):
        gw = config.build_gateway(config_dict, env=dict(ENV if env is None else env), log=lambda line: None)
        self.addCleanup(gw.close)
        return cli.describe(gw)

    def test_the_identity_block_and_the_client_line(self):
        lines = self.lines_of(cfg(client={"platforms": ["dsh", "aiocqhttp"], "only_for": ["amy"]}))
        self.assertIn("身份：", lines)
        block = lines[lines.index("身份："):lines.index("客户端：")]
        self.assertEqual(block, ["身份：", "  table；说话人散列密钥 有", "  owner（主人）: 2 个账号", "  amy: 1 个账号"])
        client = next(line for line in lines if line.startswith("  c:"))
        self.assertIn("；可报的平台 dsh, aiocqhttp", client)
        self.assertIn("；只替 amy 说话", client)

    def test_the_key_line_says_yes_or_no_only(self):
        readers = {"clients": {"r": {"token_env": "TOKEN_R", "recall": ["s"], "platforms": ["dsh"]}}}
        lines = self.lines_of(dict(cfg(identity_cfg(hash_key_env=None)), **readers))
        self.assertIn("  table；说话人散列密钥 没有", lines)

    def test_a_writer_without_platforms_gets_a_reminder(self):
        lines = self.lines_of(cfg(client={"platforms": None}))
        reminders = [line for line in lines if "提醒：" in line]
        self.assertEqual(reminders, ["  提醒：c 可以写入，却没有设 platforms：它报的每个说话人都会被拒绝（403）"])
        self.assertEqual([line for line in self.lines_of(cfg()) if "提醒：" in line], [])
        readers = {"clients": {"r": {"token_env": "TOKEN_R", "recall": ["s"]}}}
        self.assertEqual([line for line in self.lines_of(dict(cfg(), **readers)) if "提醒：" in line], [])

    def test_a_gateway_without_identity_prints_what_it_printed(self):
        lines = self.lines_of(cfg(False, client={"platforms": None}))
        self.assertNotIn("身份：", lines)
        self.assertFalse([line for line in lines if "平台" in line or "只替" in line or "platforms" in line])

    def test_the_output_holds_no_account_and_no_key(self):
        lines = self.lines_of(cfg(client={"platforms": ["dsh"], "only_for": ["amy"]}))
        self.assertNoAccount("\n".join(lines))

    def test_the_command(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "gateway.toml"
            file.write_text(
                '[stores.s]\ntype = "inmemory"\n\n'
                '[identity]\ntype = "table"\nhash_key_env = "IDK"\n\n'
                '[identity.people.owner]\nowner = true\naccounts = ["dsh:local", "aiocqhttp:10001"]\n\n'
                '[identity.people.amy]\naccounts = ["aiocqhttp:20002"]\n\n'
                '[clients.c]\ntoken_env = "TOKEN_C"\nwrite = "s"\nrecall = ["s"]\nplatforms = ["dsh"]\n'
                'only_for = ["amy"]\n', encoding="utf-8")
            code, out = run("check", "--config", str(file), env=ENV)
            self.assertEqual(code, 0, out)
            self.assertIn("  owner（主人）: 2 个账号", out)
            self.assertIn("  amy: 1 个账号", out)
            self.assertNoAccount(out)
            code, out = run("check", "--config", str(file), env={"IDK": ""})
            self.assertEqual(code, 1)
            self.assertIn("配置有问题：identity.hash_key_env: environment variable IDK is not set", out)
            self.assertNoAccount(out)
            code, out = run("pieces")
            self.assertIn("身份（identity）：", out)
            self.assertIn("  table  ", out)


if __name__ == "__main__":
    unittest.main()
