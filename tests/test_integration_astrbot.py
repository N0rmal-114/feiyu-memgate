"""The AstrBot example plugin, run against stand-ins for AstrBot's plugin interface.

AstrBot is not installed here: the modules the plugin imports are replaced by small fakes that
carry the names and fields it uses (ProviderRequest.prompt / contexts / system_prompt /
extra_user_content_parts, TextPart.mark_as_temp). This checks the plugin's own logic, most of all
where the memory block is put; it does not prove anything about a real AstrBot.
"""
import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock

import feiyu_memgate.client  # noqa: F401 - loaded before sys.modules is patched, so it is not loaded twice

PLUGIN = pathlib.Path(__file__).resolve().parent.parent / "integrations" / "astrbot" / "feiyu_memgate_recall" / "main.py"
RESULTS = [{"text": "likes oolong tea in the evening", "source": "private", "date": "2026-01-02", "score": 0.9}]


class FakeLogger:
    def __init__(self):
        self.lines = []

    def warning(self, text, *args):
        self.lines.append(text % args if args else text)


class FakeTextPart:
    def __init__(self, text):
        self.text = text
        self.temporary = False

    def mark_as_temp(self):
        self.temporary = True
        return self


class Request:
    """The fields of AstrBot's ProviderRequest that the plugin touches."""

    def __init__(self, prompt="what tea do I like", contexts=None, system_prompt="You are a helpful bot.",
                 parts=True):
        self.prompt = prompt
        self.contexts = contexts if contexts is not None else []
        self.system_prompt = system_prompt
        if parts:
            self.extra_user_content_parts = []


class OldRequest:
    """A request of a host that has neither temporary parts nor a prompt field."""

    def __init__(self):
        self.contexts = []
        self.system_prompt = "You are a helpful bot."


class Event:
    def __init__(self, text="what tea do I like", sender="10001", group=None):
        self.message_str = text
        self._sender, self._group = sender, group
        self._extra = {}

    def get_group_id(self):
        return self._group

    def is_private_chat(self):
        return self._group is None

    def get_sender_id(self):
        return self._sender

    def get_extra(self, key):
        return self._extra.get(key)

    def set_extra(self, key, value):
        self._extra[key] = value


class FakeClient:
    configured = True
    tenant_configured = True

    def __init__(self, results=RESULTS):
        self.results = results
        self.queries = []

    def recall_detail(self, query, timeout=None, tenant_id=None):
        self.queries.append((query, tenant_id))
        return {"results": self.results, "more": 0, "failed": 0, "busy": 0}


def fake_astrbot(with_text_part=True):
    """sys.modules entries standing in for the parts of astrbot the plugin imports."""
    logger = FakeLogger()

    def passthrough(*args, **kwargs):
        return lambda fn: fn

    class Star:
        def __init__(self, context):
            self.context = context

    api = types.ModuleType("astrbot.api")
    api.logger = logger
    event = types.ModuleType("astrbot.api.event")
    event.AstrMessageEvent = Event
    event.filter = types.SimpleNamespace(on_llm_request=passthrough, llm_tool=passthrough)
    provider = types.ModuleType("astrbot.api.provider")
    provider.ProviderRequest = Request
    star = types.ModuleType("astrbot.api.star")
    star.Context, star.Star = object, Star
    modules = {"astrbot": types.ModuleType("astrbot"), "astrbot.api": api, "astrbot.api.event": event,
               "astrbot.api.provider": provider, "astrbot.api.star": star,
               "astrbot.core": types.ModuleType("astrbot.core"),
               "astrbot.core.agent": types.ModuleType("astrbot.core.agent")}
    if with_text_part:
        message = types.ModuleType("astrbot.core.agent.message")
        message.TextPart = FakeTextPart
        modules["astrbot.core.agent.message"] = message
    else:
        modules["astrbot.core.agent.message"] = None  # importing it raises ImportError
    return modules, logger


class PluginCase(unittest.TestCase):
    with_text_part = True

    def setUp(self):
        modules, self.logger = fake_astrbot(self.with_text_part)
        patcher = mock.patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        spec = importlib.util.spec_from_file_location("feiyu_memgate_recall_under_test", PLUGIN)
        self.plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.plugin)
        self.block = self.plugin.FORMATTER.format(RESULTS)

    def star(self, fallback="message", client=None):
        with mock.patch.dict("os.environ", {"FEIYU_MEMGATE_OWNER_IDS": "10001", "FEIYU_MEMGATE_FALLBACK": fallback}):
            star = self.plugin.FeiyuMemgateRecall(object())
        star.client = client or FakeClient()
        return star

    def ask(self, star, event, req):
        asyncio.run(star.on_llm_request(event, req))
        return req


