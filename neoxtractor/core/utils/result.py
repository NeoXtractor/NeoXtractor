"""Value objects for the resource probe/read protocol.

``probe`` returns a :class:`ProbeResult` -- a header-only score plus a guessed
extension and category.  ``read`` (surfaced by the processors facade) returns a
:class:`ReadResult` -- the canonical object plus the format that produced it,
so a caller that asked for an automatic read still learns which decoder won.

Both are ``NamedTuple``s (mirroring
:class:`neoxtractor.core.utils.target.Target`) so the protocol can grow fields
without breaking callers.  Coercion helpers normalise the looser shapes a
third-party plugin may return (a bare ``int`` score, a raw canonical object)
in one place rather than in every module.

``ext`` and ``category`` are deliberately *per-detection* rather than
per-module: a single module (e.g. ``unsupported``) recognises many magics that
belong to different categories, so each probe call carries its own answer.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, NamedTuple, Union


class Category(StrEnum):
    """Neutral, processors-side vocabulary for grouping decoded resources.

    This is the single source of truth for resource grouping: the container
    layer carries no category enum, and a value is chosen per detection from
    the decoded content.  The UI groups and filters entries by it.
    """

    MESH = "Mesh"
    TEXTURE = "Texture"
    CHARACTER = "Character"
    BANK = "Bank"
    SKIN = "Skin"
    BXML = "NeoX XML"  # NeoX binary XML, not generic XML
    CSB = "Cocos2D UI"
    OTHER = "Other"


class ProbeResult(NamedTuple):
    """Outcome of a header-only format probe.

    ``score`` is an AVPROBE-style confidence (0 = not this format, 100 =
    exact magic, 50 = strong heuristic, 10 = weak guess).  ``ext`` is a
    guessed extension without a leading dot ("" when undecidable).
    ``category`` is a :class:`Category` value (or "" when unknown).
    """

    score: int
    ext: str = ""
    category: str = ""


class ReadResult(NamedTuple):
    """Outcome of a read, carrying the canonical object and its provenance.

    ``data`` is the canonical object (PIL image, mesh IR, XML text, dict, ...);
    its concrete type is described by the winning module's ``CANONICAL_TYPE``
    hint.  ``format``/``ext``/``category`` echo the detection so callers that
    let the facade choose still know what was chosen.  ``metadata`` is an open
    bag for future per-read information (sizes, version, warnings, ...).

    Destructures like ``data, fmt, *_ = read(...)``.  ``metadata`` defaults to
    ``None`` and is created lazily by the caller.
    """

    data: Any
    format: str = ""
    ext: str = ""
    category: str = ""
    metadata: "dict | None" = None


# Contract range for a probe score, enforced at the coercion boundary below.
# A plugin is untrusted code: an out-of-range score would let one module
# hijack every detection after the registry's weighting.
MAX_SCORE = 100


def _clamp_score(raw: object) -> int:
    """Coerce a module-supplied probe score into the 0~``MAX_SCORE`` range.

    Anything non-numeric, a bool (a flag is not a confidence), or <= 0
    collapses to a 0 reject; values above ``MAX_SCORE`` are capped.  Sole
    enforcement point for the range.
    """
    if isinstance(raw, bool):
        return 0
    # Plugin-land value: whether int() accepts it is a runtime question, so
    # cross into Any at this trust boundary and let the except below judge.
    raw_numeric: Any = raw
    try:
        score = int(raw_numeric)
    except (TypeError, ValueError):
        return 0
    if score <= 0:
        return 0
    return min(score, MAX_SCORE)


def as_probe_result(value: Union[ProbeResult, int, tuple, None]) -> ProbeResult:
    """Normalise a module's raw probe return into a :class:`ProbeResult`.

    Accepts a ``ProbeResult``, a ``(score[, ext[, category]])`` tuple, or a
    bare score (``int``/``float``).  The score is always routed through
    :func:`_clamp_score` regardless of shape, so a plugin can neither crash
    the registry nor outrank everything with an out-of-range value.
    """
    if isinstance(value, ProbeResult):
        return ProbeResult(_clamp_score(value.score), value.ext, value.category)
    if isinstance(value, tuple):
        try:
            coerced = ProbeResult(*value)
        except TypeError:
            return ProbeResult(score=0)
        return ProbeResult(
            _clamp_score(coerced.score), coerced.ext, coerced.category
        )
    # Bare score (int/float) or garbage: _clamp_score rejects bool/non-numeric.
    return ProbeResult(score=_clamp_score(value))


def as_read_result(
    value: Any,
    *,
    format: str = "",
    ext: str = "",
    category: str = "",
) -> ReadResult:
    """Wrap a module's ``read`` return into a :class:`ReadResult`.

    A module may return the canonical object directly (most common) or an
    already-built ``ReadResult`` (when it wants to attach metadata).  Empty
    provenance fields are backfilled from the detection that won.
    """
    if isinstance(value, ReadResult):
        return ReadResult(
            data=value.data,
            format=value.format or format,
            ext=value.ext or ext,
            category=value.category or category,
            metadata=value.metadata,
        )
    return ReadResult(data=value, format=format, ext=ext, category=category)