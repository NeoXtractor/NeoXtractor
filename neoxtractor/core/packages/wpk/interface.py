"""WPK container module: payload stores, standalone.

A WPK is a store of embedded ``1DPW`` entry blocks.  Two physical shapes reach
this module and share one read mechanism (slice the payload from a block,
stage-1 decode, then strip nested wrappers):

* a ``FKPW`` part -- a shard holding many ``1DPW`` blocks at aligned offsets;
  its package id is the single trailing hex token of its own filename;
* a bare ``1DPW`` slot file -- one entry that is itself the whole file,
  identified by its content hash.

The module is a child in a unit (``role=DATA``, a ``parents`` preference for
its index); opened alone it stands alone -- a part enumerates its own grid, a
bare slot opens as a one-entry archive.  Entries carry no name (``name=None``;
the UI falls back to the content hash): names and the slot stage-1
auto-selection belong to the IDX path.

FKPW parts place entries on a grid -- ``first_entry + k * step``,
``step = 2 ** align_log2`` -- and every block must satisfy that geometry to be
recorded.  Key material is copied verbatim, never paraphrased.

Claiming and trusting are separate: :func:`matches` claims on magic alone;
:meth:`WPKArchive.find_index` honours the headers (version, package id against
the filename, geometry) and raises on contradiction, naming the filename a
mismatched file should carry.
"""

from __future__ import annotations

import hashlib
import io
import os
import struct
import threading
from dataclasses import dataclass
from typing import Iterator

from neoxtractor.core.packages.base import (
    Archive,
    Capability,
    Claim,
    EntryInfo,
    EntryStream,
    NameSource,
    PackageFile,
    ParentPref,
    ReadOptions,
    RefKind,
    Role,
    Status,
    Unit,
)
from neoxtractor.core.packages.errors import (
    IntegrityMismatch,
    NoSuchEntry,
    PackageError,
    PayloadDecodeError,
    UnsupportedRef,
    VersionUnsupported,
)
from neoxtractor.core.utils.logger import get_logger

from .decryption import try_decode_payload_stage1
from .naming import strip_trailing_hex
from .payload import unwrap_nested_payloads

FORMAT = "wpk"
PRIORITY = 50
OPTIONS_TYPE = ReadOptions

_MAGIC_PART = b"FKPW"
_MAGIC_ENTRY = b"1DPW"
_PART_HEADER_LEN = 0x0C
_PART_VERSION = b"\x00\x01"
_PART_PKG_ID_OFF = 0x06
_PART_ALIGN_OFF = 0x07
_PART_FIRST_OFF = 0x08
_MIN_ENTRY_HEADER = 0x30
_ENTRY_HASH_OFF = 0x08
_ENTRY_PAYLOAD_OFF = 0x20
_ENTRY_HDR_OFF = 0x24
_ENTRY_PAD_OFF = 0x26
_ZERO_HASH = b"\x00" * 16

_SUPPORTED_KINDS = (RefKind.INDEX, RefKind.CONTENT_HASH)


@dataclass
class _BlockRecord:
    """One decoded ``1DPW`` block header, kept private to the archive.

    ``content_hash`` is ``None`` for a block whose embedded hash is all zero:
    such a block has no real hash, and one is never invented for it.
    """

    offset: int
    total_size: int
    hdr_size: int
    payload_size: int
    content_hash: bytes | None


def _pkg_id_from_name(stem: str) -> int | None:
    """The package id carried by a part filename's trailing hex token."""
    if stem and stem[-1] in "0123456789abcdef":
        return int(stem[-1], 16)
    return None


def matches(file: PackageFile) -> Claim | None:
    """Identify a WPK part or a bare slot from its first four bytes.

    Claiming is magic only; headers are honoured at load.  The parent
    preference points at the index that would own this store (same directory
    for a part, parent directory for a slot).  Nothing here consults the
    filesystem for neighbours.
    """
    head = file.read_head(4)
    if head == _MAGIC_PART:
        stem = os.path.splitext(os.path.basename(file.path))[0]
        base = strip_trailing_hex(stem)
        return Claim(
            format=FORMAT,
            entry=file.path,
            role=Role.DATA,
            key=_pkg_id_from_name(stem),
            companions=(),
            parents=(ParentPref(Role.INDEX, base + ".idx"),),
            wants=(),
        )
    if head == _MAGIC_ENTRY:
        directory_name = os.path.basename(os.path.dirname(file.path))
        return Claim(
            format=FORMAT,
            entry=file.path,
            role=Role.DATA,
            key=None,
            companions=(),
            parents=(ParentPref(Role.INDEX, f"../{directory_name}.idx"),),
            wants=(),
        )
    return None


