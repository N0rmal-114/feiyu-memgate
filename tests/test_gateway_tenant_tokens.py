"""Tokens bound to one tenant (tenant_binding = "token"): minting and verification, which tenant a
request is for, limits per tenant, the HTTP server, the `tenants token` command and the client option.

No network beyond loopback: in-memory and SQLite stores, a throw-away gateway server on an ephemeral
port. Every secret here is fake.
"""
import base64
import calendar
import contextlib
import hashlib
import hmac
import http.client
import io
import json
import logging
import os
import struct
import sys
import tempfile
import threading
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from feiyu_memgate import cli, config
from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.client import MemoryClient
from feiyu_memgate.core.write import DailyCapGuard, RedactGuard
from feiyu_memgate.errors import ConfigError
from feiyu_memgate.gateway import app
from feiyu_memgate.gateway import server as http_server
from feiyu_memgate.gateway import tokens as gateway_tokens
from feiyu_memgate.model import ClientPolicy
from feiyu_memgate.tenants.base import TenantProvider
from feiyu_memgate.tools import tenants as tenant_tools
from tests.waiting import wait_until

SECRET = "fake-minting-secret-bot-".ljust(40, "0")  # usable, but `check` calls it weak: 40 characters
SECRET_OTHER = "fake-minting-secret-other-".ljust(40, "1")
# Shaped like secrets.token_urlsafe(32): 43 characters, many distinct. Still obviously fake.
SECRET_STRONG = "fake-STRONG-minting-secret-ABCDEFGHIJKLMNOPQRSTUVWXYZ"
OWNER_TOKEN = "fake-owner-token-".ljust(40, "2")
TRUSTED_TOKEN = "fake-request-mode-token-".ljust(40, "3")
FIXED_DAY = float(calendar.timegm((2026, 11, 15, 12, 0, 0)))

OWNER = ClientPolicy("owner", recall=("private",), write="private")
BOT = ClientPolicy("bot", recall=("@tenant",), write="@tenant", tenants="group", tenant_binding="token")
OTHER = ClientPolicy("other", recall=("@tenant",), write="@tenant", tenants="group", tenant_binding="token")
TRUSTED = ClientPolicy("trusted", recall=("@tenant",), write="@tenant", tenants="group")


def mint(tenant_id, secret=SECRET, client="bot", generation=1, provider="group"):
    return app.mint_tenant_token(secret, client, provider, generation, tenant_id)


def frame(*fields):
    return b"".join(struct.pack(">I", len(f)) + f for f in fields)


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def with_part(token, index, part):
    parts = token.split(".")
    parts[index] = part
    return ".".join(parts)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Tenants(TenantProvider):
    """Digit ids of 5 to 12 ASCII digits; only `known` ones exist, each with one in-memory memory."""

    def __init__(self, known=("10001", "10002")):
        self.known = set(known)
        self.stores = {}
        self.asked = []

    def valid_id(self, tenant_id):
        return isinstance(tenant_id, str) and tenant_id.isascii() and tenant_id.isdigit() and 5 <= len(tenant_id) <= 12

    def store(self, tenant_id):
        self.asked.append(tenant_id)
        if tenant_id not in self.known:
            return None
        if tenant_id not in self.stores:
            self.stores[tenant_id] = InMemoryBackend(memories=["tenant %s likes green tea" % tenant_id])
        return self.stores[tenant_id]


class GatewayCase(unittest.TestCase):
    """A gateway called in process the way the HTTP server calls it: identify, admit, then the route."""

    def build(self, clients=None, tokens=None, known=("10001", "10002"), **kw):
        self.tenants = Tenants(known)
        self.logs = []
        clients = clients or {"owner": OWNER, "bot": BOT, "trusted": TRUSTED}
        tokens = tokens or {"owner": OWNER_TOKEN, "bot": SECRET, "trusted": TRUSTED_TOKEN, "other": SECRET_OTHER}
        gw = app.Gateway({"private": InMemoryBackend(memories=["owner private diary green tea"])}, clients, tokens,
                         tenants={"group": self.tenants}, log=self.logs.append, clock=Clock(), **kw)
        self.addCleanup(gw.close)
        return gw

    def setUp(self):
        self.gw = self.build()

    def call(self, action, token, body):
        caller = self.gw.identify(token)
        if caller is None:
            return app.error_reply(401, "unauthorized", "unauthorized")
        name, bound = caller
        refused = self.gw.admit(name, action, bound)
        if refused is not None:
            return refused
        return getattr(self.gw, action)(name, body, bound)

    def recall(self, token, **body):
        return self.call("recall", token, dict({"query": "green tea"}, **body))

    def remember(self, token, text="likes oolong", **body):
        return self.call("remember", token, dict({"messages": [{"role": "user", "content": text}]}, **body))


# ------------------------------------------------------------------------------ the format


