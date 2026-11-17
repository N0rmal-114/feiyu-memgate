"""Errors shared by every piece. Standard library only."""


class BackendError(Exception):
    """Any failure talking to a memory backend. Carries no upstream text on purpose:
    an error message must never hold memory content or a key."""


class ConfigError(Exception):
    """The configuration cannot be used. The message names the setting, never a secret value."""


# The code a refusal gets when its guard names none, by HTTP status (docs/api.md).
_CODE_BY_STATUS = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
                   413: "too_large", 429: "rate_limited", 503: "busy"}


class WriteRejected(Exception):
    """Raised by a write guard to refuse a write before anything reaches the backend.

    `status` is the HTTP status to answer with (4xx or 5xx), `error` the short public
    reason, `note` an optional word for the access log (never content) and `code` the
    stable machine-readable code clients match on (default: the usual code of `status`).
    """

    def __init__(self, status, error, note="", code=None):
        super().__init__(error)
        self.status = status
        self.error = error
        self.note = note
        self.code = code or _CODE_BY_STATUS.get(status, "bad_request" if 400 <= status < 500 else "internal")
