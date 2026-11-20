"""IDX container module: the index half of a WPK package family.

An IDX is a ``SKPW`` table of fixed records.  It holds no payload bytes: each
record names a 16-byte content hash, a package id, and where the payload sits
-- an offset inside an ``FKPW`` part (ids ``0``-``15``), a bare ``1DPW`` slot
file named by that hash (id ``255``), or nowhere at all (an id this layout
never assigns, or a store that is simply absent).  It is a pure router: it
claims itself on magic, asks the driver for its stores through ``wants``, and
once they are attached builds one :class:`WPKArchive` per store and maps each
of its records onto a child archive and key.  Reading an entry forwards to
that child; the IDX never decodes anything itself.

:class:`Status.MISSING` and :class:`Status.CORRUPTED` carry its two fault
kinds.  A record whose store did not attach -- a part that is not on disk, a
``255`` hash with no slot file, or an undefined package id -- is listed
``MISSING`` and raises :class:`MissingIndexedResourceError` on read.  A record
whose store is present but contradicts the index -- the store is truncated
under the block, or the block header's hash or sizes disagree with the record
-- is listed ``CORRUPTED`` and raises :class:`PayloadDecodeError` with reason
``corrupt`` on read, carrying what disagreed.  Both stay visible in the entry
list; MISSING is an availability fault (its own group), CORRUPTED a content
fault (grouped with the unreadable entries).  One bad block never aborts the
load: the rest of the index still opens and every other record still reads.

The index carries no plaintext names and no resid32: every entry reports
``name=None`` with ``name_source=NONE`` until a THD registry joins.  The
content hash is the index's own primary key, so ``get_res`` resolves ``INDEX``
and ``CONTENT_HASH`` references; ``NAME`` and ``SIGNATURE`` are
``UnsupportedRef``.

On-disk layout: header at ``0x20``, ``0x24``-byte records, the package id a
single byte at record ``+0x14`` (the byte after it is a separate flags field
-- reading the id too wide pairs every record with the wrong part).  Block
header validation lives in the child store: each record is handed to
:meth:`WPKArchive.load_index_blocks` with its sizes and hash as authority, so
a header that contradicts the index raises ``IntegrityMismatch`` at load and
no block-grid rules live here.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import Any, Iterator

from neoxtractor.core.packages.base import (
    Archive,
    Capability,
    Claim,
    EntryInfo,
    EntryStream,
    NameSource,
    PackageFile,
    RefKind,
    Role,
    Status,
    Unit,
)
from neoxtractor.core.packages.errors import (
    MissingIndexedResourceError,
    NoSuchEntry,
    PayloadDecodeError,
    UnsupportedRef,
)
from neoxtractor.core.packages.wpk.interface import WPKArchive, OPTIONS_TYPE
from neoxtractor.core.packages.wpk.naming import id_globs, slot_want

FORMAT = "idx"
PRIORITY = 100

_MAGIC_INDEX = b"SKPW"
_IDX_HEAD_SIZE = 0x20
_IDX_REC_SIZE = 0x24

# The layout only ever assigns 0-15 (a part) or 255 (a slot store) to the
# package id; anything else names data this resource set cannot carry.
_PKG_SLOT = 255

_SUPPORTED_KINDS = (RefKind.INDEX, RefKind.CONTENT_HASH)


@dataclass
class _IndexRecord:
    """One decoded IDX row, kept private to the archive."""

    content_hash: bytes
    ordinal: int
    pkg_id: int
    flags: int
    offset: int
    hdr_size: int
    payload_size: int
    pad_size: int

    @property
    def total_size(self) -> int:
        return self.hdr_size + self.payload_size + self.pad_size

    @property
    def sizes(self) -> tuple[int, int, int]:
        return (self.hdr_size, self.payload_size, self.pad_size)


def matches(
    file: PackageFile, want_ask: "tuple[str, Any] | None" = None
) -> Claim | None:
    """Identify an index from its first four bytes.

    Claiming is magic only; the record table is read at load.  The wants pull
    the part family (``{base}[0-9a-f].wpk`` and the underscore variant) and
    the slot directory (``{stem}/*`` narrowed to 32-hex basenames); either
    may come up empty, leaving the matching records MISSING at load.

    An index is a parent, never a child: a non-``None`` want_ask means this
    file was pulled into someone else's unit, which this format declines.
    """
    if want_ask is not None:
        return None
    if file.read_head(4) != _MAGIC_INDEX:
        return None
    stem = os.path.splitext(os.path.basename(file.path))[0]
    return Claim(
        format=FORMAT,
        entry=file.path,
        role=Role.INDEX,
        key=None,
        companions=(),
        parents=(),
        wants=id_globs(stem, ".wpk") + (slot_want(stem),),
    )


class IDXArchive(Archive):
    """A loaded index: records in RAM, payload reached through child stores.

    Owns one :class:`WPKArchive` per attached store plus a routing table from
    its own key to a child archive and child key.  State is built once at load
    and never mutated afterwards, so reads need no lock; each child guards its
    own session state.
    """

    def __init__(self, source: PackageFile, options: OPTIONS_TYPE) -> None:
        self.format = FORMAT
        self.path = source.path
        self._source = source
        self._options = options
        self.members: tuple[str, ...] = (source.path,)

        self._records: list[_IndexRecord] = []
        self._entries: list[EntryInfo] = []
        self._by_hash: dict[bytes, int] = {}
        # key -> (child archive, child key) for a record whose block was
        # honoured; a record in _corrupted or in neither set reads as a fault.
        self._route: dict[int, tuple[WPKArchive, int]] = {}
        # key -> why the store contradicted the record; the entry is
        # CORRUPTED and reads as a content fault carrying this reason.
        self._corrupted: dict[int, str] = {}
        self._children: list[WPKArchive] = []
        self._closed = False
        self.count = 0
        self.capabilities: frozenset[Capability] = frozenset()

    # --- loading -----------------------------------------------------------

    def find_index(self) -> None:
        """Read the record table; payload stays in the child stores."""
        with self._source.open() as handle:
            blob = handle.read()
        if len(blob) < _IDX_HEAD_SIZE or blob[:4] != _MAGIC_INDEX:
            raise EOFError(f"{self.path}: not a readable SKPW index header")
        count = struct.unpack_from("<I", blob, 0x0C)[0]
        need = _IDX_HEAD_SIZE + count * _IDX_REC_SIZE
        if len(blob) < need:
            raise EOFError(
                f"{self.path}: index truncated, {count} records claimed but "
                f"only {(len(blob) - _IDX_HEAD_SIZE) // _IDX_REC_SIZE} readable"
            )
        for i in range(count):
            off = _IDX_HEAD_SIZE + i * _IDX_REC_SIZE
            self._records.append(
                _IndexRecord(
                    content_hash=blob[off:off + 0x10],
                    ordinal=struct.unpack_from("<I", blob, off + 0x10)[0],
                    pkg_id=blob[off + 0x14],
                    flags=blob[off + 0x15],
                    offset=struct.unpack_from("<I", blob, off + 0x18)[0],
                    payload_size=struct.unpack_from("<I", blob, off + 0x1C)[0],
                    hdr_size=struct.unpack_from("<H", blob, off + 0x20)[0],
                    pad_size=struct.unpack_from("<H", blob, off + 0x22)[0],
                )
            )

    def attach_stores(self, unit: Unit) -> None:
        """Build a child archive per attached store and route every record.

        A ``FKPW`` part (child key = its package id) takes every record that
        names that id as a batch so its header is honoured once.  A bare
        ``1DPW`` slot (child key ``None``) matches the one ``255`` record whose
        content hash equals its filename stem and becomes a one-block child
        whose sizes and hash the index vouches for.
        :meth:`WPKArchive.load_index_blocks` returns ``(key, reason)`` per
        block; a ``None`` key marks the record CORRUPTED with that reason kept
        for the reader.  A record with no store behind it is MISSING.
        """
        parts: dict[int, PackageFile] = {}
        slots: dict[str, PackageFile] = {}
        for partner in unit.partners_for(Role.DATA):
            store = partner.paths[0]
            if partner.key is None:
                stem = os.path.splitext(os.path.basename(store.path))[0]
                slots[stem.lower()] = store
            else:
                parts[partner.key] = store

        part_blocks: dict[int, list[tuple[int, tuple[int, int, int], bytes]]] = {}
        part_keys: dict[int, list[int]] = {}
        slot_records: dict[str, int] = {}
        for key, rec in enumerate(self._records):
            if rec.pkg_id in parts:
                part_blocks.setdefault(rec.pkg_id, []).append(
                    (rec.offset, rec.sizes, rec.content_hash)
                )
                part_keys.setdefault(rec.pkg_id, []).append(key)
            elif rec.pkg_id == _PKG_SLOT:
                slot_records[rec.content_hash.hex()] = key

        for pkg, store in parts.items():
            archive = WPKArchive(store, pkg, self._options)
            results = archive.load_index_blocks(part_blocks.get(pkg, []))
            self._children.append(archive)
            for record_key, (subkey, reason) in zip(part_keys.get(pkg, []), results):
                if subkey is None:
                    self._corrupted[record_key] = reason or ""
                else:
                    self._route[record_key] = (archive, subkey)

        for stem, store in slots.items():
            slot_key = slot_records.get(stem)
            if slot_key is None:
                # The index is the authority on what exists: a slot file
                # no record names is ignored.
                continue
            rec = self._records[slot_key]
            archive = WPKArchive(store, None, self._options)
            results = archive.load_index_blocks([(0, rec.sizes, rec.content_hash)])
            self._children.append(archive)
            subkey, reason = results[0]
            if subkey is None:
                self._corrupted[slot_key] = reason or ""
            else:
                self._route[slot_key] = (archive, subkey)

        self._build_views(unit)

    def _build_views(self, unit: Unit) -> None:
        self.members = unit.all_paths
        for key, rec in enumerate(self._records):
            self._entries.append(
                EntryInfo(
                    key=key,
                    name=None,
                    name_source=NameSource.NONE,
                    signature=None,
                    content_hash=rec.content_hash,
                    ext_hint=None,
                    stored_size=rec.total_size,
                    decoded_size=None,
                    container=self.path,
                    status=self._status_for(key),
                    extra={
                        "pkg_id": rec.pkg_id,
                        "ordinal": rec.ordinal,
                        "flags": rec.flags,
                        **({"fault": self._corrupted[key]} if key in self._corrupted else {}),
                    },
                )
            )
            self._by_hash.setdefault(rec.content_hash, key)
        self.count = len(self._entries)
        if self._route:
            self.capabilities = frozenset({Capability.DATA})

    def _status_for(self, key: int) -> Status:
        if key in self._route:
            return Status.OK
        if key in self._corrupted:
            return Status.CORRUPTED
        return Status.MISSING

    # --- reading -----------------------------------------------------------

    @property
    def closed(self) -> bool:
        """True once :meth:`close` has run; further reads raise ``ValueError``."""
        return self._closed

    def entries(self, *, include_deleted: bool = False) -> Iterator[EntryInfo]:
        # An index has no deleted-slot concept; every record is presented.
        return iter(self._entries)

    def _resolve_key(self, ref: int | str | bytes, kind: RefKind) -> int:
        if kind is RefKind.INDEX:
            if isinstance(ref, int) and 0 <= ref < len(self._records):
                return ref
            raise NoSuchEntry(ref, kind)
        if kind is RefKind.CONTENT_HASH:
            needle = ref if isinstance(ref, bytes) else bytes.fromhex(str(ref))
            key = self._by_hash.get(needle)
            if key is None:
                raise NoSuchEntry(ref, kind)
            return key
        raise UnsupportedRef(FORMAT, kind, list(_SUPPORTED_KINDS))

    def get_res(
        self,
        ref: int | str | bytes,
        kind: RefKind = RefKind.INDEX,
        *,
        decoded: bool = True,
    ) -> EntryStream:
        if self._closed:
            raise ValueError("get_res on a closed archive")
        key = self._resolve_key(ref, kind)
        route = self._route.get(key)
        if route is None:
            rec = self._records[key]
            if key in self._corrupted:
                raise PayloadDecodeError(
                    FORMAT,
                    self.path,
                    "corrupt",
                    self._corrupted[key],
                    entry=key,
                )
            raise MissingIndexedResourceError(self._ref_of(key, rec), self.path)
        archive, subkey = route
        return archive.get_res(subkey, RefKind.INDEX, decoded=decoded)

    def _ref_of(self, key: int, rec: _IndexRecord) -> str:
        """A human-identifying reference for a MISSING read failure."""
        if rec.pkg_id == _PKG_SLOT:
            return f"{rec.content_hash.hex()} (slot store absent)"
        if 0 <= rec.pkg_id <= 15:
            base = os.path.splitext(os.path.basename(self.path))[0]
            return f"{base}{format(rec.pkg_id, 'x')}.wpk absent"
        return f"package id {rec.pkg_id} (undefined)"

    # --- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        children, self._children = self._children, []
        for archive in children:
            archive.close()

    def __enter__(self) -> "IDXArchive":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def load(unit: Unit, options: OPTIONS_TYPE) -> IDXArchive:
    """Build an index archive and route its records onto the attached stores."""
    source = unit.root.consumed[0]
    archive = IDXArchive(source, options)
    archive.find_index()
    archive.attach_stores(unit)
    return archive
