"""Neutral dict <-> ReadOptions codec driven by each format's OPTIONS_TYPE.

The GUI stores read options as plain per-format dicts (JSON-shaped) and never
imports a format's options class.  This module turns one such dict into the
right :class:`ReadOptions` subclass, discovered through the registry from the
format name, so adding a container format needs no change here.

Unknown fields are logged and dropped, never raised: a stale or hand-edited
config key must not crash an open.  The signature name map arrives with
``"0x<hex>"`` string keys (JSON has no integer keys) and is converted to the
int keys :attr:`ReadOptions.name_map` expects; :func:`options_to_dict` performs
the inverse so a config round-trips through JSON unchanged.

:func:`options_fields` returns the ``(name, kind, default)`` triples the config
dialog builds its widgets from, so each format's real options drive the dialog.
"""

from __future__ import annotations

import dataclasses
from typing import Any, get_type_hints

from neoxtractor.core.packages import registry
from neoxtractor.core.packages.base import ReadOptions
from neoxtractor.core.utils.logger import get_logger

__all__ = [
    "build_options",
    "options_to_dict",
    "options_fields",
]

# The signature map is the one field whose JSON form differs from its runtime
# form (string keys vs int keys); it is also edited by a dedicated table in the
# config dialog rather than a generated widget, so it is excluded from
# options_fields.
_NAME_MAP_FIELD = "name_map"


def _options_type(fmt: str | None) -> type[ReadOptions] | None:
    if not fmt:
        return None
    entry = registry.lookup(fmt)
    return entry.options_type if entry is not None else None


def _kind(hint: Any) -> str:
    """Reduce a resolved annotation to a widget kind for the dialog."""
    text = str(hint)
    if "bool" in text:
        return "bool"
    if "int" in text:
        return "int"
    return "text"


def _coerce(kind: str, value: Any) -> Any:
    """Best-effort type repair for a hand-edited JSON value.

    ``"83"`` for an int field is repaired to ``83``; an unrepairable value
    raises and the caller drops the field to its default.
    """
    if kind == "int":
        return int(value)
    if kind == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
        return bool(value)
    return value


def _fields(cls: type[ReadOptions]) -> tuple[dataclasses.Field, ...]:
    return dataclasses.fields(cls)


def _hex_keys_to_int(mapping: dict) -> dict[int, str]:
    """Convert ``"0x<hex>"`` name-map keys to int signatures."""
    out: dict[int, str] = {}
    for key, value in mapping.items():
        if isinstance(key, int):
            out[key] = value
            continue
        try:
            out[int(str(key), 16)] = value
        except ValueError:
            get_logger().warning("name_map key %r is not a hex signature; skipped", key)
    return out


def build_options(fmt: str | None, raw: dict[str, Any]) -> ReadOptions | None:
    """Construct the format's OPTIONS_TYPE from a raw field dict.

    Returns ``None`` when *fmt* is not a registered format (the caller then
    leaves options unset and lets ``open_file`` auto-detect).  Fields the
    options class does not declare are logged and dropped.  A ``name_map``
    value with string keys is converted to int keys.
    """
    cls = _options_type(fmt)
    if cls is None:
        if fmt:
            get_logger().warning("build_options: unknown format %r", fmt)
        return None

    hints = get_type_hints(cls)
    known = {field.name: _kind(hints.get(field.name, object)) for field in _fields(cls)}
    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in known:
            get_logger().warning(
                "build_options(%s): dropping unknown field %r", fmt, key
            )
            continue
        if key == _NAME_MAP_FIELD and isinstance(value, dict):
            cleaned[key] = _hex_keys_to_int(value)
            continue
        if value is None:
            cleaned[key] = None
            continue
        try:
            cleaned[key] = _coerce(known[key], value)
        except (TypeError, ValueError):
            get_logger().warning(
                "build_options(%s): field %r value %r is not a %s; using default",
                fmt, key, value, known[key],
            )

    try:
        return cls(**cleaned)
    except TypeError as exc:  # a wrong-typed value: fall back to defaults
        get_logger().warning("build_options(%s): %s", fmt, exc)
        return cls()


def options_to_dict(fmt: str, options: ReadOptions | None) -> dict[str, Any]:
    """Serialize an options instance back to a raw field dict (JSON-ready).

    ``name_map`` int keys are converted back to ``"0x<hex>"`` strings so the
    result round-trips through :func:`build_options`.
    """
    if options is None:
        return {}
    out: dict[str, Any] = {}
    for field in _fields(type(options)):
        value = getattr(options, field.name)
        if field.name == _NAME_MAP_FIELD:
            if value:
                out[field.name] = {hex(int(k)): v for k, v in value.items()}
            continue
        out[field.name] = value
    return out


def options_fields(fmt: str) -> list[tuple[str, str, Any]]:
    """``(name, kind, default)`` triples for the dialog's generated widgets.

    ``kind`` is ``"bool"`` / ``"int"`` / ``"text"``.  The signature ``name_map``
    is omitted: the dialog edits it through its dedicated table, not a
    generated field.  Empty list for an unknown format.
    """
    cls = _options_type(fmt)
    if cls is None:
        return []
    hints = get_type_hints(cls)
    triples: list[tuple[str, str, Any]] = []
    for field in _fields(cls):
        if field.name == _NAME_MAP_FIELD:
            continue
        default = None if field.default is dataclasses.MISSING else field.default
        triples.append((field.name, _kind(hints.get(field.name, object)), default))
    return triples
