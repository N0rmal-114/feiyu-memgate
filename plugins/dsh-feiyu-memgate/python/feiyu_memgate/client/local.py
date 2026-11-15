"""In-process client: the same interface as MemoryClient, without a server, a port or a token.

    client = LocalClient.from_config("gateway.toml", client="owner")     # builds and starts a gateway
    client = MemoryClient("http://127.0.0.1:8080", token)                # the same calls over HTTP

Every method (recall_detail, recall, remember_messages, remember_status, remember, report_used,
configured, tenant_configured) is inherited from MemoryClient: the argument checks, the limits that
stop a request from being sent, the mapping from an answer to "saved" / "skipped" / "failed" /
"limited" / "unsure" / None, and the never-raise rule are one piece of code. Only the transport
differs: `_post` calls the gateway's own entry points instead of opening a socket, in the order the
HTTP server does: admit() (rate limit, then permission), then recall() / remember() / used(). So the
client policy, rate limits, daily caps, recall stages, write guards, redaction and the forgetting
ledger behave as they do behind `feiyu-memgate serve`.

What is different, on purpose:
- No authentication: the caller names the client policy it acts as. Anyone who can call this object
  holds that policy, and the process holds the store keys (docs/embedded.md).
- No body-size or JSON checks (there is no body); the gateway's own validation of shape and length
  still runs on the same dict a request would carry.
- timeout / deadline: the gateway's call runs in a short-lived daemon thread and is abandoned when
  the time is up, like a closed socket: recall gives None, remember "unsure" (it may still finish),
  report_used False. The gateway's own deadlines (recall, remember) apply inside as usual.
- The access log line goes through the gateway's log function as `LOCAL <route> <status> client=...`
  (method LOCAL instead of POST), with the same note words.
"""
import json
import logging
import os
import threading
import time

from ..errors import ConfigError
from .http import NOT_SENT, OK, RECALL_LIMIT, UNSURE, MemoryClient, MAX_RESPONSE_BYTES, _failure

_ACTIONS = {"/v1/recall": "recall", "/v1/remember": "remember", "/v1/used": "used"}
# The gateway's start-up notes for a client that cannot authenticate over HTTP: no token_env, a token
# variable that is not set, or (tenant_binding = "token") a minting-secret variable that is not set.
# In process nothing authenticates, so they say nothing an embedded user needs.
_NO_CREDENTIAL_NOTES = (" has no token (", " has no token_env:", " has no minting secret (")


def _is_missing_token_line(line):
    """A start-up note about a client without a token or minting secret. Harmless here."""
    return isinstance(line, str) and line.startswith("client ") and any(note in line for note in _NO_CREDENTIAL_NOTES)