class TemporaryPartTests(PluginCase):
    def test_the_block_is_a_temporary_part_and_nothing_else_changes(self):
        req = Request()
        self.assertEqual(self.plugin.attach_memory(req, self.block), "temp")
        self.assertEqual(len(req.extra_user_content_parts), 1)
        part = req.extra_user_content_parts[0]
        self.assertEqual(part.text, self.block)
        self.assertTrue(part.temporary)
        self.assertEqual(req.prompt, "what tea do I like")
        self.assertEqual(req.system_prompt, "You are a helpful bot.")
        self.assertEqual(self.logger.lines, [])

    def test_a_reply_recalls_and_attaches(self):
        star = self.star()
        req = self.ask(star, Event(), Request())
        self.assertEqual([p.text for p in req.extra_user_content_parts], [self.block])
        self.assertEqual(req.system_prompt, "You are a helpful bot.")
        self.assertEqual(star.client.queries, [("what tea do I like", None)])

    def test_a_group_uses_its_id_as_the_tenant_and_strangers_get_nothing(self):
        star = self.star()
        req = self.ask(star, Event(group="20002", sender="30003"), Request())
        self.assertEqual(star.client.queries, [("what tea do I like", "20002")])
        self.assertEqual(len(req.extra_user_content_parts), 1)
        stranger = self.ask(star, Event(sender="99999"), Request())
        self.assertEqual(stranger.extra_user_content_parts, [])
        self.assertEqual(len(star.client.queries), 1)

    def test_nothing_recalled_attaches_nothing(self):
        req = self.ask(self.star(client=FakeClient(results=[])), Event(), Request())
        self.assertEqual(req.extra_user_content_parts, [])
        self.assertEqual(req.prompt, "what tea do I like")

    def test_the_fallback_setting_is_read_once_and_defaults_to_message(self):
        self.assertEqual(self.plugin.fallback_mode({}), "message")
        self.assertEqual(self.plugin.fallback_mode({"FEIYU_MEMGATE_FALLBACK": " System "}), "system")
        self.assertEqual(self.plugin.fallback_mode({"FEIYU_MEMGATE_FALLBACK": "off"}), "off")
        self.assertEqual(self.plugin.fallback_mode({"FEIYU_MEMGATE_FALLBACK": "nonsense"}), "message")


class FallbackTests(PluginCase):
    """An AstrBot without temporary message parts."""

    with_text_part = False

    def test_the_block_goes_to_the_current_message_and_the_system_prompt_is_untouched(self):
        req = Request()
        self.assertEqual(self.plugin.attach_memory(req, self.block), "message")
        self.assertEqual(req.prompt, "what tea do I like\n\n" + self.block)
        self.assertEqual(req.system_prompt, "You are a helpful bot.")
        self.assertEqual(req.extra_user_content_parts, [])
        self.assertEqual(len(self.logger.lines), 1)
        self.assertNotIn("oolong", self.logger.lines[0])

    def test_a_request_without_a_parts_list_or_with_an_empty_prompt(self):
        req = Request(parts=False)
        self.assertEqual(self.plugin.attach_memory(req, self.block), "message")
        self.assertTrue(req.prompt.endswith(self.block))
        for empty in (None, ""):
            req = Request(prompt=empty)
            self.assertEqual(self.plugin.attach_memory(req, self.block), "message")
            self.assertEqual(req.prompt, self.block)

    def test_the_system_prompt_is_used_only_when_asked_for(self):
        req = Request()
        self.assertEqual(self.plugin.attach_memory(req, self.block, "system"), "system")
        self.assertEqual(req.system_prompt, "You are a helpful bot.\n\n" + self.block)
        self.assertEqual(req.prompt, "what tea do I like")
        self.assertIn("cache", self.logger.lines[-1])

    def test_off_and_a_request_with_no_usable_field_attach_nothing(self):
        req = Request()
        self.assertEqual(self.plugin.attach_memory(req, self.block, "off"), "skipped")
        self.assertEqual((req.prompt, req.system_prompt), ("what tea do I like", "You are a helpful bot."))
        old = OldRequest()
        self.assertEqual(self.plugin.attach_memory(old, self.block, "message"), "skipped")
        self.assertEqual(old.system_prompt, "You are a helpful bot.")
        self.assertFalse(hasattr(old, "prompt"))

    def test_each_kind_of_fallback_is_logged_once(self):
        for _ in range(3):
            self.plugin.attach_memory(Request(), self.block)
        self.assertEqual(len(self.logger.lines), 1)

    def test_the_system_prompt_stays_the_same_over_several_replies(self):
        star = self.star()
        seen = set()
        history = []
        for text in ("what tea do I like", "and in the morning?", "remind me again"):
            req = self.ask(star, Event(text=text), Request(prompt=text, contexts=list(history)))
            seen.add(req.system_prompt)
            history.append({"role": "user", "content": req.prompt})  # this host keeps what was sent
            history.append({"role": "assistant", "content": "ok"})
        self.assertEqual(seen, {"You are a helpful bot."})

    def test_an_old_block_in_the_history_is_not_part_of_the_next_query(self):
        star = self.star()
        history = [{"role": "user", "content": "what tea do I like\n\n" + self.block},
                   {"role": "assistant", "content": "oolong"}]
        self.ask(star, Event(text="why"), Request(prompt="why", contexts=history))  # short: the previous message is added
        query, _ = star.client.queries[-1]
        self.assertIn("what tea do I like", query)
        self.assertNotIn("memory_data", query)
        self.assertNotIn("oolong", query)

    def test_the_same_block_is_not_attached_twice_in_a_row(self):
        star = self.star()
        history = [{"role": "user", "content": "what tea do I like\n\n" + self.block},
                   {"role": "assistant", "content": "oolong"}]
        req = self.ask(star, Event(text="which tea was it exactly"),
                       Request(prompt="which tea was it exactly", contexts=history))
        self.assertEqual(req.prompt, "which tea was it exactly")
        other = FakeClient(results=[{"text": "drinks coffee at work", "source": "private"}])
        req = self.ask(self.star(client=other), Event(text="which tea was it exactly"),
                       Request(prompt="which tea was it exactly", contexts=history))
        self.assertIn("coffee", req.prompt)

    def test_off_through_the_plugin(self):
        req = self.ask(self.star(fallback="off"), Event(), Request())
        self.assertEqual((req.prompt, req.system_prompt), ("what tea do I like", "You are a helpful bot."))


if __name__ == "__main__":
    unittest.main()