class MintTests(unittest.TestCase):
    def test_format(self):
        token = mint("10001")
        parts = token.split(".")
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0], "fmg1")
        self.assertEqual(parts[1], b64(b"10001"))
        self.assertEqual(len(parts[2]), 43)  # 32 bytes, unpadded
        self.assertNotIn("=", token)
        self.assertTrue(token.isascii())

    def test_the_mac_covers_label_client_provider_generation_and_tenant_with_length_prefixes(self):
        context = frame(b"feiyu-memgate tenant token v1", b"bot", b"group", b"1", b"10001")
        expected = hmac.new(SECRET.encode("utf-8"), context, hashlib.sha256).digest()
        self.assertEqual(mint("10001").split(".")[2], b64(expected))

    def test_every_field_changes_the_token(self):
        base = mint("10001")
        self.assertEqual(base, mint("10001"))
        for other in (mint("10002"), mint("10001", client="other"), mint("10001", generation=2),
                      mint("10001", provider="group2"), mint("10001", secret=SECRET_OTHER)):
            self.assertNotEqual(base.split(".")[2], other.split(".")[2])

    def test_the_framing_is_unambiguous(self):
        mac = app.tenant_token_mac
        self.assertNotEqual(mac(SECRET, "ab", "group", 1, "c"), mac(SECRET, "a", "group", 1, "bc"))
        self.assertNotEqual(mac(SECRET, "a", "group", 11, "0001"), mac(SECRET, "a", "group", 1, "10001"))
        self.assertNotEqual(mac(SECRET, "ab", "g", 1, "x"), mac(SECRET, "a", "bg", 1, "x"))
        self.assertNotEqual(mac(SECRET, "a", "g1", 1, "x"), mac(SECRET, "a", "g", 11, "x"))

    def test_parse_round_trip(self):
        tenant_id, mac = app.parse_tenant_token(mint("10001"))
        self.assertEqual(tenant_id, "10001")
        self.assertEqual(mac, app.tenant_token_mac(SECRET, "bot", "group", 1, "10001"))

    def test_mint_refuses_what_cannot_be_a_tenant_id(self):
        for bad in ("", "1" * (app.MAX_TENANT_ID_BYTES + 1), None, 10001, "\ud800"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                mint(bad)
        self.assertEqual(len(mint("1" * app.MAX_TENANT_ID_BYTES)), app.MAX_TENANT_TOKEN_CHARS)

    def test_canonical_base64url_only(self):
        self.assertEqual(app._unb64("-w"), b"\xfb")
        for bad in ("+w", "/w", "-w==", "-x", "", "A", " -w", "-w\n", "ＭＱ"):
            self.assertIsNone(app._unb64(bad), repr(bad))


# ------------------------------------------------------------------------------ verification


class IdentifyTests(GatewayCase):
    def test_a_minted_token_names_its_client_and_its_tenant(self):
        self.assertEqual(self.gw.identify(mint("10001")), ("bot", "10001"))
        self.assertEqual(self.gw.authenticate(mint("10001")), "bot")
        self.assertEqual(self.gw.identify(OWNER_TOKEN), ("owner", None))
        self.assertEqual(self.gw.identify(TRUSTED_TOKEN), ("trusted", None))
        self.assertTrue(self.gw.has_token("bot"))

    def test_the_minting_secret_alone_is_refused(self):
        self.assertIsNone(self.gw.identify(SECRET))
        self.assertEqual(self.recall(SECRET).status, 401)
        self.assertEqual(self.recall(SECRET, tenant_id="10001").status, 401)

    def test_a_tampered_tenant_part_is_refused(self):
        token = mint("10001")
        for tenant in ("10002", "10001 ", "100010", "1000"):
            self.assertIsNone(self.gw.identify(with_part(token, 1, b64(tenant.encode()))), tenant)

    def test_a_tampered_mac_is_refused(self):
        token = mint("10001")
        mac = token.split(".")[2]
        for i in range(len(mac)):
            for c in ("A", "B", "-"):
                if mac[i] != c:
                    self.assertIsNone(self.gw.identify(with_part(token, 2, mac[:i] + c + mac[i + 1:])), (i, c))

    def test_truncated_tokens_are_refused(self):
        token = mint("10001")
        tenant_part = token.split(".")[1]
        for bad in (token[:-1], token[:-10], token[:-44], "fmg1.", "fmg1." + tenant_part, "fmg1." + tenant_part + ".",
                    "fmg1.." + token.split(".")[2], token[5:], "fmg1"):
            self.assertIsNone(self.gw.identify(bad), bad)

    def test_an_over_long_token_is_refused_before_any_work(self):
        long = "fmg1." + "A" * (app.MAX_TENANT_TOKEN_CHARS - 48) + "." + "A" * 43
        self.assertEqual(len(long), app.MAX_TENANT_TOKEN_CHARS + 1)
        padded = mint("10001") + "A" * 1000
        with mock.patch.object(app, "tenant_token_mac") as mac, mock.patch.object(self.tenants, "valid_id") as valid, \
                mock.patch.object(gateway_tokens, "_unb64") as decode:
            self.assertIsNone(self.gw.identify(long))
            self.assertIsNone(self.gw.identify(padded))
        mac.assert_not_called()
        valid.assert_not_called()
        decode.assert_not_called()

    def test_non_canonical_spellings_are_refused(self):
        token = mint("10001")
        prefix, tenant_part, mac = token.split(".")
        self.assertIsNone(self.gw.identify(with_part(token, 1, tenant_part + "=")))
        self.assertIsNone(self.gw.identify(with_part(token, 2, mac + "=")))
        # The same bytes spelt with non-zero unused bits in the last character.
        for index, part in ((1, tenant_part), (2, mac)):
            raw = base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
            twins = [c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
                     if c != part[-1] and base64.urlsafe_b64decode(part[:-1] + c + "=" * (-len(part) % 4)) == raw]
            self.assertTrue(twins, index)
            for twin in twins:
                self.assertIsNone(self.gw.identify(with_part(token, index, part[:-1] + twin)))
        # The standard alphabet instead of the URL-safe one.
        for n in range(10001, 10400):
            candidate = mint(str(n))
            mac_n = candidate.split(".")[2]
            if "-" in mac_n or "_" in mac_n:
                self.assertEqual(self.gw.identify(candidate), ("bot", str(n)))
                standard = mac_n.replace("-", "+").replace("_", "/")
                self.assertIsNone(self.gw.identify(with_part(candidate, 2, standard)))
                break
        else:
            self.fail("no MAC with a URL-safe character in 400 tries")
        for bad in (token.replace(".", " .", 1), token + "\n", " " + token, token.replace("fmg1", "FMG1", 1),
                    token + ".x", prefix + "." + tenant_part + ".." + mac):
            self.assertIsNone(self.gw.identify(bad), repr(bad))

    def test_non_ascii_tokens_are_refused(self):
        token = mint("10001")
        for bad in (token[:-1] + "é", token.replace("M", "Ｍ", 1), token + "​", "fmg1.%s.%s" % ("é" * 7, "A" * 43),
                    token[:-1] + "\ud800", "fmg1.\ud800.%s" % ("A" * 43)):
            self.assertIsNone(self.gw.identify(bad), repr(bad))

    def test_text_with_a_lone_surrogate_is_refused_not_raised(self):
        # A lone surrogate has no UTF-8 form; one outside the surrogateescape range cannot even be
        # escaped. Whatever the HTTP layer or an in-process caller hands over, the answer is None.
        for bad in ("\ud800abc", "\udfff", "abc\ud800" + "x" * 40, OWNER_TOKEN[:-1] + "\ud800",
                    "\udc80" + OWNER_TOKEN[1:], OWNER_TOKEN + "\udcff"):
            self.assertIsNone(self.gw.identify(bad), repr(bad))
            self.assertIsNone(self.gw.authenticate(bad), repr(bad))
            self.assertEqual(self.recall(bad).status, 401)

    def test_a_configured_token_that_is_not_valid_text_is_a_configuration_error(self):
        for name, bad in (("owner", "fake-owner-\ud800-".ljust(40, "z")), ("bot", "fake-mint-\ud800-".ljust(40, "z"))):
            with self.assertRaises(ConfigError) as ctx:
                app.Gateway({"private": InMemoryBackend()}, {"owner": OWNER, "bot": BOT}, {name: bad},
                            tenants={"group": Tenants()}, log=lambda line: None)
            self.assertIn(name, str(ctx.exception))
            self.assertNotIn(bad, str(ctx.exception))

    def test_a_tenant_id_the_provider_refuses_never_authenticates(self):
        # A genuine MAC over an id the provider refuses: still None, and the id reaches nothing.
        for tenant in ("../x", "1234", "abcde", "10001\n", "١٠٠٠١", "10001/../10002"):
            self.assertIsNone(self.gw.identify(mint(tenant)), repr(tenant))
        # Bytes that are not UTF-8, with a MAC computed over them as they are.
        raw = b"\xff\xfe10001"
        context = frame(app.TENANT_TOKEN_LABEL, b"bot", b"group", b"1", raw)
        mac = hmac.new(SECRET.encode(), context, hashlib.sha256).digest()
        self.assertIsNone(self.gw.identify("fmg1.%s.%s" % (b64(raw), b64(mac))))
        self.assertEqual(self.tenants.asked, [])

    def test_a_token_of_one_client_never_authenticates_as_another(self):
        gw = self.build(clients={"bot": BOT, "other": OTHER})
        self.assertEqual(gw.identify(mint("10001")), ("bot", "10001"))
        self.assertEqual(gw.identify(mint("10001", SECRET_OTHER, "other")), ("other", "10001"))
        self.assertIsNone(gw.identify(mint("10001", SECRET, "other")))  # bot's secret, other's name
        self.assertIsNone(gw.identify(mint("10001", SECRET_OTHER, "bot")))  # other's secret, bot's name

    def test_one_secret_for_two_clients_is_a_configuration_error(self):
        for tokens in ({"bot": SECRET, "other": SECRET}, {"bot": SECRET, "owner": SECRET}):
            with self.assertRaises(ConfigError) as ctx:
                app.Gateway({"private": InMemoryBackend()}, {"owner": OWNER, "bot": BOT, "other": OTHER}, tokens,
                            tenants={"group": Tenants()}, log=lambda line: None)
            self.assertNotIn(SECRET, str(ctx.exception))
            self.assertIn("bot", str(ctx.exception))

    def test_a_configured_token_may_not_look_like_a_minted_one(self):
        lookalike = "fmg1." + "x" * 40
        with self.assertRaises(ConfigError) as ctx:
            app.Gateway({"private": InMemoryBackend()}, {"owner": OWNER}, {"owner": lookalike}, log=lambda line: None)
        self.assertNotIn(lookalike, str(ctx.exception))

    def test_pointing_a_client_at_another_provider_voids_its_tokens(self):
        # The same id under another provider may be someone else: a token does not follow its client.
        repointed = ClientPolicy("bot", recall=("@tenant",), write="@tenant", tenants="group2", tenant_binding="token")
        gw = app.Gateway({}, {"bot": repointed}, {"bot": SECRET}, tenants={"group": Tenants(), "group2": Tenants()},
                         log=lambda line: None)
        self.addCleanup(gw.close)
        self.assertIsNone(gw.identify(mint("10001")))
        self.assertEqual(gw.identify(mint("10001", provider="group2")), ("bot", "10001"))

    def test_a_weak_minting_secret_is_refused_in_code_too(self):
        # The configuration loader drops an unusable token; a gateway built in code refuses it.
        for weak in ("tiny-fake", "change-me", "x" * (app.MIN_TOKEN_CHARS - 1)):
            with self.assertRaises(ConfigError, msg=weak) as ctx:
                app.Gateway({}, {"bot": BOT}, {"bot": weak}, tenants={"group": Tenants()}, log=lambda line: None)
            self.assertIn("bot", str(ctx.exception))
            self.assertNotIn(weak, str(ctx.exception))
        gw = app.Gateway({}, {"bot": BOT}, {"bot": "x" * app.MIN_TOKEN_CHARS}, tenants={"group": Tenants()},
                         log=lambda line: None)
        self.addCleanup(gw.close)
        self.assertTrue(gw.has_token("bot"))

    def test_weak_minting_secret_answers_yes_or_no(self):
        self.assertTrue(app.minting_secret_looks_weak(SECRET))  # 40 characters
        self.assertTrue(app.minting_secret_looks_weak("ab" * 30))  # long, but two distinct characters
        self.assertFalse(app.minting_secret_looks_weak(SECRET_STRONG))
        self.assertEqual((app.RANDOM_SECRET_CHARS, app.RANDOM_SECRET_DISTINCT), (43, 16))
        self.assertTrue(self.gw.weak_minting_secret("bot"))
        self.assertFalse(self.gw.weak_minting_secret("owner"))  # a plain token: not a minting secret
        self.assertFalse(self.gw.weak_minting_secret("nobody"))
        gw = self.build(tokens={"bot": SECRET_STRONG, "owner": OWNER_TOKEN})
        self.assertIs(gw.weak_minting_secret("bot"), False)

    def test_raising_the_generation_voids_every_older_token(self):
        bumped = ClientPolicy("bot", recall=("@tenant",), write="@tenant", tenants="group", tenant_binding="token",
                              tenant_token_generation=2)
        gw = self.build(clients={"bot": bumped})
        self.assertIsNone(gw.identify(mint("10001")))
        self.assertIsNone(gw.identify(mint("10002")))
        self.assertEqual(gw.identify(mint("10001", generation=2)), ("bot", "10001"))

    def test_binding_needs_tenants_a_known_mode_and_a_positive_generation(self):
        bad = (ClientPolicy("x", recall=(), tenant_binding="token"),
               ClientPolicy("x", recall=(), tenants="group", tenant_binding="tokens"),
               ClientPolicy("x", recall=(), tenants="group", tenant_binding="token", tenant_token_generation=0),
               ClientPolicy("x", recall=(), tenants="group", tenant_binding="token", tenant_token_generation=True),
               ClientPolicy("x", recall=(), tenants="group", tenant_binding="token", tenant_token_generation="2"))
        for policy in bad:
            with self.assertRaises(ConfigError, msg=policy):
                app.Gateway({}, {"x": policy}, {"x": SECRET}, tenants={"group": Tenants()}, log=lambda line: None)


# ------------------------------------------------------------------------------ the request's tenant


class RequestTenantTests(GatewayCase):
    def texts(self, reply):
        self.assertEqual(reply.status, 200, reply.body)
        return [r["text"] for r in reply.body["results"]]

    def test_leaving_tenant_id_out_means_the_bound_tenant(self):
        self.assertEqual(self.texts(self.recall(mint("10001"))), ["tenant 10001 likes green tea"])
        self.assertEqual(self.texts(self.recall(mint("10002"))), ["tenant 10002 likes green tea"])
        self.assertEqual(self.tenants.asked, ["10001", "10002"])

    def test_the_bound_tenant_may_be_repeated(self):
        self.assertEqual(self.texts(self.recall(mint("10001"), tenant_id="10001")), ["tenant 10001 likes green tea"])

    def test_any_other_tenant_id_is_403_tenant_mismatch_and_reaches_nothing(self):
        mismatch = (403, {"error": "tenant_id does not match the token", "code": "tenant_mismatch"})
        token = mint("10001")
        for value in ("10002", "99999", "../x", "", " 10001", "10001\n", "１０００１", 10001, None, True, ["10001"],
                      {"id": "10001"}):
            self.assertEqual(self.recall(token, tenant_id=value)[:2], mismatch, repr(value))
            self.assertEqual(self.remember(token, tenant_id=value)[:2], mismatch, repr(value))
            used = self.call("used", token, {"used": [{"source": "group", "id": "x"}], "tenant_id": value})
            self.assertEqual(used[:2], mismatch, repr(value))
        self.assertEqual(self.tenants.asked, [])
        self.assertEqual(self.recall(token, tenant_id="10002").note, "tenant-mismatch")

    def test_the_context_cannot_name_a_tenant(self):
        reply = self.recall(mint("10001"), context={"tenant_id": "10002"})
        self.assertEqual(self.texts(reply), ["tenant 10001 likes green tea"])

    def test_writes_go_to_the_bound_tenant_only(self):
        self.assertEqual(self.remember(mint("10002"), "bound write about harbour lanterns").status, 200)
        store = self.tenants.stores["10002"]
        self.assertIn("bound write about harbour lanterns", [store.read(ref) for ref, _ in store.refs()])
        self.assertNotIn("10001", self.tenants.stores)

    def test_an_unregistered_tenant_is_403_unknown_tenant(self):
        self.assertEqual(self.recall(mint("30003"))[:2], (403, {"error": "unknown tenant", "code": "unknown_tenant"}))
        self.assertEqual(self.remember(mint("30003")).body["code"], "unknown_tenant")

    def test_in_process_calls_are_never_guessed(self):
        body = {"query": "green tea", "tenant_id": "10001"}
        self.assertEqual(self.gw.recall("bot", body).status, 401)  # the body never stands in for the token
        self.assertEqual(self.gw.recall("trusted", body, "10001").status, 401)
        self.assertEqual(self.gw.recall("owner", {"query": "green tea"}, "10001").status, 401)
        self.assertEqual(self.gw.remember("bot", {"messages": [{"role": "user", "content": "x"}]}).status, 401)
        self.assertEqual(self.gw.used("bot", {"used": []}).status, 401)
        self.assertEqual(self.gw.recall("bot", {"query": "green tea"}, "10001").status, 200)

    def test_admit_needs_the_bound_tenant_exactly_when_the_client_is_bound(self):
        self.assertEqual(self.gw.admit("bot", "recall").status, 401)
        self.assertEqual(self.gw.admit("owner", "recall", "10001").status, 401)
        self.assertEqual(self.gw.admit("trusted", "recall", "10001").status, 401)
        for bad in ("abc", "../x", "", 10001):  # only an in-process caller can hand these over
            self.assertEqual(self.gw.admit("bot", "recall", bad)[:2],
                             (400, {"error": "bad tenant_id", "code": "bad_tenant"}), repr(bad))
        self.assertEqual(len(self.gw.limiter), 0)  # none of these spent anything
        self.assertEqual(self.tenants.asked, [])
        self.assertIsNone(self.gw.admit("bot", "recall", "10001"))

    def test_request_mode_is_unchanged(self):
        self.assertEqual(TRUSTED.tenant_binding, "request")
        self.assertEqual(self.texts(self.recall(TRUSTED_TOKEN, tenant_id="10002")), ["tenant 10002 likes green tea"])
        self.assertEqual(self.recall(TRUSTED_TOKEN)[:2], (400, {"error": "bad tenant_id", "code": "bad_tenant"}))
        self.assertEqual(self.recall(OWNER_TOKEN, tenant_id="10001").body["code"], "bad_tenant")
        self.assertEqual(self.texts(self.recall(OWNER_TOKEN)), ["owner private diary green tea"])


class LimitTests(GatewayCase):
    def bot(self, **options):
        return ClientPolicy("bot", recall=("@tenant",), write="@tenant", tenants="group", tenant_binding="token",
                            **options)

    def test_the_rate_limit_is_per_tenant_and_in_total(self):
        self.gw = self.build(clients={"bot": self.bot(rate_limit=2, rate_limit_total=3)})
        self.assertEqual([self.recall(mint("10001")).status for _ in range(5)], [200, 200, 429, 429, 429])
        # The tenant's own bucket is asked first: its refused requests spent nothing of the total.
        self.assertEqual(self.recall(mint("10002")).status, 200)  # the client's 3rd counted request
        self.assertEqual(self.recall(mint("10002")).status, 429)  # the total of 3 is used up
        gw = self.build(clients={"bot": self.bot(rate_limit=2)})
        self.gw = gw
        self.assertEqual([self.recall(mint("10001")).status for _ in range(3)], [200, 200, 429])
        self.assertEqual(self.recall(mint("10002")).status, 200)

    def test_one_tenant_cannot_lock_the_other_tenants_out(self):
        # rate_limit 3 per tenant, 10 in total. Tenant 10001 and a token of a tenant that has been
        # removed (30003) flood every route with requests good and bad; each spends at most its own
        # 3 of the total, so 10002 still gets its 3 and 10003 the one that is left.
        self.gw = self.build(clients={"bot": self.bot(rate_limit=3, rate_limit_total=10)},
                             known=("10001", "10002", "10003"))
        flood = []
        for token in (mint("10001"), mint("30003")):
            for n in range(150):
                flood += [self.recall(token).status,
                          self.recall(token, tenant_id="10002").status,  # tenant_mismatch
                          self.call("recall", token, {"tenant_id": "10002"}).status,  # no query
                          self.remember(token, "flood %d" % n).status,
                          self.call("remember", token, {"messages": "not a list"}).status,
                          self.call("used", token, {"used": [{"source": "group", "id": "x"}], "tenant_id": "10003"}).status]
        self.assertEqual(flood.count(429), len(flood) - 6)  # 3 got past the limit per flooding token
        self.assertEqual(set(self.tenants.asked), {"10001", "30003"})  # the flood named no other tenant's store
        self.assertEqual([self.recall(mint("10002")).status for _ in range(4)], [200, 200, 200, 429])  # its own 3
        self.assertEqual([self.recall(mint("10003")).status for _ in range(2)], [200, 429])  # the 10th, then the total

    def test_one_tenant_cannot_lock_the_others_out_in_process_either(self):
        from feiyu_memgate.client import LocalClient

        self.gw = self.build(clients={"bot": self.bot(rate_limit=3, rate_limit_total=6)})
        local = LocalClient(self.gw, tenant_client="bot", start=False, logger=mock.Mock())
        self.assertEqual([local.recall_detail("green tea", tenant_id="10001") is None for _ in range(200)].count(False), 3)
        self.assertEqual([local.recall_detail("green tea", tenant_id="10002") is None for _ in range(4)],
                         [False, False, False, True])

    def test_in_request_mode_one_caller_can_use_up_the_total(self):
        # Kept on purpose (docs/security.md): the total is asked before the caller-chosen tenant id is
        # read, so inventing ids cannot multiply the budget, and a single caller can spend all of it.
        trusted = ClientPolicy("trusted", recall=("@tenant",), write="@tenant", tenants="group", rate_limit=2,
                               rate_limit_total=4)
        self.gw = self.build(clients={"trusted": trusted})
        self.assertEqual([self.recall(TRUSTED_TOKEN, tenant_id="10001").status for _ in range(5)],
                         [200, 200, 429, 429, 429])
        self.assertEqual(self.recall(TRUSTED_TOKEN, tenant_id="10002").status, 429)
        self.assertEqual([self.recall(TRUSTED_TOKEN, tenant_id=str(n)).status for n in range(20001, 20004)],
                         [429, 429, 429])

    def test_daily_caps_are_per_tenant_and_in_total(self):
        guards = [RedactGuard(), DailyCapGuard(wall=lambda: FIXED_DAY)]
        self.gw = self.build(clients={"bot": self.bot(daily_writes=2, daily_writes_total=3)}, guards=guards)
        one = [self.remember(mint("10001"), "write %d" % n).status for n in range(3)]
        self.assertEqual(one, [200, 200, 429])
        self.assertEqual(self.remember(mint("10001")).body["code"], "daily_limit")
        self.assertEqual(self.remember(mint("10002")).status, 200)
        self.assertEqual(self.remember(mint("10002")).body["code"], "daily_limit")  # the total of 3 is used up

    def test_the_tenant_budget_is_keyed_by_provider_and_tenant(self):
        self.gw = self.build(tenant_pool_total=4, tenant_pool_per_tenant=1)
        self.assertTrue(self.gw.tenant_budget.acquire("group:10001"))
        self.assertEqual(self.recall(mint("10001"))[:2], (503, {"error": "busy", "code": "busy"}))
        # A request-mode client of the same provider shares that tenant's budget, as two clients do today.
        self.assertEqual(self.recall(TRUSTED_TOKEN, tenant_id="10001").status, 503)
        self.assertEqual(self.recall(mint("10002")).status, 200)
        self.gw.tenant_budget.release("group:10001")
        self.assertEqual(self.recall(mint("10001")).status, 200)


# ------------------------------------------------------------------------------ over HTTP


class HttpCase(unittest.TestCase):
    """A gateway built from a configuration (SQLite stores, a directory of tenant databases,
    forgetting on the tenants) behind the real HTTP server."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        d = self._tmp.name
        self.cfg = {
            "stores": {"private": {"type": "sqlite", "path": os.path.join(d, "private.db")}},
            "tenants": {"group": {"type": "directory", "path": os.path.join(d, "groups"), "id_pattern": "[0-9]{5,12}"}},
            "clients": {
                "owner": {"token_env": "FMG_T_OWNER", "recall": ["private"], "write": "private"},
                "bot": {"token_env": "FMG_T_MINT", "tenants": "group", "tenant_binding": "token",
                        "recall": ["@tenant"], "write": "@tenant", "pin_writes": True},
                "trusted": {"token_env": "FMG_T_TRUSTED", "tenants": "group", "recall": ["@tenant"], "write": "@tenant"},
            },
            "forgetting": {"ledger": os.path.join(d, "ledger.db"), "archive": os.path.join(d, "archive.db"),
                           "stores": ["group"], "sweep_every_hours": 0},
        }
        env = {"FMG_T_OWNER": OWNER_TOKEN, "FMG_T_MINT": SECRET, "FMG_T_TRUSTED": TRUSTED_TOKEN}
        self.logs = []
        self.gw = config.build_gateway(self.cfg, env=env, log=self.logs.append)
        for tenant in ("10001", "10002"):
            self.gw.tenants["group"].add(tenant)
        self.server = http_server.make_server(self.gw, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()  # closes the gateway too
        self.thread.join(5)

    def request(self, path, body, token):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        try:
            headers = {"Content-Type": "application/json"}
            if token is not None:
                headers["Authorization"] = "Bearer " + token
            conn.request("POST", path, body=json.dumps(body).encode("utf-8"), headers=headers)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()


class HttpTests(HttpCase):
    def test_a_bound_token_writes_recalls_and_reports_use_for_its_tenant_only(self):
        token = mint("10001")
        status, body = self.request("/v1/remember", {"messages": [{"role": "user", "content": "bound memo about kites"}]},
                                    token)
        self.assertEqual((status, body["status"]), (200, "ok"))
        status, body = self.request("/v1/recall", {"query": "bound memo about kites"}, token)
        self.assertEqual(status, 200)
        self.assertEqual([(r["source"], r["text"]) for r in body["results"]], [("group", "bound memo about kites")])
        ref = body["results"][0]["id"]
        status, used = self.request("/v1/used", {"used": [{"source": "group", "id": ref}]}, token)
        self.assertEqual((status, used), (200, {"status": "ok", "recorded": 1}))
        ledger = self.gw.forgetting.ledger
        entry = ledger.get_many("group:10001", [ref])[ref]
        self.assertEqual(entry.uses, 1)
        self.assertTrue(entry.pinned)  # pin_writes keyed by the bound tenant
        self.assertEqual(ledger.entries("group:10002"), [])
        self.assertEqual(self.gw.tenants["group"].store("10002").count(), 0)
        # The other tenant's token does not see it.
        status, body = self.request("/v1/recall", {"query": "bound memo about kites"}, mint("10002"))
        self.assertEqual((status, body["results"]), (200, []))

    def test_the_minting_secret_is_refused_exactly_like_a_wrong_token(self):
        unauthorized = (401, {"error": "unauthorized", "code": "unauthorized"})
        for token in (SECRET, "wrong-token-".ljust(40, "x")):
            self.assertEqual(self.request("/v1/recall", {"query": "x"}, token), unauthorized)
            self.assertEqual(self.request("/v1/recall", {"query": "x", "tenant_id": "10001"}, token), unauthorized)
        self.assertEqual(self.request("/v1/recall", {"query": "x"}, mint("10001")[:-1]), unauthorized)
        self.assertEqual(self.request("/v1/recall", {"query": "x"}, mint("10001")[:-1] + "é"), unauthorized)

    def test_another_tenant_in_the_body_is_403(self):
        status, body = self.request("/v1/recall", {"query": "x", "tenant_id": "10002"}, mint("10001"))
        self.assertEqual((status, body), (403, {"error": "tenant_id does not match the token", "code": "tenant_mismatch"}))
        status, _ = self.request("/v1/remember", {"messages": [{"role": "user", "content": "x"}], "tenant_id": "10002"},
                                 mint("10001"))
        self.assertEqual(status, 403)
        self.assertEqual(self.gw.tenants["group"].store("10002").count(), 0)
        self.assertTrue(wait_until(lambda: any("/v1/recall 403 client=bot" in line and "tenant-mismatch" in line for line in self.logs)))

    def test_an_unregistered_tenant_is_403_and_removing_a_tenant_cuts_it_off(self):
        self.assertEqual(self.request("/v1/recall", {"query": "x"}, mint("30003"))[1]["code"], "unknown_tenant")
        self.assertEqual(self.request("/v1/recall", {"query": "x"}, mint("10002"))[0], 200)
        self.gw.tenants["group"].remove("10002")
        self.assertEqual(self.request("/v1/recall", {"query": "x"}, mint("10002"))[1]["code"], "unknown_tenant")

    def test_request_mode_is_unchanged_over_http(self):
        self.assertEqual(self.request("/v1/recall", {"query": "x", "tenant_id": "10002"}, TRUSTED_TOKEN)[0], 200)
        self.assertEqual(self.request("/v1/recall", {"query": "x"}, TRUSTED_TOKEN)[1]["code"], "bad_tenant")

    def test_logs_hold_no_token_no_secret_and_no_tenant_id(self):
        tokens = [mint("10001"), mint("10002"), mint("30003")]
        for token in tokens + [SECRET]:
            self.request("/v1/recall", {"query": "x"}, token)
            self.request("/v1/recall", {"query": "x", "tenant_id": "10002"}, token)
        joined = "\n".join(self.logs)
        for needle in tokens + [SECRET, "10001", "10002", "30003"]:
            self.assertNotIn(needle, joined)


class ClientTests(HttpCase):
    def setUp(self):
        super().setUp()
        # Keep the expected warnings off stderr.
        logging.getLogger("feiyu_memgate.client").addHandler(logging.NullHandler())

    def test_a_bound_client_needs_no_tenant_id(self):
        client = MemoryClient(self.url, None, tenant_token=mint("10001"), tenant_bound=True)
        self.assertTrue(client.configured)
        self.assertTrue(client.tenant_configured)
        self.assertEqual(client.remember_status("bound client memo about tea"), "saved")
        self.assertEqual([r["text"] for r in client.recall("bound client memo about tea")], ["bound client memo about tea"])
        self.assertEqual(len(client.recall("bound client memo about tea", tenant_id="10001")), 1)
        self.assertEqual(client.recall("bound client memo about tea", tenant_id="10002"), [])
        self.assertEqual(client.remember_status("elsewhere", tenant_id="10002"), "failed")  # tenant_mismatch: definite
        self.assertEqual(self.gw.tenants["group"].store("10002").count(), 0)

    def test_a_bound_client_with_a_main_token_stays_off(self):
        client = MemoryClient(self.url, OWNER_TOKEN, tenant_token=mint("10001"), tenant_bound=True)
        self.assertFalse(client.configured)
        self.assertFalse(client.tenant_configured)
        self.assertEqual(client.remember_status("must not land anywhere"), "failed")
        self.assertEqual(self.gw.stores["private"].count(), 0)

    def test_without_the_option_nothing_changes(self):
        client = MemoryClient(self.url, None, tenant_token=mint("10001"))
        self.assertFalse(client.configured)
        self.assertEqual(client.remember_status("not sent"), "failed")
        self.assertEqual(client.remember_status("sent", tenant_id="10001"), "saved")


class LocalClientTests(unittest.TestCase):
    """The in-process client with a tenant-bound client: no minting secret is needed, and the
    tenant a call names is the bound one."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.cfg = {
            "stores": {"private": {"type": "inmemory"}},
            "tenants": {"group": {"type": "directory", "path": os.path.join(self._tmp.name, "groups"),
                                  "id_pattern": "[0-9]{5,12}"}},
            "clients": {
                "owner": {"recall": ["private"], "write": "private"},
                "bot": {"token_env": "FMG_L_MINT", "tenants": "group", "tenant_binding": "token",
                        "recall": ["@tenant"], "write": "@tenant"},
            },
        }

    def test_the_minting_secret_may_stay_unset_and_its_note_is_not_logged(self):
        from feiyu_memgate.client import LocalClient

        lines = []
        local = LocalClient.from_config(self.cfg, client="owner", tenant_client="bot", env={}, log=lines.append,
                                        logger=mock.Mock())
        self.addCleanup(local.close)
        self.assertFalse(local.gateway.has_token("bot"))
        self.assertEqual([line for line in lines if "minting secret" in line or "has no token" in line], [])
        for tenant in ("10001", "10002"):
            local.gateway.tenants["group"].add(tenant)
        self.assertEqual(local.remember_status("in process memo about herons", tenant_id="10001"), "saved")
        self.assertEqual(local.remember_status("in process memo about herons", tenant_id=10002), "saved")
        self.assertEqual(len(local.recall("memo about herons", tenant_id="10001")), 1)
        self.assertEqual(local.remember_status("nowhere", tenant_id="30003"), "failed")  # unknown tenant
        self.assertEqual(local.recall("memo about herons"), [])  # the owner's store holds none of it
        self.assertTrue(any(line.startswith("LOCAL /v1/remember 200 client=bot") for line in lines))
        self.assertFalse(any("10001" in line or "herons" in line for line in lines))

    def test_a_tenant_bound_client_cannot_be_the_client_for_calls_without_a_tenant(self):
        from feiyu_memgate.client import LocalClient

        with self.assertRaises(ConfigError) as ctx:
            LocalClient.from_config(self.cfg, client="bot", env={}, log=lambda line: None)
        self.assertIn("tenant_client", str(ctx.exception))
        local = LocalClient.from_config(self.cfg, client="owner", env={}, log=lambda line: None)
        self.addCleanup(local.close)
        with self.assertRaises(ConfigError):
            local.for_client("bot")
        self.assertTrue(local.for_client("owner", "bot").tenant_configured)