class WPKArchive(Archive):
    """A loaded WPK session: block table in RAM, payload read on demand.

    All per-session state -- the block records and the lookup dicts -- lives on
    the instance and is released on :meth:`close`.  Payload bytes are read
    through the single :class:`PackageFile` the unit was handed; a fresh handle
    per :meth:`get_res` keeps concurrent reads independent, so no shared file
    cursor is guarded.
    """

    def __init__(
        self, source: PackageFile, pkg_id: int | None, options: ReadOptions
    ) -> None:
        self.format = FORMAT
        self.path = source.path
        self.members: tuple[str, ...] = (source.path,)

        self._source = source
        self._pkg_id = pkg_id
        self._options = options
        self._lock = threading.Lock()
        self._open_streams: list[EntryStream] = []
        self._closed = False

        self._records: list[_BlockRecord] = []
        self._entries: list[EntryInfo] = []
        self._by_hash: dict[bytes, int] = {}
        self.count = 0
        self.capabilities: frozenset[Capability] = frozenset({Capability.DATA})

    # --- loading -----------------------------------------------------------

    def find_index(self) -> None:
        """Enumerate the embedded blocks; payload stays on disk.

        A bare slot is a single block at offset 0; a part has its header
        honoured here (version, package id against the filename, grid).  A
        contradiction surfaces as a load failure -- never a silent downgrade
        or a dropped entry.
        """
        with self._source.open() as handle:
            head = handle.read(4)
            handle.seek(0)
            if head == _MAGIC_ENTRY:
                records = self._scan_single(handle)
            elif head == _MAGIC_PART:
                step, first = self._read_part_header(handle)
                records = self._scan_grid(handle, step, first, self._source.size())
            else:
                raise PayloadDecodeError(
                    FORMAT,
                    self.path,
                    "corrupt",
                    "header is neither FKPW nor 1DPW -- not a WPK store",
                )
        self._records = records
        self._build_views()

    def load_index_blocks(
        self, blocks: list[tuple[int, tuple[int, int, int], bytes]]
    ) -> list[tuple[int | None, str | None]]:
        """Load the blocks an index vouches for, in the order given.

        The authoritative IDX-driven entry: the caller hands over
        ``(offset, (hdr, payload, pad), hash)`` for exactly the blocks its
        records name, decoded through :meth:`_read_block_header` with the
        index's values as authority.  Unlike the standalone scan, a fault is
        returned per block -- ``(key, None)`` or ``(None, reason)`` naming what
        contradicted the index -- so one bad block never sinks the other
        records the same store carries; the caller maps failures to corrupted
        entries.  A ``FKPW`` part header is honoured here too: a package id
        contradicting the filename corrupts the whole part (every block shares
        the reason), while an unreadable version still aborts -- a build
        limitation, not bad data.  Successful blocks become this archive's
        records.
        """
        size = self._source.size()
        results: list[tuple[int | None, str | None]] = []
        part_reason: str | None = None
        with self._source.open() as handle:
            head = handle.read(4)
            handle.seek(0)
            if head == _MAGIC_PART:
                try:
                    self._read_part_header(handle)
                except IntegrityMismatch as exc:
                    part_reason = str(exc)
            for off, known_sizes, known_sig in blocks:
                if part_reason is not None:
                    results.append((None, part_reason))
                    continue
                try:
                    record = self._read_block_header(
                        handle,
                        off,
                        size,
                        known_sizes=known_sizes,
                        known_sig=known_sig,
                    )
                except IntegrityMismatch as exc:
                    results.append((None, str(exc)))
                    continue
                if record is None:
                    results.append(
                        (
                            None,
                            f"the store ends before the block the index names "
                            f"at 0x{off:X}",
                        )
                    )
                else:
                    results.append((len(self._records), None))
                    self._records.append(record)
        self._build_views()
        return results

    def _read_part_header(self, handle) -> tuple[int, int]:
        """Honour the FKPW header and return its grid: ``(step, first_entry)``.

        The version must be the one this build reads.  When the filename
        carries a trailing hex token it must equal the package id stored in
        the header; a mismatch raises naming the file that *would* be correct.
        """
        header = handle.read(_PART_HEADER_LEN)
        if len(header) < _PART_HEADER_LEN:
            raise PayloadDecodeError(
                FORMAT, self.path, "corrupt", "truncated FKPW header"
            )
        if header[4:6] != _PART_VERSION:
            version = struct.unpack_from("<H", header, 4)[0]
            raise VersionUnsupported(
                FORMAT, self.path, version, "version 1"
            )
        stem = os.path.splitext(os.path.basename(self.path))[0]
        name_pkg = _pkg_id_from_name(stem)
        header_pkg = header[_PART_PKG_ID_OFF]
        if name_pkg is not None and header_pkg != name_pkg:
            ext = os.path.splitext(self.path)[1]
            expected = strip_trailing_hex(stem) + format(header_pkg, "x") + ext
            raise IntegrityMismatch(
                FORMAT,
                self.path,
                "package id",
                name_pkg,
                header_pkg,
                expected_filename=expected,
            )
        step = 1 << header[_PART_ALIGN_OFF]
        first = struct.unpack_from("<I", header, _PART_FIRST_OFF)[0]
        return step, first

    def _scan_grid(
        self, handle, step: int, first: int, size: int
    ) -> list[_BlockRecord]:
        """Walk ``first + k * step``, recording every block that fits the grid.

        A recognised block advances by its own total (a whole number of steps,
        guaranteed by the alignment check), so no interior is re-read and a
        stray ``1DPW`` inside a payload is never mistaken for an entry.  An
        invalid grid point advances one step.
        """
        records: list[_BlockRecord] = []
        pos = first
        while pos < size:
            record = self._read_block_header(handle, pos, size, alignment=step)
            if record is None:
                pos += step
                continue
            records.append(record)
            pos += record.total_size
        return records

    def _scan_single(self, handle) -> list[_BlockRecord]:
        """One bare slot file: its only block sits at offset 0.

        The slot's filename must be the block's own content hash; a name that
        contradicts the content raises with the filename it should carry.
        """
        size = self._source.size()
        record = self._read_block_header(handle, 0, size)
        if record is None:
            raise PayloadDecodeError(
                FORMAT, self.path, "corrupt", "bare slot header is not a valid block"
            )
        if record.content_hash is not None:
            hash_hex = record.content_hash.hex()
            name = os.path.basename(self.path)
            if os.path.splitext(name)[0].lower() != hash_hex:
                ext = os.path.splitext(self.path)[1]
                raise IntegrityMismatch(
                    FORMAT,
                    self.path,
                    "slot filename",
                    name,
                    hash_hex,
                    expected_filename=f"{hash_hex}{ext}",
                )
        return [record]

    def _read_block_header(
        self,
        handle,
        off: int,
        size: int,
        *,
        alignment: int | None = None,
        known_sizes: tuple[int, int, int] | None = None,
        known_sig: bytes | None = None,
    ) -> _BlockRecord | None:
        """Decode one ``1DPW`` header at ``off``, or return None.

        Mandatory: the magic matches, a full header reads, and
        ``total = hdr + payload + pad`` is positive and ends within the file.
        Caller-supplied parameters are checked only when given: a grid walk
        passes ``alignment`` (``total`` must be a multiple of it); the index
        path passes ``known_sizes`` ``(hdr, payload, pad)`` and ``known_sig``
        (the 16-byte content hash).  A *geometry* failure returns None (a
        rejected grid point is normal scan control flow; for an index record it
        means the store is truncated under the block).  An *authority*
        contradiction raises ``IntegrityMismatch`` naming the field with both
        values, checked before geometry -- a store truncated over a block whose
        header still contradicts the index raises rather than reading as a mere
        short block.  Turning each outcome into an entry status is the caller's
        policy.  An all-zero hash stays ``None`` -- never invented.
        """
        handle.seek(off)
        header = handle.read(_MIN_ENTRY_HEADER)
        if len(header) < _MIN_ENTRY_HEADER or header[:4] != _MAGIC_ENTRY:
            return None
        payload_size = struct.unpack_from("<I", header, _ENTRY_PAYLOAD_OFF)[0]
        hdr_size = struct.unpack_from("<H", header, _ENTRY_HDR_OFF)[0]
        pad_size = struct.unpack_from("<H", header, _ENTRY_PAD_OFF)[0]
        raw_hash = header[_ENTRY_HASH_OFF:_ENTRY_HASH_OFF + 0x10]
        if known_sig is not None and raw_hash != known_sig:
            raise IntegrityMismatch(
                FORMAT,
                self.path,
                "content hash",
                raw_hash.hex(),
                known_sig.hex(),
                offset=off,
            )
        if known_sizes is not None:
            for field, reported, authority in zip(
                ("hdr_size", "payload_size", "pad_size"),
                (hdr_size, payload_size, pad_size),
                known_sizes,
            ):
                if reported != authority:
                    raise IntegrityMismatch(
                        FORMAT,
                        self.path,
                        field,
                        reported,
                        authority,
                        offset=off,
                    )
        total = hdr_size + payload_size + pad_size
        if total <= 0 or off + total > size:
            return None
        if alignment is not None and total % alignment != 0:
            return None
        content_hash = None if raw_hash == _ZERO_HASH else raw_hash
        return _BlockRecord(
            offset=off,
            total_size=total,
            hdr_size=hdr_size,
            payload_size=payload_size,
            content_hash=content_hash,
        )

    def _build_views(self) -> None:
        """Project block records into neutral EntryInfo and lookup dicts.

        A standalone store carries no names at all: every entry reports
        ``name=None`` with ``name_source=NONE``; the UI derives the display
        fallback from ``content_hash``.  Names arrive through the IDX path.
        """
        for key, record in enumerate(self._records):
            self._entries.append(
                EntryInfo(
                    key=key,
                    name=None,
                    name_source=NameSource.NONE,
                    signature=None,
                    content_hash=record.content_hash,
                    ext_hint=None,
                    stored_size=record.total_size,
                    decoded_size=None,
                    container=self.path,
                    status=Status.OK,
                    extra={
                        "pkg_id": self._pkg_id,
                        "hdr_size": record.hdr_size,
                        "payload_size": record.payload_size,
                    },
                )
            )

            if record.content_hash is not None:
                self._by_hash.setdefault(record.content_hash, key)

        self.count = len(self._entries)

    # --- reading -----------------------------------------------------------

    @property
    def closed(self) -> bool:
        """True once :meth:`close` has run; further reads raise ``ValueError``."""
        return self._closed

    def entries(self, *, include_deleted: bool = False) -> Iterator[EntryInfo]:
        # A WPK has no deleted-slot concept; every enumerated block is live.
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
        record = self._records[key]
        raw = self._read_stored(record)

        if not decoded:
            # The stored-bytes variant: the block exactly as it sits on disk.
            return self._track(io.BytesIO(raw))

        data = self._decode(key, record, raw)
        return self._track(io.BytesIO(data))

    def _read_stored(self, record: _BlockRecord) -> bytes:
        with self._source.open() as handle:
            handle.seek(record.offset)
            return handle.read(record.total_size)

    def _decode(self, key: int, record: _BlockRecord, raw: bytes) -> bytes:
        """Slice the payload and deliver decoded content, honouring the block.

        Stage-1 runs in one of two header dialects -- with or without the
        64-byte obfuscation reversal -- then the wrapper loop strips every
        layer it recognises.  The dialect is not recorded on disk (no idx
        field, no block-header flag), so each block's own content hash decides:

        1. decode with the obfuscation applied, fully unwrap, compare ``md5``
           against the header's content hash -- the writer hashed plaintext,
           so a match proves this dialect;
        2. otherwise decode skipping it and compare again;
        3. when neither matches (script packages hash something other than
           plaintext) or the block has no hash, take the dialect leaving fewer
           ``0x5A`` bytes in the decoded head -- the wrong dialect scrambles a
           clean header into 0x5A noise rather than raising.

        The wrong dialect usually just stops the unwrap loop early (its head
        matches no wrapper magic); when it does crash a decoder,
        :meth:`_decode_payload` guards the crash as a failed candidate.  A
        payload that fails to decode in both dialects is corrupt and raises --
        ``decoded=True`` promises content.
        """
        context = f"{self.path} block={key} off=0x{record.offset:X}"
        if record.hdr_size > 0 and record.hdr_size <= len(raw):
            end = record.hdr_size + record.payload_size
            payload = raw[record.hdr_size : end] if record.payload_size > 0 else raw[record.hdr_size :]
        else:
            payload = raw
        return self._decode_payload(payload, record.content_hash, context)

    @staticmethod
    def _decode_payload(
        payload: bytes, content_hash: bytes | None, context: str
    ) -> bytes:
        """Resolve the header dialect for one payload (see :meth:`_decode`).

        The content hash is the md5 of the *fully decoded* bytes, so the hash
        check can only run after the wrapper loop.  The wrong dialect can crash
        a wrapper decoder on the obfuscated bytes, so each dialect's unwrap is
        guarded: a crash simply rules that dialect out rather than surfacing as
        a read failure.  The obfuscated dialect is tried first, so the common
        case (its hash agrees) decodes exactly once.
        """

        def resolve(skip: bool) -> tuple[bytes | None, bool, Exception | None]:
            """Fully decode one dialect: (content, tagless, fault)."""
            try:
                data, decoded, _tag = try_decode_payload_stage1(
                    payload, context=context, skip_header_decode=skip
                )
            except PackageError as exc:
                return None, False, exc
            body = data if decoded else payload
            try:
                return unwrap_nested_payloads(body, context=context), not decoded, None
            except Exception as exc:  # noqa: BLE001 -- a wrong dialect can crash a decoder
                return None, False, exc

        with_full, with_tagless, with_fault = resolve(False)
        if with_full is not None:
            if content_hash is not None and hashlib.md5(with_full).digest() == content_hash:
                return with_full
            if with_tagless:
                # No stage-1 tag: the other dialect would reproduce these
                # same bytes, and the hash above already disagreed with them.
                return with_full

        skip_full, _skip_tagless, skip_fault = resolve(True)
        if skip_full is not None:
            if content_hash is not None and hashlib.md5(skip_full).digest() == content_hash:
                return skip_full

        if with_full is not None and skip_full is not None:
            # Neither dialect matches the hash (a package whose content hash
            # is not the plaintext md5, or a hashless block): the wrong
            # dialect scrambles a clean head into 0x5A noise rather than
            # raising, so the cleaner head wins.
            if with_full[:64].count(0x5A) <= skip_full[:64].count(0x5A):
                return with_full
            return skip_full
        if with_full is not None:
            # The skip dialect crashed a decoder; with delivered content, so
            # with is the dialect this block was written in.
            return with_full
        if skip_full is not None:
            return skip_full
        # Both dialects failed to deliver: nothing can be served.  Surface the
        # with-dialect fault.
        fault = with_fault if with_fault is not None else skip_fault
        if isinstance(fault, PackageError):
            raise fault
        raise PayloadDecodeError(
            FORMAT,
            context,
            "corrupt",
            f"no header dialect decodes the payload: {fault!r}",
        )

    # --- lifecycle ---------------------------------------------------------

    def _track(self, stream: EntryStream) -> EntryStream:
        with self._lock:
            if self._closed:
                raise ValueError("get_res on a closed archive")
            self._open_streams.append(stream)
        return stream

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            streams = list(self._open_streams)
            self._open_streams.clear()
        for stream in streams:
            stream.close()

    def __enter__(self) -> "WPKArchive":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def load(unit: Unit, options: ReadOptions) -> WPKArchive:
    """Build a WPK archive from the unit's entry file."""
    source = unit.root.consumed[0]
    archive = WPKArchive(source, unit.root.claim.key, options)
    archive.find_index()
    return archive
