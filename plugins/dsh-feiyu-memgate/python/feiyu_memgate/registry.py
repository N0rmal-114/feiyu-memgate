"""The piece registry: how a name in the configuration becomes an object.

A piece is anything callable that returns the object for one slot. Three ways to
make a piece known, in the order they are looked up:

1. a dotted path in the configuration, ``type = "my_pkg.my_module:MyBackend"``:
   nothing to register, the module is imported when the configuration is loaded;
2. ``@register("backend", "mine")`` in code that is imported before the
   configuration is loaded (the built-in pieces use this);
3. an entry point in the group ``feiyu_memgate.<kind>`` of an installed package,
   so ``pip install`` alone makes the piece available.

Standard library only.
"""
import importlib
import threading
from importlib import metadata

from .errors import ConfigError

KINDS = ("backend", "recall_stage", "write_guard", "redactor", "tenants", "forgetting_curve")

_pieces = {kind: {} for kind in KINDS}
_lock = threading.Lock()
_builtins_loaded = False


def _check_kind(kind):
    if kind not in _pieces:
        raise ConfigError("unknown piece kind %r (known: %s)" % (kind, ", ".join(KINDS)))


def register(kind, name):
    """Class or function decorator: make `factory` available as `name` for the slot `kind`.

    Registering the same factory again is harmless (a module imported twice); a name that
    already belongs to another factory is a ConfigError, so one piece cannot silently
    replace another (a built-in included)."""
    _check_kind(kind)
    if not isinstance(name, str) or not name or ":" in name:
        raise ConfigError("a piece name is a non-empty string without ':'")

    def decorator(factory):
        with _lock:
            known = _pieces[kind].get(name)
            if known is not None and known is not factory:
                raise ConfigError("the %s name %r is already registered to another piece" % (kind, name))
            _pieces[kind][name] = factory
        return factory

    return decorator


def _load_builtins():
    global _builtins_loaded
    if not _builtins_loaded:
        _builtins_loaded = True
        importlib.import_module("feiyu_memgate.builtins")


def _from_path(path):
    module_name, _, attr = path.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as error:
        raise ConfigError("cannot import %r: %s" % (module_name, type(error).__name__)) from None
    try:
        return getattr(module, attr)
    except AttributeError:
        raise ConfigError("%r has no attribute %r" % (module_name, attr)) from None


def _from_entry_point(kind, name):
    try:
        found = metadata.entry_points(group="feiyu_memgate." + kind, name=name)
    except Exception:  # noqa: BLE001 - a broken installed package must not break the lookup
        return None
    for entry in found:
        try:
            return entry.load()
        except Exception as error:  # noqa: BLE001
            raise ConfigError("entry point %r failed to load: %s" % (name, type(error).__name__)) from None
    return None


def resolve(kind, name):
    """The factory for `name` in slot `kind`. Raises ConfigError when there is none."""
    _check_kind(kind)
    if not isinstance(name, str) or not name:
        raise ConfigError("a %s needs a type" % kind)
    if ":" in name:
        return _from_path(name)
    _load_builtins()
    with _lock:
        factory = _pieces[kind].get(name)
    if factory is None:
        factory = _from_entry_point(kind, name)
    if factory is None:
        raise ConfigError("no %s piece named %r (known: %s)" % (kind, name, ", ".join(sorted(available(kind))) or "none"))
    return factory


def create(kind, spec, **extra):
    """Build one piece from its configuration.

    `spec` is a name, or a mapping with a "type" and the options the piece takes:
    ``{"type": "sqlite", "path": "memories.db"}``. `extra` are options the caller adds
    (they win over the ones in `spec`).
    """
    if isinstance(spec, str):
        spec = {"type": spec}
    if not isinstance(spec, dict):
        raise ConfigError("a %s is a name or a table with a type" % kind)
    options = {k: v for k, v in spec.items() if k != "type"}
    options.update(extra)
    name = spec.get("type")
    factory = resolve(kind, name)
    try:
        piece = factory(**options)
    except TypeError as error:
        # A wrong or missing option. The message names options, never their values.
        raise ConfigError("%s %r: %s" % (kind, name, error)) from None
    if kind in NAMED_KINDS and ":" not in name:
        _name(piece, name)
    return piece


# Pieces whose registered name shows up in notes and log lines (a dotted path keeps the class name).
NAMED_KINDS = ("recall_stage", "write_guard")


def _name(piece, name):
    try:
        piece.name = name
    except (AttributeError, TypeError):  # __slots__ or a built-in object: the class name is used
        pass


def available(kind):
    """name -> factory of the pieces registered in code for `kind` (built-ins included)."""
    _check_kind(kind)
    _load_builtins()
    with _lock:
        return dict(_pieces[kind])


def installed(kind):
    """Names of the pieces that installed packages offer for `kind` through entry points."""
    _check_kind(kind)
    try:
        return sorted(entry.name for entry in metadata.entry_points(group="feiyu_memgate." + kind))
    except Exception:  # noqa: BLE001
        return []