# ------------------------------------------------------------------------------ configuration and CLI


class ConfigTests(unittest.TestCase):
    def build(self, client, env=None):
        cfg = {"stores": {"private": {"type": "inmemory"}},
               "tenants": {"group": {"type": "directory", "path": "unused-groups"}},
               "clients": {"bot": dict({"token_env": "FMG_C_MINT", "recall": ["@tenant"]}, **client)}}
        gw = config.build_gateway(cfg, env=env if env is not None else {"FMG_C_MINT": SECRET}, log=lambda line: None)
        self.addCleanup(gw.close)
        return gw

    def test_the_two_options_are_read(self):
        gw = self.build({"tenants": "group", "tenant_binding": "token", "tenant_token_generation": 4})
        policy = gw.clients["bot"]
        self.assertEqual((policy.tenant_binding, policy.tenant_token_generation, policy.token_bound), ("token", 4, True))
        self.assertEqual(gw.identify(mint("10001", generation=4)), ("bot", "10001"))
        self.assertEqual(self.build({"tenants": "group"}).clients["bot"].tenant_binding, "request")

    def test_misuse_is_refused_naming_the_setting(self):
        base = {"token_env": "FMG_C_MINT", "recall": ["@tenant"]}
        token = dict(base, tenants="group", tenant_binding="token")
        for spec, setting in ((dict(base, tenant_binding="token"), "tenant_binding"),  # no tenants
                              (dict(base, tenants="group", tenant_binding="tokens"), "tenant_binding"),
                              (dict(base, tenants="group", tenant_binding=1), "tenant_binding"),
                              (dict(base, tenants="group", tenant_token_generation=2), "tenant_token_generation"),
                              (dict(token, tenant_token_generation=0), "tenant_token_generation"),
                              (dict(token, tenant_token_generation="2"), "tenant_token_generation"),
                              (dict(token, tenant_token_generation=True), "tenant_token_generation"),
                              ({k: v for k, v in token.items() if k != "token_env"}, "token_env")):  # no secret
            with self.assertRaises(ConfigError, msg=spec) as ctx:
                config.client_policy("bot", spec)
            self.assertIn("clients.bot.%s" % setting, str(ctx.exception))


