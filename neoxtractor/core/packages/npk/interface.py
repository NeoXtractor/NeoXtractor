"""NPK container module: self-sufficient single-file archives.

An NPK carries its own entry table and, when the header's name-table flag is
set, a plaintext name table (NXFN) inside the same file, so it declares no
companions, parents, or wants and never attaches to a unit.  Payload is
decoded lazily per entry through :class:`Archive.get_res`; ``load`` reads only
the header and index tables.

The header spans 24 bytes: magic, entry count, the high half of the index
offset, an encryption byte, a name-table flag byte, two reserved bytes, the
hash mode, and the low half of the index offset.  Each index record spans 28
bytes and ends with four single-byte fields -- compression, a reserved byte,
encryption, and the high byte of the payload offset -- so both offsets are
64-bit and archives may exceed 4 GiB.

Direct port of the reference decoder: header layout, index record widths,
and the decrypt/decompress chain follow its control flow exactly -- do not
simplify.  Key material is copied verbatim, never paraphrased.
"""

from __future__ import annotations

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
    Capability,
    Claim,
    EntryInfo,
    EntryStream,
    NameSource,
    PackageFile,
    ReadOptions,
    RefKind,
    Status,
    Unit,
)
from neoxtractor.core.packages.errors import (
    NoSuchEntry,
    PayloadDecodeError,
    UnsupportedRef,
)
from neoxtractor.core.utils.binary_readers import (
    read_uint8,
    read_uint16,
    read_uint32,
    read_uint64,
)
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

from .decompression import decompress_entry
from .decryption import decrypt_eggparty_index, decrypt_entry
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

# ARC4 key for hash_mode == 3 index and plaintext payloads.  Copied verbatim
# from the format; each use builds its own cipher so decoding one entry never
# depends on the order entries were read.
_ARC4_KEY = b"61ea476e-8201-11e5-864b-fcaa147137b7"

# Zero block used to advance a replayed ARC4 stream in bounded chunks; ARC4
# output depends only on how many bytes pass, not their values.
_REPLAY_ZEROS = b"\x00" * 0x10000

_SUPPORTED_KINDS = (RefKind.INDEX, RefKind.NAME, RefKind.SIGNATURE)


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


