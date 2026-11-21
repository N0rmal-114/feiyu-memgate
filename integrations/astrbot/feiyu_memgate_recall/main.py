"""Example AstrBot plugin: recall memories before each reply, and a `remember` tool.

Written against AstrBot v4.29's plugin interface; AstrBot is not needed to read it.
Configuration comes only from the environment:

    FEIYU_MEMGATE_URL            gateway address, e.g. http://127.0.0.1:8080
    FEIYU_MEMGATE_TOKEN          token of the owner client
    FEIYU_MEMGATE_TENANT_TOKEN   token of the tenant client (group chats)
    FEIYU_MEMGATE_OWNER_IDS      owner accounts (comma separated), each written <platform>:<id>, for example
                                 aiocqhttp:10001; the platform is event.get_platform_name(). Entries without
                                 a ":" are ignored (one warning at start). The same number on another
                                 platform is another account and does not match
    FEIYU_MEMGATE_FALLBACK       where the memory block goes on an AstrBot without temporary message
                                 parts: "message" (default), "system" or "off"; see attach_memory

Who gets memory:
  - a private chat whose "<platform>:<sender id>" is listed in FEIYU_MEMGATE_OWNER_IDS: the owner token;
  - a group chat: the tenant token, with the group id as tenant_id (so every group has a
    store of its own), and at most one remember per reply;
  - anyone else: no recall and no remember.

Recall and remember also send context={"channel": <platform name>, "chat": "private" | "group"}.
"""
import asyncio
import datetime
import os
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star

from feiyu_memgate.client import BlockFormatter, MemoryClient, build_query, previous_user_text

RECALL_TIMEOUT = 8.5   # the client's own deadline for one recall
RECALL_BUDGET = 9.0    # how long the reply may wait for it at most
REMEMBER_BUDGET = 12.0
REMEMBERED_KEY = "feiyu_memgate_remembered"  # a group event hands over at most one memory

# Source names are the store names of the gateway configuration; use yours here.
FORMATTER = BlockFormatter(labels={"private": "你们私聊里的", "group": "这个群的"})


def owner_ids(env=os.environ):
    """The owner accounts as "<platform>:<id>" strings. An entry without a ":" is left out: a bare number
    would match the same number on every platform."""
    entries = [i.strip() for i in env.get("FEIYU_MEMGATE_OWNER_IDS", "").split(",") if i.strip()]
    kept = {i for i in entries if all(i.partition(":")[::2])}  # both a platform and an id
    skipped = sum(1 for i in entries if i not in kept)
    if skipped:  # the count only, never the ids
        logger.warning("FEIYU_MEMGATE_OWNER_IDS: %d entries ignored, each must be <platform>:<id>, "
                       "for example aiocqhttp:12345", skipped)
    return kept


FALLBACKS = ("message", "system", "off")
_warned = set()  # one log line per kind of fallback, not one per reply


def fallback_mode(env=os.environ):
    mode = env.get("FEIYU_MEMGATE_FALLBACK", "message").strip().lower()
    return mode if mode in FALLBACKS else "message"


def _warn_once(key, text):
    if key not in _warned:
        _warned.add(key)
        logger.warning(text)


def attach_memory(req: ProviderRequest, block: str, fallback: str = "message") -> str:
    """Hand the block to the model with the current message; returns where it went.

    "temp"     a temporary part of the current message: AstrBot sends it to the model and leaves it
               out of the restored history. Nothing before the current message changes, so the
               system prompt and the history keep hitting the provider's prompt cache. The normal path.
    "message"  for an AstrBot without temporary parts: appended to the text of the current message.
               The system prompt and the earlier history still do not change, so the cached prefix
               holds; the cost is that the host keeps the block in the history with that message.
    "system"   only when FEIYU_MEMGATE_FALLBACK=system: appended to the system prompt. That changes
               the very start of the prompt on every reply, so nothing after it is served from the
               cache. Kept for hosts where the other two are impossible.
    "skipped"  nothing was attached (fallback "off", or the request has no usable field).
    """
    parts = getattr(req, "extra_user_content_parts", None)
    if isinstance(parts, list):
        try:
            from astrbot.core.agent.message import TextPart
            parts.append(TextPart(text=block).mark_as_temp())
            return "temp"
        except Exception:  # noqa: BLE001 - the temporary part is optional; fall back to the message
            pass
    prompt = getattr(req, "prompt", False)
    if fallback == "message" and (prompt is None or isinstance(prompt, str)):
        _warn_once("message", "memory block: no temporary message part, appended to the current message "
                              "(it stays in the history on this AstrBot)")
        req.prompt = (prompt + "\n\n" + block) if prompt else block
        return "message"
    if fallback == "system" and isinstance(getattr(req, "system_prompt", None), str):
        _warn_once("system", "memory block: appended to the system prompt; the prompt cache is lost on every reply")
        req.system_prompt = (req.system_prompt + "\n\n" + block) if req.system_prompt else block
        return "system"
    _warn_once("skipped", "memory block: no place to put it on this AstrBot, recall is not shown to the model")
    return "skipped"


