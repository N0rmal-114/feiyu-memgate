"""From the keys of a request's messages to the record fields a store keeps.

`_valid_messages` has checked each value on its own; here they meet the target store and the client's
policy. A message of a request may name a `channel`, an `origin`, an `event_date`, a `kind` and an
`audience`. For a store that keeps fields (`stores_fields`) they become the message key "fields", which
the guards see and the backend stores, together with the audience every record gets: the store's own
level, or a narrower one the message asks for. For a store that keeps none they are dropped, after the
same checks. Either way the message that goes on holds only role, content, time, speaker and "fields".

Standard library only.
"""
from ..model import AUDIENCES
from .validate import REQUEST_FIELDS, valid_channel


class Refused(Exception):
    """The request names something it may not: the gateway answers with `status` and `code`."""

    def __init__(self, status, code, text):
        super().__init__(text)
        self.status = status
        self.code = code
        self.text = text


def request_fields(messages, *, level, policy, context, keeps):
    """The messages as the guards and the store get them. Raises Refused.

    level     the audience of the store the write goes to (who its content may be shown to)
    policy    the client's ClientPolicy: its `origins` say which origin a message may name
    context   the request's checked "context" dict: a string "channel" in it is the default channel
    keeps     True when the store keeps record fields

    An origin the client may not write is refused (403 forbidden), whether or not the store keeps fields;
    an audience wider than `level` is refused (400 bad_messages) when it does. An origin is only a record of
    what the host says: it is stored, and nothing here or later grants anything because of it.
    """
    default_channel = context.get("channel")
    if not valid_channel(default_channel):
        default_channel = None
    out = []
    for message in messages:
        asked = {key: message[key] for key in REQUEST_FIELDS if key in message}
        origin = asked.get("origin")
        if origin is not None and origin not in policy.origins:
            raise Refused(403, "forbidden", "forbidden")
        clean = {key: value for key, value in message.items() if key not in REQUEST_FIELDS}
        if keeps:
            audience = asked.get("audience", level)
            if AUDIENCES.index(audience) > AUDIENCES.index(level):
                raise Refused(400, "bad_messages", "bad messages")
            fields = {"audience": audience}
            channel = asked.get("channel", default_channel)
            if channel is not None:
                fields["channel"] = channel
            for key in ("origin", "event_date", "kind"):
                if key in asked:
                    fields[key] = asked[key]
            clean["fields"] = fields
        out.append(clean)
    return out
