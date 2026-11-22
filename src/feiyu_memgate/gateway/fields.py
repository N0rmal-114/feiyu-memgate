"""From the keys of a request's messages to the record fields a store keeps.

`_valid_messages` has checked each value on its own; here they meet the target store and the client's
policy. A message of a request may name a `channel`, an `origin`, an `event_date`, a `kind` and an
`audience`. For a store that keeps fields (`stores_fields`) they become the message key "fields", which
the guards see and the backend stores, together with the audience every record gets: the store's own
level, or a narrower one the message asks for. For a store that keeps none they are dropped, after the
same checks. Either way the message that goes on holds only role, content, time, speaker and "fields".

With an identity table the message's `speaker` and `about` are accounts, and they are resolved here, before
any guard runs and before any redaction (the public redactor would turn every 11-digit account into the same
text): to the person's name when listed, else to a stand-in code for the store. From then on the raw account
is gone. The resolved speaker and about join the record's fields when the store keeps fields.

Standard library only.
"""
from ..model import AUDIENCES, platform_of
from .validate import REQUEST_FIELDS, valid_channel


class Refused(Exception):
    """The request names something it may not: the gateway answers with `status` and `code`."""

    def __init__(self, status, code, text):
        super().__init__(text)
        self.status = status
        self.code = code
        self.text = text


def _forbidden():
    return Refused(403, "forbidden", "forbidden")


def stand_in(identity, account, scope):
    """The resolved value of an account: its person's name when listed, else its code for the store `scope`."""
    person = identity.resolve(account)
    return person if person is not None else identity.code(account, scope)


def check_platform(policy, account):
    """Raise Refused (403) unless `policy` may name `account`: its platform must be one the client lists."""
    if platform_of(account) not in policy.platforms:
        raise _forbidden()


def _who(message, policy, identity, scope):
    """(speaker, about): the resolved values for one message, None where the message names none. Raises Refused."""
    role, account, about = message["role"], message.get("speaker"), message.get("about")
    if role == "assistant":
        speaker = "self"  # whatever the host sent: the assistant is not anyone's account
    elif account is not None:
        check_platform(policy, account)
        speaker = stand_in(identity, account, scope)
    else:
        speaker = None
    if role == "user" and policy.only_for:
        person = identity.resolve(account) if account is not None else None
        if person is None or person not in policy.only_for:
            raise _forbidden()
    if about is None:
        return speaker, None
    check_platform(policy, about)
    named = stand_in(identity, about, scope)
    if named != speaker and policy.reach != "owner":
        raise _forbidden()  # saying something about someone else is for an owner-level client
    return speaker, named


def request_fields(messages, *, level, policy, context, keeps, identity=None, scope=None):
    """The messages as the guards and the store get them. Raises Refused.

    level     the audience of the store the write goes to (who its content may be shown to)
    policy    the client's ClientPolicy: its `origins` say which origin a message may name
    context   the request's checked "context" dict: a string "channel" in it is the default channel
    keeps     True when the store keeps record fields
    identity  the gateway's identity provider, None without one
    scope     the name of the store the write goes to ("provider:tenant" for a tenant's store), the scope
              of the stand-in codes; needed with `identity`

    An origin the client may not write is refused (403 forbidden), whether or not the store keeps fields;
    an audience wider than `level` is refused (400 bad_messages) when it does. An origin is only a record of
    what the host says: it is stored, and nothing here or later grants anything because of it. With an
    identity, the accounts of speaker and about are checked against the client's platforms (and only_for)
    and replaced by the resolved values; an `about` is dropped without one.
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
        clean = {key: value for key, value in message.items() if key not in REQUEST_FIELDS and key != "about"}
        speaker = about = None
        if identity is not None:
            speaker, about = _who(message, policy, identity, scope)
            if speaker is None:
                clean.pop("speaker", None)
            else:
                clean["speaker"] = speaker
        if keeps:
            audience = asked.get("audience", level)
            if AUDIENCES.index(audience) > AUDIENCES.index(level):
                raise Refused(400, "bad_messages", "bad messages")
            fields = {"audience": audience}
            if speaker is not None:
                fields["speaker"] = speaker
            if about is not None:
                fields["about"] = about
            channel = asked.get("channel", default_channel)
            if channel is not None:
                fields["channel"] = channel
            for key in ("origin", "event_date", "kind"):
                if key in asked:
                    fields[key] = asked[key]
            clean["fields"] = fields
        out.append(clean)
    return out