class FeiyuMemgateRecall(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.client = MemoryClient.from_env()
        self.owners = owner_ids()
        self.fallback = fallback_mode()
        if not self.client.configured and not self.client.tenant_configured:
            logger.warning("feiyu memgate is off: set FEIYU_MEMGATE_URL and FEIYU_MEMGATE_TOKEN")

    def _scope(self, event: AstrMessageEvent):
        """(allowed, tenant_id) for this event. The ids come from the event only, never from the model."""
        group_id = event.get_group_id() or None
        if group_id is not None:
            return self.client.tenant_configured, str(group_id)
        platform, sender = event.get_platform_name(), event.get_sender_id()
        account = "%s:%s" % (platform, sender) if platform and sender else None  # never half an account
        owner = event.is_private_chat() and account in self.owners and self.client.configured
        return owner, None

    @staticmethod
    def _context(event: AstrMessageEvent):
        """What the gateway's stages and guards may know about where this came from (never identity)."""
        return {"channel": str(event.get_platform_name()), "chat": "group" if event.get_group_id() else "private"}

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        try:
            allowed, tenant_id = self._scope(event)
            if not allowed:
                return
            # On a host that keeps the block in the history (the "message" fallback) the previous user
            # message may carry an old block: take it out, a query is the user's words only.
            previous_raw = previous_user_text(getattr(req, "contexts", None))
            query = build_query(event.message_str, FORMATTER.strip(previous_raw) or None)
            if query is None:
                return
            detail = await asyncio.wait_for(
                asyncio.to_thread(self.client.recall_detail, query, RECALL_TIMEOUT, tenant_id,
                                  context=self._context(event)), RECALL_BUDGET)
            if detail is None:
                logger.warning("memory recall skipped: failed")  # never the query
                return
            block = FORMATTER.format(detail["results"], detail["more"], today=datetime.date.today().isoformat())
            if not block:
                return
            if isinstance(previous_raw, str) and block in previous_raw:
                return  # the same memories are already in the history, right before this message
            attach_memory(req, block, self.fallback)
        except Exception as e:  # noqa: BLE001 - a memory problem must never stop the reply
            logger.warning("memory recall skipped: %s", type(e).__name__)

    @filter.llm_tool(name="remember")
    async def remember_tool(self, event: AstrMessageEvent, text: str) -> str:
        """记住一条关于用户的信息。只在用户明确要求记住某件事，或表达了稳定的偏好、身份、长期计划时使用；普通闲聊、一次性的话题不要记。

        Args:
            text(string): 要记住的内容，用一两句话写清楚，写成第三人称陈述
        """
        allowed, tenant_id = self._scope(event)
        if not allowed:
            return "这里没有开放记忆功能，这条没有记。请如实告诉用户。"
        if not isinstance(text, str) or not text.strip():
            return "没记住：内容是空的。"
        if tenant_id is not None:
            if event.get_extra(REMEMBERED_KEY):
                return "这次回复里已经记过一条了，这条没有记。"
            event.set_extra(REMEMBERED_KEY, True)
        # The local date and time with offset: the memory is dated by where the user is, not by UTC.
        message = {"role": "user", "content": text,
                   "time": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
        deadline = time.monotonic() + REMEMBER_BUDGET
        try:
            outcome = await asyncio.wait_for(
                asyncio.to_thread(self.client.remember_messages, [message], deadline, tenant_id,
                                  context=self._context(event)), REMEMBER_BUDGET + 0.5)
        except asyncio.TimeoutError:
            outcome = "unsure"  # the worker may still finish: the write may happen
        except Exception as e:  # noqa: BLE001 - a failed write is logged by type and reported as failed
            logger.warning("memory remember failed: %s", type(e).__name__)
            outcome = "failed"
        if tenant_id is not None and outcome != "saved":
            # Group members must not learn about the memory service's state.
            return "这条没有记。"
        if outcome == "limited":
            return "今天能交给记忆服务的次数用完了，这条没有交出去。请如实告诉用户，用你自己的话说。"
        if outcome == "skipped":
            # The gateway looked at it and kept nothing (a filter decided it is not worth keeping).
            return "记忆服务看过了这条，判断没有需要记下的内容，所以没有存。请如实告诉用户，用你自己的话说。"
        if outcome == "unsure":
            return ("没能确认是否保存成功：记忆服务没有及时回应，这条内容可能已经记住，也可能没有。"
                    "请如实告诉用户结果不确定，不要下定论。")
        if outcome != "saved":
            return "没记住：记忆服务暂时不可用，请如实告诉用户这次没有记住。"
        if tenant_id is not None:
            return "已记住。"
        # Handed to the memory service, which sorts it later; not yet a kept memory.
        return "这条已经交给记忆服务了，整理要过一会儿，还不能保证一定记下来。请如实告诉用户，用你自己的话说。"
