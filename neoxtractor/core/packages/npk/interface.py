"""NPK container module: self-sufficient single-file archives.

An NPK carries its own entry table and, when the header's name-table flag
is set, a plaintext name table (NXFN) inside the same file.  An NXPK may
pull an optional sidecar name list (``npk_info_ext``) that fills the
entries NXFN left nameless.  Payload is decoded lazily per entry through
:class:`Archive.get_res`; ``load`` reads only the header and index tables.

Two header layouts share the magic.  The v1 header spans 24 bytes:
magic, entry count, the high half of the index offset, an encryption
byte, a name-table flag byte, two reserved bytes, the hash mode, and the
low half of the index offset.  The v2 header spans 25 bytes: ``0x08``
and ``0x0C`` are zero, ``0x10`` is a format version (>= 2), ``0x14`` is
the whole index offset, and ``0x18`` selects the index encoding -- 0
plain text, 1 AES-128-ECB, anything else unsupported.  The version word
is borrowed from v1's hash mode, so no header word proves v2 on its own:
the v1 reading is tried first and must validate; only a failed reading
on a v2-fingerprinted header opens the v2 path.

Index records span 28 bytes (32 with a 64-bit signature) and end with
four single-byte fields -- compression, a reserved byte, encryption, and
the high byte of the payload offset.
"""

from __future__ import annotations

import glob
import io
import os
import struct
import threading
import zlib
from dataclasses import dataclass
from typing import Any, Iterator

from arc4 import ARC4

from neoxtractor.core.packages.base import (
    Archive,
    Ask,
    Capability,
    Claim,
    EntryInfo,
    EntryStream,
    NameSource,
    PackageFile,
    ReadOptions,
    RefKind,
    Role,
    Status,
    Unit,
    Want,
)
from neoxtractor.core.packages.errors import (
    NoSuchEntry,
    PayloadDecodeError,
    UnsupportedRef,
)
from neoxtractor.core.utils.binary_readers import read_uint32
from neoxtractor.core.utils.filelike import Seekable
from neoxtractor.core.utils.wrappers import (
    check_lz4_like,
    check_nxs3,
    check_rotor,
    strip_none_wrapper,
    unpack_lz4_like,
    unpack_nxs3,
    unpack_rotor,
)
from ..npk_info_ext.interface import (
    FORMAT as SIDECAR_FORMAT,
    SIDECAR_SUFFIX,
    SidecarNames,
)

from .decompression import decompress_entry
from .decryption import (
    HAS_AES,
    decrypt_eggparty_index,
    decrypt_entry,
    decrypt_v2_index,
)
from .enums import (
    CompressionType,
    DecryptionType,
    NPKFileType,
    is_unknown,
)
from .expkkeys import EXPKKeyGenerator

FORMAT = "npk"
PRIORITY = 100

_MAGIC_NXPK = b"NXPK"
_MAGIC_EXPK = b"EXPK"
_MAGIC_NXFN = b"NXFN"

# ARC4 key for hash_mode == 3 index and payloads; each use builds its own
# cipher, so decoding one entry never depends on read order.
_ARC4_KEY = b"61ea476e-8201-11e5-864b-fcaa147137b7"

# Zero block for advancing a replayed ARC4 stream in bounded chunks.
_REPLAY_ZEROS = b"\x00" * 0x10000

_SUPPORTED_KINDS = (RefKind.INDEX, RefKind.NAME, RefKind.SIGNATURE)


def _sidecar_confirmed(file: PackageFile) -> Ask:
    """Build the query that binds a pulled sidecar to this NPK.

    The sidecar carries a verbatim copy of this file's on-disk index
    region; the query compares it against the stored bytes -- before any
    index decryption, so every mode binds the same way.  A backup whose
    length disagrees with the record count is declined without a read.
    """

    def confirm(*, backup: bytes) -> bool:
        if len(backup) == 0 or len(backup) % 0x1C != 0:
            return False
        head = file.read_head(0x18)
        if len(head) < 0x18 or head[:4] != _MAGIC_NXPK:
            return False
        count = struct.unpack_from("<I", head, 4)[0]
        index_offset = (
            struct.unpack_from("<I", head, 8)[0] << 32
        ) | struct.unpack_from("<I", head, 0x14)[0]
        if count * 0x1C != len(backup):
            return False
        with file.open() as handle:
            handle.seek(index_offset)
            return handle.read(len(backup)) == backup

    return confirm


