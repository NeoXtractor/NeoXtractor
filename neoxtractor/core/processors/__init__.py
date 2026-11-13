"""Format processors facade.

Callers (gui / cli / mount) import **only** this package -- never
``neoxtractor.core.resources`` directly.  The facade lazily discovers the
built-in resource modules, merges them with user plugins, and offers:

- :func:`resolve`  -- identify a source (best format + score + targets)
- :func:`read`     -- decode to the canonical object (auto or forced format)
- :func:`convert`  -- canonical object -> a declared target (format required)
- :func:`convert_source` -- one-shot read + convert
- :func:`guess`    -- probe-only candidate list (never decodes)
- :func:`info`     -- static metadata for a known format name
  (targets/type/capability); no source, no probing
- :func:`list_sources` / :func:`load_plugin` / :func:`unload_plugin` /
  :func:`reload_environment` -- the plugin-management surface

Exception contract: automatic mode returns ``None`` for "not a known
format"; a forced format raises :class:`UnknownFormat` /
:class:`FormatMismatch`; corrupt payloads propagate the decoder's exception.
See :mod:`neoxtractor.core.processors.errors` for the full mapping.

File-like contract: every ``f`` parameter accepts a
:data:`neoxtractor.core.utils.filelike.FileLike` -- ``bytes``-like or a
:class:`~neoxtractor.core.utils.filelike.Seekable` stream.  Each module's
``probe`` reads only its own bounded head (via ``read_head``, which rewinds),
so streams can be handed straight in without windowing.

Module contract: resource modules are stateless -- see
:mod:`neoxtractor.core.processors.protocol`.

Entry payloads stay raw bytes; identifying, decoding and converting are
always explicit calls through this facade.
"""

from .errors import (
    FormatMismatch,
    PluginError,
    ProcessorError,
    ProcessorNoReader,
    UnknownFormat,
    UnknownTarget,
)
from .registry import (
    FormatInfo,
    ProbeHit,
    Resolution,
    SourceInfo,
    convert,
    convert_source,
    guess,
    info,
    list_sources,
    load_plugin,
    read,
    reload_environment,
    resolve,
    unload_plugin,
)
from neoxtractor.core.utils.result import Category, ProbeResult, ReadResult

__all__ = [
    # v2 facade
    "FormatInfo",
    "ProbeHit",
    "Resolution",
    "SourceInfo",
    "convert",
    "convert_source",
    "guess",
    "info",
    "list_sources",
    "load_plugin",
    "read",
    "reload_environment",
    "resolve",
    "unload_plugin",
    # v2 result value objects
    "Category",
    "ProbeResult",
    "ReadResult",
    # errors
    "FormatMismatch",
    "PluginError",
    "ProcessorError",
    "ProcessorNoReader",
    "UnknownFormat",
    "UnknownTarget",
]