class ExampleTests(unittest.TestCase):
    def test_the_commented_token_mode_example_builds_and_mints(self):
        path = Path(__file__).resolve().parent.parent / "examples" / "multi-tenant" / "gateway.toml"
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        start = lines.index("# [clients.group_bound]")
        block = []
        for line in lines[start:]:
            if not line.startswith("# "):
                break
            block.append(line[2:])
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cfg = tomllib.loads(text + "\n" + "\n".join(block) + "\n")
            for spec in list(cfg["stores"].values()) + list(cfg["tenants"].values()):
                spec["path"] = os.path.join(tmp, os.path.basename(spec["path"]))
            env = {client["token_env"]: "example-fake-secret-%02d-" % i + "x" * 32
                   for i, client in enumerate(cfg["clients"].values())}
            gw = config.build_gateway(cfg, env=env, log=lambda line: None)
            try:
                self.assertTrue(gw.clients["group_bound"].token_bound)
                self.assertFalse(gw.clients["group"].token_bound)
                token = tenant_tools.mint_token(cfg, "group_bound", "10001", env)
                self.assertEqual(gw.identify(token), ("group_bound", "10001"))
            finally:
                gw.close()


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


CLI_CONFIG = """
[stores.private]
type = "inmemory"

[tenants.group]
type = "directory"
path = "{groups}"
id_pattern = "[0-9]{{5,12}}"

[clients.owner]
token_env = "FMG_CLI_OWNER"
recall = ["private"]

[clients.bot]
token_env = "FMG_CLI_MINT"
tenants = "group"
tenant_binding = "token"
tenant_token_generation = {generation}
recall = ["@tenant"]
{bot_extra}

[clients.trusted]
token_env = "FMG_CLI_TRUSTED"
tenants = "group"
recall = ["@tenant"]
"""


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        saved = list(sys.path)  # config.load puts the configuration's folder on sys.path
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), saved))
        self.dir = Path(self._tmp.name)
        self.path = self.write(1)
        self.env = {"FMG_CLI_OWNER": OWNER_TOKEN, "FMG_CLI_MINT": SECRET, "FMG_CLI_TRUSTED": TRUSTED_TOKEN}

    def write(self, generation, bot_extra=""):
        path = self.dir / ("gateway-%d%s.toml" % (generation, "-extra" if bot_extra else ""))
        path.write_text(CLI_CONFIG.format(groups=(self.dir / "groups").as_posix(), generation=generation,
                                          bot_extra=bot_extra), encoding="utf-8")
        return str(path)

    def check(self, path=None, env=None):
        with mock.patch.dict(os.environ, env if env is not None else self.env):
            code, out, err = run("check", "--config", path or self.path)
        self.assertEqual(code, 0, err)
        return out.splitlines()

    def token(self, *extra, path=None, env=None, unset=()):
        with mock.patch.dict(os.environ, env if env is not None else self.env):
            for name in unset:
                os.environ.pop(name, None)  # put back by patch.dict
            return run("tenants", "token", "--config", path or self.path, *extra)

    def assert_refused(self, result, *words):
        code, out, err = result
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith("错误："), err)
        for word in words:
            self.assertIn(word, err)
        self.assertNotIn(SECRET, err)

    def test_prints_one_token_and_nothing_else(self):
        code, out, err = self.token("--client", "bot", "--tenant", "10001")
        self.assertEqual((code, out, err), (0, mint("10001") + "\n", ""))
        self.assertNotIn(SECRET, out)
        self.assertFalse((self.dir / "groups").exists())  # minting opens no store

    def test_the_generation_comes_from_the_configuration(self):
        code, out, _ = self.token("--client", "bot", "--tenant", "10001", path=self.write(3))
        self.assertEqual((code, out), (0, mint("10001", generation=3) + "\n"))

    def test_refusals(self):
        self.assert_refused(self.token("--client", "trusted", "--tenant", "10001"), "trusted", "token")
        self.assert_refused(self.token("--client", "owner", "--tenant", "10001"))
        self.assert_refused(self.token("--client", "nobody", "--tenant", "10001"), "nobody")
        for bad in ("abc", "1234", "../10001", "10001\n", ""):
            self.assert_refused(self.token("--client", "bot", "--tenant", bad), "group")
        self.assert_refused(self.token("--client", "bot", "--tenant", "10001", unset=("FMG_CLI_MINT",)), "FMG_CLI_MINT")
        for unusable in ("short", "change-me"):
            self.assert_refused(self.token("--client", "bot", "--tenant", "10001",
                                           env=dict(self.env, FMG_CLI_MINT=unusable)), "FMG_CLI_MINT")
        self.assert_refused(self.token("--client", "bot"), "--tenant")
        self.assert_refused(self.token("--client", "bot", "--tenant", "10001", path=str(self.dir / "missing.toml")),
                            "配置有问题")

    def test_the_key_file_commands_still_need_their_file(self):
        code, out, _ = run("tenants", "list")
        self.assertEqual(code, 1)
        self.assertIn("--keys", out)

    def test_check_shows_each_binding_and_warns_about_request_mode(self):
        with mock.patch.dict(os.environ, self.env):
            code, out, _ = run("check", "--config", self.path)
        self.assertEqual(code, 0)
        self.assertNotIn(SECRET, out)
        lines = out.splitlines()
        bot = [line for line in lines if line.startswith("  bot:")]
        trusted = [line for line in lines if line.startswith("  trusted:")]
        self.assertIn("租户绑定 token", bot[0])
        self.assertIn("有可用的铸造密钥", bot[0])
        self.assertIn("租户绑定 request", trusted[0])
        warnings = [line for line in lines if "租户号由调用方自报" in line]
        self.assertEqual(len(warnings), 1)
        self.assertIn("trusted", warnings[0])
        self.assertIn("tenant_binding = \"token\"", warnings[0])
        self.assertNotIn("租户绑定", [line for line in lines if line.startswith("  owner:")][0])

    def test_check_says_when_a_minting_secret_does_not_look_random(self):
        weak = [line for line in self.check() if "不是随机生成" in line]
        self.assertEqual(len(weak), 1)
        self.assertIn("bot", weak[0])
        self.assertIn("secrets.token_urlsafe(32)", weak[0])
        self.assertNotIn(SECRET, weak[0])
        self.assertNotIn(str(len(SECRET)), weak[0])  # nor its length
        few = "ab" * 30  # long enough, two distinct characters
        lines = self.check(env=dict(self.env, FMG_CLI_MINT=few))
        weak = [line for line in lines if "不是随机生成" in line]
        self.assertEqual(len(weak), 1)
        self.assertNotIn("60", weak[0])
        self.assertFalse(any(few in line for line in lines))
        lines = self.check(env=dict(self.env, FMG_CLI_MINT=SECRET_STRONG))
        self.assertEqual([line for line in lines if "不是随机生成" in line], [])
        self.assertFalse(any(SECRET_STRONG in line for line in lines))

    def test_check_warns_about_a_total_daily_cap_without_a_per_tenant_one(self):
        reminder = "设了 daily_writes_total 却没设 daily_writes"
        lines = self.check(self.write(1, "daily_writes_total = 100"))
        self.assertEqual(len([line for line in lines if reminder in line and "bot" in line]), 1)
        lines = self.check(self.write(1, "daily_writes_total = 100\ndaily_writes = 10"))
        self.assertEqual([line for line in lines if reminder in line], [])
        self.assertEqual([line for line in self.check() if reminder in line], [])


if __name__ == "__main__":
    unittest.main()