@dataclass
class NPKReadOptions(ReadOptions):
    """Options for reading NPK files.

    ``decryption_key`` is required only by BASIC_XOR payloads; the other XOR
    variants derive their key from index metadata.  ``info_size`` overrides the
    auto-detected index record width when a file's layout is ambiguous.
    """

    decryption_key: int | None = None
    info_size: int | None = None


OPTIONS_TYPE = NPKReadOptions


@dataclass
class _IndexRecord:
    """One decoded NPK index row, kept private to the archive."""

    signature: int
    file_offset: int
    file_length: int
    file_original_length: int
    zcrc: int
    crc: int
    zip_flag: CompressionType
    encrypt_flag: DecryptionType
    name: str | None
    name_source: NameSource


@dataclass
class _V1Outcome:
    """A v1 reading's records plus the state a kept reading commits.

    A failed attempt discards the outcome, so a decryptor's stream state
    never leaks into another layout's archive.
    """

    records: "list[_IndexRecord]"
    key_generator: "EXPKKeyGenerator | None"
    arc4_len: int
    nxfn: "list[bytes]"


def matches(
    file: PackageFile, want_ask: "tuple[str, Any] | None" = None
) -> Claim | None:
    """Identify an NPK from its first four bytes.

    Identity is the magic alone; an NPK is self-sufficient and never a
    child, so a non-``None`` want_ask is declined.  An NXPK pulls its
    optional sidecar name list, ``<own name>.info_ext`` in the same
    directory, and offers the binding query; EXPK never asks.
    """
    if want_ask is not None:
        return None
    head = file.read_head(4)
    wants: tuple[Want, ...] = ()
    if head == _MAGIC_NXPK:
        wants = (
            Want(
                Role.NAMES,
                glob.escape(os.path.basename(file.path)) + SIDECAR_SUFFIX,
                max_files=1,
                child_ask=_sidecar_confirmed(file),
            ),
        )
    elif head != _MAGIC_EXPK:
        return None
    return Claim(
        format=FORMAT,
        entry=file.path,
        role=None,
        key=None,
        companions=(),
        parents=(),
        wants=wants,
    )


