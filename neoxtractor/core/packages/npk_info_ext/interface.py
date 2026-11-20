"""Sidecar name list for an NPK: the ``<name>.npk.info_ext`` container.

Some NPK builds ship the plaintext entry names in a sibling file
instead of an in-file NXFN table.  The sidecar is a zlib stream holding a
Python 2 pickle (protocol 0) of exactly two parts::

    [index_backup, [(name, content_md5, flag), ...]]

``index_backup`` is a verbatim copy of the parent's on-disk index region,
and the name list is positional: item ``i`` names index record ``i``.  The
backup is what binds the two files together, and this module checks it
during identification: the declaring parent offers a read-only query
through ``want_ask``, and a sidecar whose backup disagrees with the
parent's own index bytes is not this parent's sidecar, so it is not
claimed at all.

The format exists only as a child.  Asked standalone -- as a seed, a
companion, or under any other parent -- ``matches`` declines: without the
index it backs up, the name list addresses nothing.  The parent NPK
consumes the parsed names from :attr:`Claim.private` when it loads.
"""

from __future__ import annotations

import os
import pickle
import zlib
from dataclasses import dataclass
from typing import Any

from neoxtractor.core.packages.base import (
    Archive,
    Claim,
    ParentPref,
    PackageFile,
    ReadOptions,
    Role,
    Unit,
)
from neoxtractor.core.packages.errors import IncompleteUnit

FORMAT = "npk_info_ext"
PRIORITY = 10

OPTIONS_TYPE = ReadOptions

# The suffix the parent's want adds to its own filename; stripping it back
# off yields the parent's basename, which is the attach target.
SIDECAR_SUFFIX = ".info_ext"

# The parent format this sidecar serves, and the name of the query it must
# be offered under.  The parent declares the same pair in its want.
PARENT_FORMAT = "npk"

# Index records are a fixed 28 bytes, and every name row carries a 16-byte
# content hash; both widths bound the structural check below.
_RECORD_SIZE = 28
_HASH_SIZE = 16


@dataclass
class SidecarNames:
    """Parsed sidecar payload, kept in ``Claim.private`` for the parent.

    ``names[i]`` is the plaintext name of index record ``i`` as raw bytes;
    the parent owns the decoding policy.  The driver never reads this.
    """

    names: tuple[bytes, ...]


def _parse(raw: bytes) -> "tuple[bytes, list[Any]] | None":
    """Decode the sidecar stream, or return ``None`` if it is not one.

    A valid sidecar inflates, unpickles (Python 2 protocol 0, byte strings
    kept as bytes), and yields the two-part shape described in the module
    docstring with a name row per index record.  Any other shape -- a bad
    stream, a foreign pickle, a truncated or mistyped table -- is simply
    not this format.
    """
    try:
        data = pickle.loads(zlib.decompress(raw), encoding="bytes")
    except Exception:
        return None
    if not isinstance(data, (list, tuple)) or len(data) != 2:
        return None
    backup, rows = data
    if not isinstance(backup, (bytes, bytearray)):
        return None
    if len(backup) % _RECORD_SIZE != 0:
        return None
    if not isinstance(rows, (list, tuple)) or len(rows) != (
        len(backup) // _RECORD_SIZE
    ):
        return None
    names: list[Any] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            return None
        name, digest, flag = row
        if not isinstance(name, (bytes, bytearray)):
            return None
        if (
            not isinstance(digest, (bytes, bytearray))
            or len(digest) != _HASH_SIZE
        ):
            return None
        if not isinstance(flag, int) or isinstance(flag, bool):
            return None
        names.append(name)
    return bytes(backup), names


def matches(
    file: PackageFile, want_ask: "tuple[str, Any] | None" = None
) -> Claim | None:
    """Identify a sidecar against the NPK that pulled it.

    A sidecar is never a standalone unit and never anyone's child but an
    NPK's: without a ``want_ask`` naming ``"npk"`` -- as a seed, a
    companion, or pulled under another format -- there is no index to
    bind these names to, and the file is declined outright.

    With a parent context, the stream is parsed and its index backup is
    put to the parent's read-only query.  The parent compares the backup
    against its own index bytes; any answer but a plain ``True`` (a
    mismatch, no query offered, a faulting query) declines the claim, so
    a sidecar never attaches to an index it does not describe.
    """
    if want_ask is None:
        return None
    declarer, ask = want_ask
    if declarer != PARENT_FORMAT or ask is None:
        return None

    name = os.path.basename(file.path)
    if not name.endswith(SIDECAR_SUFFIX) or name == SIDECAR_SUFFIX:
        return None

    with file.open() as handle:
        parsed = _parse(handle.read())
    if parsed is None:
        return None
    backup, names = parsed

    # The driver guards the query: a faulting or mismatched answer arrives
    # as None.  Anything but a plain True declines the claim.
    if ask(backup=backup) is not True:
        return None

    return Claim(
        format=FORMAT,
        entry=file.path,
        role=Role.NAMES,
        key=None,
        companions=(),
        parents=(ParentPref(None, name[: -len(SIDECAR_SUFFIX)]),),
        wants=(),
        private=SidecarNames(names=tuple(names)),
    )


def load(unit: Unit, options: ReadOptions) -> Archive:
    """Refuse to load: a sidecar is consumed by its parent, never alone.

    The driver only ever loads a unit's root module, and this format
    claims nothing outside a parent context, so reaching ``load`` means
    the unit has no NPK to bind the names to.  The parent reads the
    parsed table straight from ``Claim.private`` instead.
    """
    raise IncompleteUnit(FORMAT, unit.root.claim.entry, missing=[PARENT_FORMAT])