def matches(
    file: PackageFile, want_ask: "tuple[str, Any] | None" = None
) -> Claim | None:
    """Identify an NPK from its first four bytes.

    Identity comes from this file's magic alone; an NPK is self-sufficient, so
    the claim carries no role, companions, parents, or wants.  An NPK is never
    a child: a non-``None`` want_ask means the file was pulled into someone
    else's unit, which this format declines outright.
    """
    if want_ask is not None:
        return None
    head = file.read_head(4)
    if head not in (_MAGIC_NXPK, _MAGIC_EXPK):
        return None
    return Claim(
        format=FORMAT,
        entry=file.path,
        role=None,
        key=None,
        companions=(),
        parents=(),
        wants=(),
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

        # Bytes the shared ARC4 stream advanced while decrypting the index
        # (hash_mode 3 only).  Payload replay starts from this offset so a
        # random-access get_res reproduces the reference's sequential stream.
        self._index_arc4_len = 0
        # Prefix sums of stream bytes consumed before each NONE payload;
        # built once when hash_mode 3, queried in O(1) per get_res.
        self._arc4_prefix: list[int] = []

        self._records: list[_IndexRecord] = []
        self._entries: list[EntryInfo] = []
        self._by_name: dict[str, int] = {}
        self._by_sig: dict[int, int] = {}
        self.count = 0
        self.capabilities: frozenset[Capability] = frozenset({Capability.DATA})

    # --- loading -----------------------------------------------------------

    def load_index(self) -> None:
        """Read the header and index tables; payload stays on disk."""
        with self._source.open() as handle:
            self._read_header(handle)
            self._read_indices(handle)
        self._build_views()
        if self._hash_mode == 3:
            self._build_arc4_prefix()

    def _read_header(self, file: Seekable) -> None:
        magic = file.read(4)
        if magic == _MAGIC_NXPK:
            self._file_type = NPKFileType.NXPK
        elif magic == _MAGIC_EXPK:
            self._file_type = NPKFileType.EXPK
        else:
            raise ValueError(f"Not a valid NPK file: {self.path}")

        self._file_count = read_uint32(file)
        index_offset_high = read_uint32(file)
        self._encrypt_mode = read_uint8(file)
        self._name_table = bool(read_uint8(file) & 1)
        read_uint16(file)
        self._hash_mode = read_uint32(file)
        self._index_offset = (index_offset_high << 32) | read_uint32(file)

        self._info_size = (
            self._options.info_size
            if self._options.info_size is not None
            else self._determine_info_size(file)
        )

        if self._name_table:
            names_at = self._index_offset + self._file_count * self._info_size
            self._nxfn_files = self._read_nxfn_table(file, names_at)

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

    def _determine_info_size(self, file: Seekable) -> int:
        """Resolve the width of one index record from the file's layout."""
        if self._name_table:
            return 0x1C
        current_pos = file.tell()
        file.seek(self._index_offset)
        index_start = file.tell()
        file.seek(0, 2)
        index_end = file.tell()
        file.seek(current_pos)
        if self._file_count == 0:
            return 0
        return (index_end - index_start) // self._file_count

    def _read_indices(self, file: Seekable) -> None:
        file.seek(self._index_offset)
        if self._file_count == 0:
            return
        index_data = file.read(self._file_count * self._info_size)

        if self._file_type == NPKFileType.EXPK:
            self._key_generator = EXPKKeyGenerator()
            index_data = self._key_generator.decrypt(index_data)
        if self._hash_mode == 3:
            self._index_arc4_len = len(index_data)
            index_data = ARC4(_ARC4_KEY).decrypt(index_data)
        if self._encrypt_mode == 3:
            index_data = decrypt_eggparty_index(index_data, self._info_size)

        nxfn = self._nxfn_files
        with io.BytesIO(index_data) as buf:
            for i in range(self._file_count):
                if self._info_size == 28:
                    signature = read_uint32(buf)
                elif self._info_size == 32:
                    signature = read_uint64(buf)
                else:
                    signature = read_uint32(buf)

                file_offset = read_uint32(buf)
                file_length = read_uint32(buf)
                file_original_length = read_uint32(buf)
                zcrc = read_uint32(buf)
                crc = read_uint32(buf)

                zip_flag = read_uint8(buf)
                if zip_flag == 5:
                    zip_flag = 2
                zip_type = CompressionType(zip_flag)

                read_uint8(buf)
                encrypt_flag = read_uint8(buf)
                if encrypt_flag == 3:
                    encrypt_flag = 2
                encrypt_type = DecryptionType(encrypt_flag)

                file_offset |= read_uint8(buf) << 32

                structure = nxfn[i] if i < len(nxfn) else None
                name, source = self._resolve_name(structure, signature)

                self._records.append(
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
            if source is NameSource.NXFN:
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
        # Unknown algorithms (the enums' _missing_ makes an UNKNOWN(n)
        # member) decode by falling through untouched: the bytes are neither
        # OK (content would look trustworthy) nor ENCRYPTED (no withheld
        # option would fix them).  They outrank a missing key -- even with
        # the key there is no decoder here.
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

        # decoded=True promises decoded content: a withheld key or an unknown
        # algorithm raises instead of returning undecoded bytes.  Status is a
        # display hint for the entry list, never a licence to serve ciphertext
        # as plaintext.
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

        The order and the failure boundaries match the original reader: EXPK
        stream, then the config-key XOR, then either the hash_mode 3 branch or
        a plain decompress.  A decompression that raises returns the bytes it
        had (never the wrapper loop); hash_mode 3 is handled by a length-only
        replay of the shared ARC4 stream so random access equals sequential.
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

        Only NONE and LZ4 payloads are touched here, exactly as in the
        reference: NONE advances the shared ARC4 stream, LZ4 decompresses
        without touching it, and any other zip_flag falls through untouched
        (leaving tried_and_failed False so the later decompress step is
        skipped).  An exception flips tried_and_failed so the decompress step
        runs, matching the reference.  The stream is rebuilt from the index
        offset by advancing over the byte counts of every earlier NONE payload,
        which reproduces sequential decoding for any access order.
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

        ``_arc4_prefix[i]`` is the total the reference stream would have
        advanced before decoding entry ``i``: the index bytes plus the
        ``file_length`` of every earlier NONE payload (LZ4 and other zip
        flags never touch the stream).  Contents are irrelevant to an ARC4
        advance, so lengths alone reproduce the sequential state exactly.
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
    """Build an NPK archive from the unit's entry file."""
    assert isinstance(options, NPKReadOptions)
    source = unit.root.consumed[0]
    archive = NPKArchive(source, options)
    archive.load_index()
    return archive