class NPKArchive(Archive):
    """A loaded NPK session: index in RAM, payload decoded on demand.

    All per-session state -- header fields, the index table, the name map, and
    the EXPK key stream -- lives on the instance and is released on
    :meth:`close`.  Payload bytes are read through the single
    :class:`PackageFile` the unit was handed; a fresh handle per ``get_res``
    keeps concurrent reads independent, so no shared file cursor is guarded.
    """

    def __init__(
        self,
        source: PackageFile,
        options: NPKReadOptions,
    ) -> None:
        self.format = FORMAT
        self.path = source.path
        self.members: tuple[str, ...] = (source.path,)

        self._source = source
        self._options = options
        self._lock = threading.Lock()
        self._open_streams: list[EntryStream] = []
        self._closed = False

        self._file_type = NPKFileType.NXPK
        self._file_count = 0
        self._index_offset = 0
        self._hash_mode = 0
        self._encrypt_mode = 0
        self._name_table = False
        self._info_size = 0
        self._nxfn_files: list[bytes] = []
        self._key_generator: EXPKKeyGenerator | None = None

        # ARC4 stream state for hash_mode 3: the bytes the index
        # decryption advanced, and per-entry prefix sums replaying it.
        self._index_arc4_len = 0
        self._arc4_prefix: list[int] = []

        self._records: list[_IndexRecord] = []
        self._entries: list[EntryInfo] = []
        self._by_name: dict[str, int] = {}
        self._by_sig: dict[int, int] = {}
        self.count = 0
        self.capabilities: frozenset[Capability] = frozenset({Capability.DATA})

    # --- loading -----------------------------------------------------------

    def load_index(
        self,
        sidecar_names: "tuple[bytes, ...] | None" = None,
    ) -> None:
        """Read the header and index tables; payload stays on disk.

        The v1 reading is tried first and must validate; only a failed
        reading on a v2-fingerprinted header gets the second one (see
        :meth:`_dispatch_layout`).  A forced ``info_size`` skips the
        dispatch.

        ``sidecar_names`` is a bound sidecar's positional name list; it
        fills names only where this file carries none of its own.
        """
        with self._source.open() as handle:
            header = self._parse_header(handle)
            if header is None:
                raise ValueError(f"Not a valid NPK file: {self.path}")
            if self._options.info_size is not None:
                self._info_size = self._options.info_size
                self._load_forced_v1(handle, header)
            else:
                self._dispatch_layout(handle, header)
        self._apply_sidecar(sidecar_names)
        self._build_views()
        if self._hash_mode == 3:
            self._build_arc4_prefix()

    def _parse_header(self, file: Seekable) -> "dict[str, int] | None":
        """Read the shared prefix of both layouts; None on a foreign magic.

        The words carry layout-neutral names: ``w08``/``w14`` are the
        halves of the v1 index offset and the low word alone the v2 one,
        ``w0c`` packs the v1 encryption and name-table bytes, ``w10`` is
        the v1 hash mode or the v2 version, ``b18`` the v2 encoding byte
        (-1 when absent; v1 never consults it).
        """
        magic = file.read(4)
        if magic == _MAGIC_NXPK:
            self._file_type = NPKFileType.NXPK
        elif magic == _MAGIC_EXPK:
            self._file_type = NPKFileType.EXPK
        else:
            return None
        header = {
            "count": read_uint32(file),
            "w08": read_uint32(file),
            "w0c": read_uint32(file),
            "w10": read_uint32(file),
            "w14": read_uint32(file),
        }
        tail = file.read(1)
        header["b18"] = tail[0] if tail else -1
        return header

    @staticmethod
    def _is_v2(header: "dict[str, int]") -> bool:
        """True when the header words could be a v2 fingerprint.

        Zeroed ``0x08``/``0x0C`` and a version of at least 2 at ``0x10``
        are necessary, never sufficient -- they overlap legal v1 values.
        This only gates entry after the v1 reading failed.
        """
        return header["w08"] == 0 and header["w0c"] == 0 and header["w10"] >= 2

    def _dispatch_layout(
        self, file: Seekable, header: "dict[str, int]"
    ) -> None:
        """Resolve the layout by trying each reading until one validates.

        v1 first; the v2 reading runs only after v1 failed on a
        fingerprinted header.  A reading commits nothing until it
        validates.
        """
        size = file.seek(0, 2)
        records = self._try_v1(file, header, size)
        if records is None and self._is_v2(header):
            records = self._try_v2(file, header, size)
        if records is not None:
            self._records = records
            return
        raise PayloadDecodeError(
            FORMAT,
            self.path,
            "unknown-algorithm",
            "index matches neither the v1 layout nor the v2 "
            "layout under any known key",
        )

    @staticmethod
    def _name_table_of(header: "dict[str, int]") -> bool:
        """The name-table flag: bit 0 of the byte at 0x0D.

        ``w0c`` packs the encryption byte (low) and the name-table byte
        (next), so the flag is ``(w0c >> 8) & 1``.
        """
        return bool((header["w0c"] >> 8) & 1)

    def _load_forced_v1(
        self, file: Seekable, header: "dict[str, int]"
    ) -> None:
        """Read a v1 index under a caller-forced record width, no arbiter.

        An explicit ``info_size`` is the caller asserting the layout.
        """
        count = header["count"]
        self._file_count = count
        self._index_offset = (header["w08"] << 32) | header["w14"]
        self._encrypt_mode = header["w0c"] & 0xFF
        self._name_table = self._name_table_of(header)
        self._hash_mode = header["w10"]
        if count == 0:
            return
        file.seek(self._index_offset)
        index_data = file.read(count * self._info_size)
        outcome = self._decode_v1_index(header, index_data, self._info_size)
        if outcome is None:
            # A forced layout has no second reading to fall back to.
            raise PayloadDecodeError(
                FORMAT,
                self.path,
                "unknown-algorithm",
                "enc3 index but no known EggParty key decrypts it",
            )
        self._records = outcome.records
        self._commit_v1_state(outcome)

    def _try_v1(
        self, file: Seekable, header: "dict[str, int]", size: int
    ) -> "list[_IndexRecord] | None":
        """Attempt the v1 reading; None when the index does not validate."""
        count = header["count"]
        index_offset = (header["w08"] << 32) | header["w14"]
        info_size = self._determine_info_size(
            file, count, index_offset, self._name_table_of(header)
        )
        if count and (
            info_size < 28
            or index_offset + count * info_size > size
        ):
            return None
        file.seek(index_offset)
        raw = file.read(count * info_size) if count else b""
        outcome = self._decode_v1_index(header, raw, info_size)
        if outcome is None:
            return None
        if not self._validate_records(outcome.records, index_offset):
            return None
        self._file_count = count
        self._index_offset = index_offset
        self._encrypt_mode = header["w0c"] & 0xFF
        self._name_table = self._name_table_of(header)
        self._hash_mode = header["w10"]
        self._info_size = info_size
        self._commit_v1_state(outcome)
        return outcome.records

    def _commit_v1_state(self, outcome: _V1Outcome) -> None:
        """Store a kept v1 reading's decryptor and name-table state."""
        self._key_generator = outcome.key_generator
        self._index_arc4_len = outcome.arc4_len
        self._nxfn_files = outcome.nxfn

    def _decode_v1_index(
        self,
        header: "dict[str, int]",
        index_data: bytes,
        info_size: int,
    ) -> "_V1Outcome | None":
        """Apply the v1 index transformations and parse the records.

        ``None`` when a decryptor declines the bytes (an unknown
        EggParty key).  Nothing is stored on the archive: the caller
        commits the outcome only once the reading validates.
        """
        count = header["count"]
        if count == 0:
            return _V1Outcome([], None, 0, [])
        key_generator: EXPKKeyGenerator | None = None
        if self._file_type == NPKFileType.EXPK:
            key_generator = EXPKKeyGenerator()
            index_data = key_generator.decrypt(index_data)
        index_arc4_len = 0
        if header["w10"] == 3:
            index_arc4_len = len(index_data)
            index_data = ARC4(_ARC4_KEY).decrypt(index_data)
        if header["w0c"] & 0xFF == 3:
            try:
                index_data = decrypt_eggparty_index(index_data, info_size)
            except ValueError:
                return None
        nxfn: list[bytes] = []
        if self._name_table_of(header):
            names_at = (
                ((header["w08"] << 32) | header["w14"]) + count * info_size
            )
            with self._source.open() as handle:
                nxfn = self._read_nxfn_table(handle, names_at)
        records = self._parse_records(index_data, nxfn, info_size)
        return _V1Outcome(records, key_generator, index_arc4_len, nxfn)

    def _try_v2(
        self, file: Seekable, header: "dict[str, int]", size: int
    ) -> "list[_IndexRecord] | None":
        """Attempt the v2 reading; None when it is unsupported or invalid.

        The index offset lives at ``0x14``, records are 28 bytes, and
        there is no in-file name table.  ``0x18`` selects the encoding:
        0 plain text, 1 AES-128-ECB over whole blocks, anything else
        unsupported.  ``_hash_mode`` commits 0 -- the v2 reader knows no
        stream mode.
        """
        flag = header["b18"]
        if flag not in (0, 1):
            return None
        count = header["count"]
        if count == 0:
            self._commit_v2(header, 0)
            return []
        index_offset = header["w14"]
        region = count * 28
        if flag == 1:
            if not HAS_AES:
                return None
            needed = (region + 15) & ~15
            if index_offset + needed > size:
                return None
            file.seek(index_offset)
            data = decrypt_v2_index(file.read(needed))[:region]
        else:
            if index_offset + region > size:
                return None
            file.seek(index_offset)
            data = file.read(region)
        records = self._parse_records(data, [], 28)
        if not self._validate_records(records, index_offset):
            return None
        self._commit_v2(header, count)
        return records

    def _commit_v2(self, header: "dict[str, int]", count: int) -> None:
        """Store the v2 layout's header fields after a valid reading."""
        self._file_count = count
        self._index_offset = header["w14"]
        self._encrypt_mode = 0
        self._name_table = False
        self._hash_mode = 0
        self._info_size = 28

    @staticmethod
    def _validate_records(
        records: "list[_IndexRecord]", index_offset: int
    ) -> bool:
        """True when a parsed table could be this file's index.

        Two independent facts: signatures are non-decreasing (the table
        is sorted by name hash, so wrong bytes scatter them), and every
        payload lies below the index, which both layouts place after all
        payload.  The geometry check also settles a single record, whose
        order alone says nothing.
        """
        if not records:
            return True
        signatures = [record.signature for record in records]
        if any(cur < prev for prev, cur in zip(signatures, signatures[1:])):
            return False
        return all(
            record.file_offset + record.file_length <= index_offset
            for record in records
        )

    def _read_nxfn_table(self, file: Seekable, offset: int) -> list[bytes]:
        """Read the NXFN name table at *offset*; [] when it is not understood.

        Layout: 16-byte header (magic ``NXFN``, encryption byte, reserved
        byte, compression word, stored length, uncompressed length) then the
        table body of NUL-separated names.  Compression 0 stores the body as
        plain text and 1 as a zlib stream.  A missing magic, an unsupported
        algorithm, or a length mismatch leaves the entries nameless.
        """
        file.seek(offset)
        header = file.read(16)
        if len(header) < 16 or header[:4] != _MAGIC_NXFN:
            return []
        encrypt_flag = header[4]
        zip_flag = struct.unpack_from("<H", header, 6)[0]
        clen, ulen = struct.unpack_from("<II", header, 8)
        if encrypt_flag != 0:
            return []
        if zip_flag == 0:
            body = file.read(clen)
        elif zip_flag == 1:
            try:
                body = zlib.decompress(file.read(clen))
            except zlib.error:
                return []
        else:
            return []
        if len(body) != ulen:
            return []
        return [x for x in body.split(b"\x00") if x != b""]

    def _determine_info_size(
        self, file: Seekable, count: int, index_offset: int, name_table: bool
    ) -> int:
        """Resolve the width of one index record from the file's layout."""
        if name_table:
            return 0x1C
        file.seek(0, 2)
        index_end = file.tell()
        if count == 0:
            return 0
        return (index_end - index_offset) // count

    def _parse_records(
        self, index_data: bytes, nxfn: "list[bytes]", info_size: int
    ) -> "list[_IndexRecord]":
        """Project raw index bytes into records, positionally named.

        Records are 28 bytes, or 32 with a 64-bit signature; the fields
        after the signature keep their offsets relative to the record
        tail.  A width outside both forms reads 28-byte records from the
        head of each slot.
        """
        count = len(index_data) // info_size if info_size else 0
        stride = info_size if info_size in (28, 32) else 28
        records: list[_IndexRecord] = []
        for i in range(count):
            base = i * stride
            fields = base + stride - 24
            if stride == 32:
                signature = struct.unpack_from("<Q", index_data, base)[0]
            else:
                signature = struct.unpack_from("<I", index_data, base)[0]
            file_offset = struct.unpack_from("<I", index_data, fields)[0]
            file_length = struct.unpack_from("<I", index_data, fields + 4)[0]
            file_original_length = struct.unpack_from(
                "<I", index_data, fields + 8
            )[0]
            zcrc = struct.unpack_from("<I", index_data, fields + 12)[0]
            crc = struct.unpack_from("<I", index_data, fields + 16)[0]

            zip_flag = index_data[fields + 20]
            if zip_flag == 5:
                zip_flag = 2
            zip_type = CompressionType(zip_flag)

            encrypt_flag = index_data[fields + 22]
            if encrypt_flag == 3:
                encrypt_flag = 2
            encrypt_type = DecryptionType(encrypt_flag)

            file_offset |= index_data[fields + 23] << 32

            structure = nxfn[i] if i < len(nxfn) else None
            name, source = self._resolve_name(structure, signature)

            records.append(
                _IndexRecord(
                    signature=signature,
                    file_offset=file_offset,
                    file_length=file_length,
                    file_original_length=file_original_length,
                    zcrc=zcrc,
                    crc=crc,
                    zip_flag=zip_type,
                    encrypt_flag=encrypt_type,
                    name=name,
                    name_source=source,
                )
            )
        return records

    def _apply_sidecar(
        self, names: "tuple[bytes, ...] | None"
    ) -> None:
        """Fill nameless records from a bound sidecar's positional list.

        A list that does not cover every record is ignored whole.  An
        existing name -- NXFN or otherwise -- outranks the sidecar; a
        name that will not decode leaves its record nameless.
        """
        if names is None or len(names) != len(self._records):
            return
        for record, raw in zip(self._records, names):
            if record.name is not None:
                continue
            try:
                record.name = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            record.name_source = NameSource.SIDECAR

    def _resolve_name(
        self, structure: bytes | None, signature: int
    ) -> tuple[str | None, NameSource]:
        if structure is not None:
            try:
                return structure.decode("utf-8"), NameSource.NXFN
            except UnicodeDecodeError:
                pass  # a dirty NXFN entry degrades to no-name, never hex stand-in
        return None, NameSource.NONE

    def _build_views(self) -> None:
        """Project index records into neutral EntryInfo and lookup dicts."""
        name_map = self._options.name_map or {}
        has_plaintext = False
        for key, record in enumerate(self._records):
            override = name_map.get(record.signature)
            name = override if override is not None else record.name
            source = (
                NameSource.MAP
                if override is not None
                else record.name_source
            )
            if source in (NameSource.NXFN, NameSource.SIDECAR):
                has_plaintext = True

            status = self._entry_status(record)
            ext_hint = (
                os.path.splitext(name)[1].lstrip(".") or None
                if name
                else None
            )

            self._entries.append(
                EntryInfo(
                    key=key,
                    name=name,
                    name_source=source,
                    signature=record.signature,
                    content_hash=None,
                    ext_hint=ext_hint,
                    stored_size=record.file_length,
                    decoded_size=record.file_original_length,
                    container=self.path,
                    status=status,
                    extra={
                        "zip_flag": record.zip_flag.value,
                        "encrypt_flag": record.encrypt_flag.value,
                    },
                )
            )

            if name is not None and name not in self._by_name:
                self._by_name[name] = key
            self._by_sig.setdefault(record.signature, key)

        caps = {Capability.DATA, Capability.DECODED_SIZE}
        if has_plaintext:
            caps.add(Capability.NAMES_PLAINTEXT)
        self.capabilities = frozenset(caps)
        self.count = len(self._entries)

    def _entry_status(self, record: _IndexRecord) -> Status:
        # Unknown algorithms decode by falling through untouched: the
        # bytes are neither OK nor fixable by a withheld option, so they
        # outrank a missing key.
        if is_unknown(record.encrypt_flag) or is_unknown(record.zip_flag):
            return Status.UNRECOGNIZED
        if (
            record.encrypt_flag == DecryptionType.BASIC_XOR
            and self._options.decryption_key is None
        ):
            return Status.ENCRYPTED
        return Status.OK

    # --- reading -----------------------------------------------------------

    @property
    def closed(self) -> bool:
        """True once :meth:`close` has run; further reads raise ``ValueError``."""
        return self._closed

    def entries(self, *, include_deleted: bool = False) -> Iterator[EntryInfo]:
        # NPK has no deleted-slot concept; every index row is live.
        return iter(self._entries)

    def _resolve_key(self, ref: int | str | bytes, kind: RefKind) -> int:
        if kind is RefKind.INDEX:
            if isinstance(ref, int) and 0 <= ref < len(self._records):
                return ref
            raise NoSuchEntry(ref, kind)
        if kind is RefKind.NAME:
            key = self._by_name.get(str(ref))
            if key is None:
                raise NoSuchEntry(ref, kind)
            return key
        if kind is RefKind.SIGNATURE:
            if not isinstance(ref, int):
                raise NoSuchEntry(ref, kind)
            key = self._by_sig.get(ref)
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
            # Stored-bytes channel: exactly what sits in the container,
            # without decryption, decompression, or unwrapping.
            return self._track(io.BytesIO(raw))

        # decoded=True promises decoded content: a withheld key or an
        # unknown algorithm raises, never passes undecoded bytes off as
        # plaintext.
        status = self._entry_status(record)
        if status is Status.ENCRYPTED:
            raise PayloadDecodeError(
                FORMAT,
                self.path,
                "missing-option",
                "BASIC_XOR payload but no decryption_key was supplied; read "
                "decoded=False for the stored bytes or provide the key",
                entry=key,
            )
        if status is Status.UNRECOGNIZED:
            raise PayloadDecodeError(
                FORMAT,
                self.path,
                "unknown-algorithm",
                f"index names an algorithm this build cannot decode "
                f"(encrypt={record.encrypt_flag}, zip={record.zip_flag})",
                entry=key,
            )

        data = self._decode(key, record, raw)
        return self._track(io.BytesIO(data))

    def _read_stored(self, record: _IndexRecord) -> bytes:
        with self._source.open() as handle:
            handle.seek(record.file_offset)
            return handle.read(record.file_length)

    def _decode(self, key: int, record: _IndexRecord, raw: bytes) -> bytes:
        """Decode one payload, mirroring the reference control flow exactly.

        EXPK stream, config-key XOR, then the hash_mode 3 branch or a
        plain decompress.  hash_mode 3 replays the shared ARC4 stream by
        length so random access equals sequential.
        """
        data = raw
        if self._file_type == NPKFileType.EXPK and self._key_generator:
            data = self._key_generator.decrypt(data)

        if record.encrypt_flag != DecryptionType.NONE:
            decrypted = decrypt_entry(
                data,
                record.encrypt_flag,
                record.crc,
                record.file_length,
                record.file_original_length,
                self._options.decryption_key,
            )
            if decrypted is None:
                raise PayloadDecodeError(
                    FORMAT,
                    self.path,
                    "missing-option",
                    f"decrypt_entry reports a required option is missing for "
                    f"encrypt_flag={record.encrypt_flag}",
                    entry=key,
                )
            data = decrypted

        tried_and_failed = self._hash_mode != 3
        if self._hash_mode == 3:
            data, tried_and_failed = self._decode_hashmode3(key, record, data)

        if record.zip_flag != CompressionType.NONE and tried_and_failed:
            try:
                data = decompress_entry(
                    data, record.zip_flag, record.file_original_length
                )
            except Exception as exc:
                raise PayloadDecodeError(
                    FORMAT,
                    self.path,
                    "corrupt",
                    f"decompression failed for zip_flag={record.zip_flag}: {exc}",
                    entry=key,
                ) from exc

        return self._unwrap(data, entry=key)

    def _decode_hashmode3(
        self, key: int, record: _IndexRecord, data: bytes
    ) -> tuple[bytes, bool]:
        """Handle the hash_mode 3 branch, returning (data, tried_and_failed).

        Only NONE and LZ4 payloads are touched, exactly as in the
        reference: NONE advances the shared ARC4 stream, LZ4 decompresses
        without touching it, and any other zip_flag falls through with
        tried_and_failed False, skipping the later decompress step.  The
        stream is rebuilt by advancing over the byte counts of every
        earlier NONE payload, reproducing sequential decoding for any
        access order.
        """
        if record.zip_flag == CompressionType.NONE:
            cipher = ARC4(_ARC4_KEY)
            self._advance_stream(cipher, self._arc4_prefix[key])
            return cipher.decrypt(data), False
        if record.zip_flag == CompressionType.LZ4:
            try:
                return (
                    decompress_entry(
                        data, record.zip_flag, record.file_original_length
                    ),
                    False,
                )
            except Exception as exc:
                raise PayloadDecodeError(
                    FORMAT,
                    self.path,
                    "corrupt",
                    f"hash_mode 3 LZ4 payload failed to decompress: {exc}",
                    entry=key,
                ) from exc
        # In hash_mode 3 the ARC4 stream couples to NONE payloads and LZ4 uses
        # block decompression; no other zip_flag is decodable here, so falling
        # through would return undecoded bytes under a decoded=True request.
        raise PayloadDecodeError(
            FORMAT,
            self.path,
            "unknown-algorithm",
            f"hash_mode 3 payload with zip_flag={record.zip_flag} is not "
            f"decodable by this branch",
            entry=key,
        )

    def _build_arc4_prefix(self) -> None:
        """Tabulate stream bytes consumed before each NONE payload.

        ``_arc4_prefix[i]`` is the index bytes plus the ``file_length``
        of every earlier NONE payload; ARC4 advances on lengths alone.
        """
        total = self._index_arc4_len
        self._arc4_prefix = []
        for record in self._records:
            self._arc4_prefix.append(total)
            if record.zip_flag == CompressionType.NONE:
                total += record.file_length

    @staticmethod
    def _advance_stream(cipher: ARC4, n: int) -> None:
        """Advance ``cipher`` by ``n`` bytes using bounded chunk allocations."""
        remaining = n
        while remaining > 0:
            chunk = min(remaining, len(_REPLAY_ZEROS))
            cipher.decrypt(_REPLAY_ZEROS[:chunk])
            remaining -= chunk

    def _unwrap(self, data: bytes, *, entry: int) -> bytes:
        """Strip nested wrappers until the payload stops changing."""
        seen: set[tuple[int, bytes]] = set()
        for _ in range(32):
            signature = (len(data), data[:16])
            if signature in seen:
                break
            seen.add(signature)

            stripped = strip_none_wrapper(data)
            if stripped != data:
                data = stripped
                continue

            if check_lz4_like(data):
                try:
                    unpacked = unpack_lz4_like(data)
                except Exception as exc:
                    raise PayloadDecodeError(
                        FORMAT,
                        self.path,
                        "corrupt",
                        f"LZ4-like wrapper detected but unpack failed: {exc}",
                        entry=entry,
                    ) from exc
                if unpacked and unpacked != data:
                    data = unpacked
                    continue

            if check_rotor(data):
                data = unpack_rotor(data)
                continue

            if check_nxs3(data):
                data = unpack_nxs3(data)
                continue

            break
        return data

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

    def __enter__(self) -> "NPKArchive":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def load(unit: Unit, options: ReadOptions) -> NPKArchive:
    """Build an NPK archive from the unit's entry file.

    A sidecar name list that attached to this unit contributes its parsed
    table from ``Claim.private``; the archive fills its nameless records
    from it.  A unit without a sidecar loads exactly as before.
    """
    assert isinstance(options, NPKReadOptions)
    source = unit.root.consumed[0]
    sidecar_names: "tuple[bytes, ...] | None" = None
    for child in unit.children:
        if (
            child.claim.format == SIDECAR_FORMAT
            and isinstance(child.claim.private, SidecarNames)
        ):
            sidecar_names = child.claim.private.names
            break
    archive = NPKArchive(source, options)
    archive.load_index(sidecar_names)
    return archive
