"""Manage tenants: the key file of the "keyfile" tenants provider (add, remove and list
tenants), and the tokens of a client whose tokens are bound to one tenant (mint_token).

The file is a JSON object {"<tenant id>": "<key>"} (see tenants/keyfile.py, which
reads it and refuses what these functions refuse). Writes are atomic: a restricted
temp file in the same directory, fsync, then os.replace, so the running gateway
never sees a half-written file and a failure leaves the old file as it was.

These functions do not lock: run one management command at a time. Keys and minting
secrets are never returned by list_tenants and never put in an error message.

Standard library only.
"""
import json
import os
import re
import uuid
from pathlib import Path

from ..tenants.keyfile import DEFAULT_ID_PATTERN, MAX_KEYS_BYTES


def _check_keys(data, id_pattern=DEFAULT_ID_PATTERN):
    if not isinstance(data, dict):
        raise ValueError("密钥文件不是对象")
    id_re = re.compile(id_pattern)
    for tenant_id, key in data.items():
        if not isinstance(tenant_id, str) or id_re.fullmatch(tenant_id) is None or not isinstance(key, str) or not key:
            raise ValueError("密钥文件里有不合格的条目")


def load_keys(path, id_pattern=DEFAULT_ID_PATTERN):
    """The keys file as a dict ({} when missing). Raises ValueError on content the gateway would refuse."""
    path = Path(path)
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_KEYS_BYTES + 1)
    except FileNotFoundError:
        return {}
    if len(raw) > MAX_KEYS_BYTES:
        raise ValueError("密钥文件超过 1 MiB")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("密钥文件不是有效的 JSON") from None
    _check_keys(data, id_pattern)
    return data


def stale_temp_files(path):
    """Names of leftover <file>.tmp-* files next to the keys file."""
    path = Path(path)
    try:
        return sorted(p.name for p in path.parent.glob(path.name + ".tmp-*"))
    except OSError:
        return []


def write_keys(path, keys, id_pattern=DEFAULT_ID_PATTERN):
    """Write the keys file atomically. Any failure leaves the old file exactly as it was
    and no temp file behind."""
    path = Path(path)
    _check_keys(keys, id_pattern)
    data = json.dumps(keys, indent=2, sort_keys=True).encode("utf-8")
    if len(data) > MAX_KEYS_BYTES:
        raise ValueError("密钥文件会超过 1 MiB")
    path.parent.mkdir(parents=True, exist_ok=True)
    for stale in stale_temp_files(path):  # left by a killed run
        try:
            (path.parent / stale).unlink()
        except OSError:
            pass
    tmp = path.with_name("%s.tmp-%s" % (path.name, uuid.uuid4().hex))
    # Created with mode 0o600 from the start: key bytes never sit in a file others can read
    # (where the OS has such modes; on Windows the folder's permissions apply).
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add_tenant(path, tenant_id, key, id_pattern=DEFAULT_ID_PATTERN, replace=False):
    """Register a tenant with its key. An id that exists is an error unless `replace` is true."""
    if not isinstance(tenant_id, str) or re.fullmatch(id_pattern, tenant_id) is None:
        raise ValueError("租户编号不符合规则：%s" % id_pattern)
    if not isinstance(key, str) or not key:
        raise ValueError("密钥不能为空")
    keys = load_keys(path, id_pattern)
    if tenant_id in keys and not replace:
        raise ValueError("租户 %s 已经存在" % tenant_id)
    keys[tenant_id] = key
    write_keys(path, keys, id_pattern)


def remove_tenant(path, tenant_id):
    """Drop a tenant. True when it was there, False when it was not (the file is then untouched)."""
    keys = load_keys(path, r".+")
    if tenant_id not in keys:
        return False
    del keys[tenant_id]
    write_keys(path, keys, r".+")
    return True


def list_tenants(path):
    """The tenant ids, sorted. Never the keys."""
    return sorted(load_keys(path, r".+"))


def mint_token(cfg, client, tenant_id, env=None):
    """The request token of `client` bound to `tenant_id`, from a loaded configuration and the
    minting secret in the environment variable the client's token_env names.

    Only what the token needs is built: the client's policy (checked) and its tenants provider,
    to ask whether the id is well-formed there; no store is opened and the gateway is not started.
    Whether the tenant is registered is not checked: a token minted before its tenant is added
    gets 403 unknown_tenant until then. Raises ValueError (a message without the secret) when the
    client is missing or not tenant-bound, the id is refused, or the secret is missing or unusable;
    ConfigError for a configuration the gateway would refuse."""
    from .. import config, registry  # lazy: the key-file commands need none of this
    from ..gateway.app import MAX_TENANT_ID_BYTES, MIN_TOKEN_CHARS, mint_tenant_token, usable_token
    env = os.environ if env is None else env
    clients = cfg.get("clients") if isinstance(cfg, dict) else None
    spec = clients.get(client) if isinstance(clients, dict) else None
    if not isinstance(spec, dict):
        raise ValueError("配置里没有客户端 %s" % client)
    policy = config.client_policy(client, spec)
    if not policy.token_bound:
        raise ValueError("客户端 %s 不是 tenant_binding = \"token\"：它的租户号由请求自报，没有可铸造的令牌" % client)
    tenants = cfg.get("tenants")
    provider_spec = tenants.get(policy.tenants) if isinstance(tenants, dict) else None
    if not isinstance(provider_spec, dict):
        raise ValueError("客户端 %s 的租户提供方 %s 没有配置" % (client, policy.tenants))
    provider = registry.create("tenants", config.resolve_env(provider_spec, env, "tenants." + policy.tenants))
    try:
        valid = isinstance(tenant_id, str) and bool(provider.valid_id(tenant_id))
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()
    if not valid:
        raise ValueError("租户编号 %r 不符合租户提供方 %s 的规则" % (tenant_id, policy.tenants))
    try:
        size = len(tenant_id.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError("租户编号不是有效的文字") from None
    if size > MAX_TENANT_ID_BYTES:
        raise ValueError("租户编号太长：UTF-8 超过 %d 字节，做不成绑定令牌" % MAX_TENANT_ID_BYTES)
    token_env = spec.get("token_env")
    secret = usable_token(env.get(token_env, "")) if isinstance(token_env, str) and token_env else ""
    if not secret:
        raise ValueError("环境变量 %s 没有设置铸造密钥，或者它是占位符、短于 %d 个字符" % (token_env, MIN_TOKEN_CHARS))
    return mint_tenant_token(secret, client, policy.tenants, policy.tenant_token_generation, tenant_id)