class LocalClient(MemoryClient):
    """One in-process Gateway, up to two client policies.

    gateway          a feiyu_memgate.gateway.app.Gateway; None leaves the client off (configured is False)
    client           name of the client policy for calls without tenant_id
    tenant_client    name of the (tenant) client policy for calls with tenant_id
    start            call gateway.start() (idempotent): background pieces and the forgetting sweep thread
    close_gateway    close() also closes the gateway (from_config sets it: the client built it)
    logger           anything with .warning(fmt, *args); default logging "feiyu_memgate.client"
    recall_limit     how many results to ask for

    An unknown client name is a ConfigError here, at construction. Calls are thread-safe because
    the Gateway is; the client itself keeps only a closed flag.
    """

    def __init__(self, gateway, client=None, tenant_client=None, *, start=True, close_gateway=False,
                 logger=None, recall_limit=RECALL_LIMIT):
        if gateway is not None:
            # Imported here: an HTTP-only program importing feiyu_memgate.client does not load the gateway.
            from ..gateway.app import Gateway
        if gateway is not None and not isinstance(gateway, Gateway):
            raise ConfigError("LocalClient needs a Gateway (or None to stay off)")
        for what, name in (("client", client), ("tenant_client", tenant_client)):
            if name is None:
                continue
            if gateway is None:
                raise ConfigError("%s %r is named but there is no gateway" % (what, name))
            if name not in gateway.clients:
                raise ConfigError("%s %r is not a client of this gateway (known: %s)" % (
                    what, name, ", ".join(sorted(gateway.clients)) or "none"))
        if client is not None and getattr(gateway.clients[client], "token_bound", False):
            # Every call of a tenant-bound client is for one tenant; as `client` (calls without
            # tenant_id) each call would only be refused. Said here, at construction, like a bad name.
            raise ConfigError("client %r has tenant_binding = \"token\": name it as tenant_client and give "
                              "tenant_id on each call" % client)
        # The attributes MemoryClient's methods read; none of them means anything here.
        self.base_url = self.token = self.tenant_token = ""
        self.gateway = gateway
        self.client = client
        self.tenant_client = tenant_client
        self.log = logger if logger is not None else logging.getLogger("feiyu_memgate.client")
        self.recall_limit = recall_limit
        self._close_gateway = bool(close_gateway)
        self._closed = False
        self._lock = threading.Lock()
        if gateway is not None and start:
            gateway.start()

    # ------------------------------------------------------------------ construction

    @classmethod
    def from_config(cls, config, client=None, tenant_client=None, *, env=None, log=None,
                    gateway_options=None, **options):
        """Build a gateway from a configuration file (path) or table (dict), start it and act as
        `client` / `tenant_client`. The client owns that gateway: close() closes it.

        `[server]` host and port are ignored (nothing listens). A client table needs no `token_env`
        (it is optional to the loader), and a `token_env` whose variable is not set is fine: the
        gateway's notes about a missing token or minting secret are dropped. A tenant_binding =
        "token" client still needs a `token_env` (the loader's rule: it names the minting secret),
        but its variable may stay unset in process. Any other build problem is the ConfigError
        `build_gateway` raises. `log` receives the gateway's log lines (default stderr, as under
        serve); `gateway_options` go to build_gateway as they are."""
        from .. import config as config_module  # lazy: see __init__

        cfg = config_module.load(config) if not isinstance(config, dict) else config
        sink = log if log is not None else config_module.stderr_log

        def filtered(line):
            if not _is_missing_token_line(line):
                sink(line)

        gateway = config_module.build_gateway(cfg, env=env, log=filtered, **(gateway_options or {}))
        try:
            return cls(gateway, client, tenant_client, start=True, close_gateway=True, **options)
        except BaseException:
            gateway.close()
            raise

    @classmethod
    def from_env(cls, env=os.environ, prefix="FEIYU_MEMGATE", **options):
        """Read <prefix>_CONFIG (a configuration file), <prefix>_CLIENT and <prefix>_TENANT_CLIENT.

        Like MemoryClient.from_env, an unset <prefix>_CONFIG gives a client that is off (configured
        is False, every call fails soft), so a bot runs without memory when none is set up. A
        configuration that is set but wrong is a ConfigError: an embedded setup that silently runs
        without memory after a typo would be worse than a failed start."""
        path = env.get(prefix + "_CONFIG", "")
        if not path:
            return cls(None, **{k: v for k, v in options.items() if k in ("logger", "recall_limit")})
        return cls.from_config(path, env.get(prefix + "_CLIENT") or None, env.get(prefix + "_TENANT_CLIENT") or None,
                               env=env, **options)

    def for_client(self, client=None, tenant_client=None):
        """Another LocalClient on the same gateway, acting as other policies (one process, an owner
        policy and a group policy). It does not own the gateway: closing it leaves the gateway alone."""
        if self.gateway is None:
            raise ConfigError("this client has no gateway")
        return LocalClient(self.gateway, client, tenant_client, start=False, close_gateway=False,
                           logger=self.log, recall_limit=self.recall_limit)

    # ------------------------------------------------------------------ state

    @property
    def configured(self):
        """True when calls without a tenant can be made."""
        return self.gateway is not None and self.client is not None and not self._closed

    @property
    def tenant_configured(self):
        """True when calls with a tenant_id can be made."""
        return self.gateway is not None and self.tenant_client is not None and not self._closed

    def health(self, deep=False):
        """The gateway's health (what GET /healthz answers); None when off or closed. Never raises."""
        if self.gateway is None or self._closed:
            return None
        try:
            return self.gateway.health(deep=deep)
        except Exception as error:  # noqa: BLE001
            self.log.warning("memory health failed: %s", type(error).__name__)
            return None

    def close(self):
        """Stop using the gateway; when this client built it, stop its sweep thread and close its
        stores. Calling it again does nothing. Calls after close() are not made (recall None, remember "failed")."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._close_gateway and self.gateway is not None:
            self.gateway.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ------------------------------------------------------------------ transport

    def _config(self, tenant):
        name = self.tenant_client if tenant else self.client
        if self.gateway is None or name is None or self._closed:
            return None
        return name

    def _post(self, path, payload, deadline, what, tenant_id=None):
        """The gateway's answer to one request, as (decoded object or None, outcome): the same
        contract as MemoryClient._post, so the inherited methods map it the same way."""
        if tenant_id is not None:
            if not isinstance(tenant_id, (str, int)) or isinstance(tenant_id, bool) or not str(tenant_id).strip():
                return None, NOT_SENT
            payload = dict(payload, tenant_id=str(tenant_id).strip())
        name = self._config(tenant=tenant_id is not None)
        action = _ACTIONS.get(path)
        if name is None or action is None:
            return None, NOT_SENT
        left = deadline - time.monotonic()
        if left <= 0:
            self.log.warning("memory %s not sent: deadline passed", what)
            return None, NOT_SENT
        try:
            reply = self._within(lambda: self._dispatch(name, action, path, payload), left)
        except Exception as error:  # noqa: BLE001 - never raise to the caller; log the type only
            self.log.warning("memory %s failed: %s", what, type(error).__name__)
            return None, UNSURE
        if reply is None:
            self.log.warning("memory %s failed: timeout", what)
            return None, UNSURE
        body = reply.body if isinstance(reply.body, dict) else {}
        if reply.status != 200:
            code = body.get("code")
            code = code if isinstance(code, str) else None
            self.log.warning("memory %s failed: http %s %s", what, reply.status, code or "-")
            return None, _failure(reply.status, code)
        try:
            # What a client of the server would get: plain JSON, a copy nobody else holds.
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("response too large")
            data = json.loads(raw.decode("utf-8"))
        except Exception as error:  # noqa: BLE001
            self.log.warning("memory %s failed: %s", what, type(error).__name__)
            return None, UNSURE
        return (data, OK) if isinstance(data, dict) else (None, UNSURE)

    def _dispatch(self, name, action, path, payload):
        """What the HTTP server does after authentication: admit first, then the entry point."""
        gw = self.gateway
        started = time.monotonic()
        # A tenant-bound client gets its tenant from its token over HTTP. In process there is no
        # token: the caller is trusted with the policy, so the tenant it names is the bound one. It
        # goes to admit() too, which limits such a client per tenant before the client's total.
        policy = gw.clients.get(name)
        bound = payload.get("tenant_id") if getattr(policy, "token_bound", False) else None
        refused = gw.admit(name, action, bound_tenant=bound)
        reply = refused if refused is not None else getattr(gw, action)(name, payload, bound_tenant=bound)
        gw.log("LOCAL", path, reply.status, name, started, reply.note)
        return reply

    @staticmethod
    def _within(fn, seconds):
        """fn() or None when it did not finish in `seconds`; the abandoned call finishes on its own
        (it is bounded by the gateway's deadlines) and its result is dropped. Re-raises fn's exception."""
        box = {}

        def run():
            try:
                box["value"] = fn()
            except BaseException as error:  # noqa: BLE001 - handed to the caller's thread
                box["error"] = error

        worker = threading.Thread(target=run, name="local-client-call", daemon=True)
        worker.start()
        worker.join(seconds)
        if worker.is_alive():
            return None
        if "error" in box:
            raise box["error"]
        return box.get("value")
