"""IDX + WPK / standalone WPK reader."""

from __future__ import annotations

import os
from typing import BinaryIO, Dict, List

from core.formats import process_entry_with_processors
from core.logger import get_logger
from core.npk.class_types import (
    NPKEntry,
    NPKEntryDataFlags,
    NPKIndex,
    NPKReadOptions,
    State,
)
from core.npk.detection import get_ext, get_file_category, is_binary
from core.wpk.decryption import try_decode_payload_stage1
from id5mapping.paths import restore_archive_paths

from .constants import (
    EMBEDDED_MAGIC,
    IDX_HEAD_SIZE,
    IDX_REC_SIZE,
    MIN_EMBEDDED_HEADER_SIZE,
    WPK_MAGIC,
)
from .idx_reader import log_idx_split_summary, read_idx_header, read_idx_indices
from .paths import WPKPathResolver
from .payload import WPKPayloadProcessor
from .slot_file import SlotFileResolver
from .standalone import (
    build_index_from_embedded_header,
    read_wpk_header,
    scan_wpk_indices,
)

class MissingIndexedResourceError(EOFError):
    """Entry exists in IDX, but the actual data cannot be read from the current resource set."""


def _read_idx_records(idx_path: str) -> List[dict]:
    """Read the fixed-size SKPW records of another IDX (metadata only)."""
    records: List[dict] = []
    with open(idx_path, "rb") as file:
        if file.read(4) != b"SKPW":
            raise ValueError(f"Not a valid WPKS (.idx) file: {idx_path}")
        file.seek(0x0C)
        count = int.from_bytes(file.read(4), "little")
        file.seek(IDX_HEAD_SIZE)
        for _ in range(count):
            rec = file.read(IDX_REC_SIZE)
            if len(rec) != IDX_REC_SIZE:
                break
            raw_hash = rec[0x00:0x10]
            hdr_size = int.from_bytes(rec[0x20:0x22], "little")
            payload_size = int.from_bytes(rec[0x1C:0x20], "little")
            records.append(
                {
                    "raw_hash_hex": raw_hash.hex(),
                    "pkg_id": rec[0x14],
                    "file_offset": int.from_bytes(rec[0x18:0x1C], "little"),
                    "file_length": hdr_size + payload_size,
                    "hdr_size": hdr_size,
                    "payload_size": payload_size,
                }
            )
    return records

class IDXWPKFile:
    """
    Reader for:
    - SKPW IDX + one/many WPK files
    - direct standalone FKPW WPK scanning

    Current assumptions derived from user samples:
    - IDX magic: b"SKPW"
    - IDX header size: 0x20
    - IDX record size: 0x24
    - WPK package magic: b"FKPW"
    - Embedded entry magic inside WPK: b"1DPW"
    - Embedded entry header size usually stored at +0x24 (u16)
    - Embedded entry payload size usually stored at +0x20 (u32)
    """

    IDX_HEAD_SIZE = IDX_HEAD_SIZE
    IDX_REC_SIZE = IDX_REC_SIZE
    WPK_MAGIC = WPK_MAGIC
    EMBEDDED_MAGIC = EMBEDDED_MAGIC
    MIN_EMBEDDED_HEADER_SIZE = MIN_EMBEDDED_HEADER_SIZE

    def __init__(
        self,
        file_path: str,
        options: NPKReadOptions | None = None,
        merge_counterpart: bool = False,
        restore_paths: bool = False,
    ):
        self.file_path = file_path
        self.options = options if options is not None else NPKReadOptions()
        self.merge_counterpart = merge_counterpart
        self.restore_paths = restore_paths

        self.entries: Dict[int, NPKEntry] = {}
        self.indices: List[NPKIndex] = []
        self.file_count: int = 0
        self._wpk_cache: Dict[int, BinaryIO | None] = {}
        self._wpk_paths: Dict[int, str] = {}
        self._alt_handle_cache: Dict[tuple[str, int], BinaryIO | None] = {}
        self.counterpart_idx_path: str | None = None

        self._missing_entry_count: int = 0
        self._partial_entry_count: int = 0
        self._other_error_count: int = 0

        self.mode = ""
        self.idx_path: str | None = None
        self.wpk_path: str | None = None
        self.base_dir = os.path.dirname(file_path)
        self.base_stem = os.path.splitext(os.path.basename(file_path))[0]

        self._path_resolver = WPKPathResolver(self)
        self._slot_file_resolver = SlotFileResolver(self)
        self._payload_processor = WPKPayloadProcessor()

        with open(self.file_path, "rb") as file:
            magic = file.read(4)

        if magic == b"SKPW":
            self.mode = "idx"
            self.idx_path = file_path
            get_logger().info("Opening IDX file: %s", self.idx_path)
            with open(self.idx_path, "rb") as file:
                self._read_idx_header(file)
                self._read_idx_indices(file)
            if self.merge_counterpart:
                self._merge_counterpart_idx()
        elif magic == self.WPK_MAGIC:
            self.mode = "wpk"
            self.wpk_path = file_path
            get_logger().info("Opening standalone WPK file: %s", self.wpk_path)
            with open(self.wpk_path, "rb") as file:
                self._read_wpk_header(file)
                self._scan_wpk_indices(file)
        else:
            raise ValueError(f"Unsupported IDX/WPK file type: {file_path}")

        if self.mode == "idx" and self.restore_paths:
            restore_archive_paths(self)

    def apply_path_mapping(self, thy_paths: list[str] | None = None) -> int:
        """Re-run path restoration with the given `.thy` tables (base first)."""
        if self.mode != "idx":
            return 0
        restored = restore_archive_paths(self, thy_paths)
        for index, entry in self.entries.items():
            if index < len(self.indices):
                entry.filename = self.indices[index].filename
        return restored

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def close(self):
        for handle in self._wpk_cache.values():
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass
        self._wpk_cache.clear()
        for handle in self._alt_handle_cache.values():
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass
        self._alt_handle_cache.clear()

    # ------------------------------------------------------------------
    # Base / hot update overlay
    # ------------------------------------------------------------------
    def _merge_counterpart_idx(self) -> None:
        """Overlay this IDX with its counterpart (base `<root>/res` <-> hot `<root>/Documents/res`).

        Hot update packages only carry the files that changed, so opening one
        alone hides every base resource. Merging both record lists (hot rows
        win on identical md5) keeps the full listing while every row still
        points at its own WPK through `_alt_dir`.
        """
        idx_dir = os.path.dirname(os.path.abspath(self.idx_path or self.file_path))
        name = os.path.basename(self.idx_path or self.file_path)
        parent = os.path.dirname(idx_dir)
        is_hot = os.path.basename(parent).lower() == "documents"
        if is_hot:
            counterpart_dir = os.path.join(os.path.dirname(parent), os.path.basename(idx_dir))
        else:
            counterpart_dir = os.path.join(parent, "Documents", os.path.basename(idx_dir))
        counterpart = os.path.join(counterpart_dir, name)
        if not os.path.exists(counterpart):
            return

        try:
            records = _read_idx_records(counterpart)
        except Exception:  # noqa: BLE001
            get_logger().exception("Failed to read counterpart IDX %s", counterpart)
            return

        current = {index.raw_hash_hex: index for index in self.indices}
        added = 0
        replaced = 0
        for rec in records:
            md5_hex = rec["raw_hash_hex"]
            index = current.get(md5_hex)
            if index is not None:
                if not is_hot:
                    # Counterpart is the hot package: its version wins.
                    index.pkg_id = rec["pkg_id"]
                    index.file_offset = rec["file_offset"]
                    index.file_length = rec["file_length"]
                    index.file_original_length = rec["file_length"]
                    index.hdr_size = rec["hdr_size"]
                    index.payload_size = rec["payload_size"]
                    index._alt_dir = counterpart_dir
                    replaced += 1
                continue
            index = NPKIndex()
            index.file_signature = int.from_bytes(bytes.fromhex(md5_hex), "little")
            index.file_offset = rec["file_offset"]
            index.file_length = rec["file_length"]
            index.file_original_length = rec["file_length"]
            index.filename = md5_hex
            index.pkg_id = rec["pkg_id"]
            index.hdr_size = rec["hdr_size"]
            index.payload_size = rec["payload_size"]
            index.raw_hash_hex = md5_hex
            index._alt_dir = counterpart_dir
            self.indices.append(index)
            current[md5_hex] = index
            added += 1

        self.file_count = len(self.indices)
        self.counterpart_idx_path = counterpart
        get_logger().info(
            "IDX overlay: merged %s (%d added, %d replaced, %d rows total)",
            counterpart,
            added,
            replaced,
            len(self.indices),
        )

    def _get_alt_wpk_handle(self, alt_dir: str, pkg_id: int) -> BinaryIO | None:
        key = (alt_dir, pkg_id)
        if key in self._alt_handle_cache:
            return self._alt_handle_cache[key]
        handle = None
        candidates = [
            os.path.join(alt_dir, f"{self.base_stem}{pkg_id}.wpk"),
            os.path.join(alt_dir, f"{self.base_stem}_{pkg_id}.wpk"),
        ]
        try:
            for entry in os.listdir(alt_dir):
                low = entry.lower()
                if low.endswith(".wpk") and low.startswith(self.base_stem.lower()) and str(pkg_id) in low:
                    candidates.append(os.path.join(alt_dir, entry))
        except OSError:
            pass
        for candidate in candidates:
            if os.path.exists(candidate):
                try:
                    handle = open(candidate, "rb")
                except OSError:
                    handle = None
                break
        self._alt_handle_cache[key] = handle
        return handle

    def get_load_error_summary(self) -> dict:
        return {
            "missing_entry_count": self._missing_entry_count,
            "partial_entry_count": self._partial_entry_count,
            "other_error_count": self._other_error_count,
        }

    # ------------------------------------------------------------------
    # Delegated IDX parsing
    # ------------------------------------------------------------------
    def _read_idx_header(self, file) -> None:
        read_idx_header(self, file)

    def _read_idx_indices(self, file) -> None:
        read_idx_indices(self, file)

    def _log_idx_split_summary(self) -> None:
        log_idx_split_summary(self)

    # ------------------------------------------------------------------
    # Delegated path + slot_file helpers
    # ------------------------------------------------------------------
    def _iter_wpk_path_candidates(self, pkg_id: int):
        yield from self._path_resolver.iter_wpk_path_candidates(pkg_id)

    def _find_wpk_path(self, pkg_id: int) -> str:
        return self._path_resolver.find_wpk_path(pkg_id)

    def _is_slot_file_pkg(self, pkg_id: int) -> bool:
        return self._path_resolver.is_slot_file_pkg(pkg_id)

    def _get_slot_file_dir(self, base_dir: str | None = None):
        return self._path_resolver.get_slot_file_dir(base_dir)

    def _read_slot_file_data(self, entry: NPKEntry) -> bytes | None:
        return self._slot_file_resolver.read_slot_file_data(entry)

    def _get_wpk_handle(self, pkg_id: int) -> BinaryIO | None:
        return self._path_resolver.get_wpk_handle(pkg_id)

    # ------------------------------------------------------------------
    # Delegated payload processing
    # ------------------------------------------------------------------
    def _maybe_unpack_dtsz(self, data: bytes, *, context: str) -> tuple[bytes, bool]:
        return self._payload_processor.maybe_unpack_dtsz(data, context=context)

    def _maybe_strip_enon_header(
        self, data: bytes, *, context: str
    ) -> tuple[bytes, bool]:
        return self._payload_processor.maybe_strip_enon_header(data, context=context)

    def _unwrap_nested_payloads(self, entry: NPKEntry, *, context: str) -> None:
        self._payload_processor.unwrap_nested_payloads(entry, context=context)

    def _maybe_unpack_cobl(self, data: bytes, *, context: str) -> tuple[bytes, bool]:
        return self._payload_processor.maybe_unpack_cobl(data, context=context)

    def _decode_cobl_concat(self, data: bytes, *, context: str) -> bytes:
        return self._payload_processor.decode_cobl_concat(data, context=context)

    def _decode_cobl_block(self, data: bytes, *, context: str) -> bytes:
        return self._payload_processor.decode_cobl_block(data, context=context)

    def _deobfuscate_cobl_probe_region(self, data: bytes) -> tuple[bytes, int]:
        return self._payload_processor.deobfuscate_cobl_probe_region(data)

    def _score_slot_stage1_candidate(self, data: bytes) -> tuple[int, str]:
        return self._payload_processor.score_slot_stage1_candidate(data)

    def _decode_slot_payload_auto(
        self, payload: bytes, *, context: str
    ) -> tuple[bytes, bool, int | None, bool]:
        return self._payload_processor.decode_slot_payload_auto(
            payload, context=context
        )

    # ------------------------------------------------------------------
    # Delegated standalone WPK parsing
    # ------------------------------------------------------------------
    def _read_wpk_header(self, file) -> None:
        read_wpk_header(self, file)

    def _scan_wpk_indices(self, file) -> None:
        scan_wpk_indices(self, file)

    def _build_index_from_embedded_header(
        self, data: bytes, off: int, next_off: int, ordinal: int
    ) -> NPKIndex:
        return build_index_from_embedded_header(data, off, next_off, ordinal)

    # ------------------------------------------------------------------
    # Unified entry API
    # ------------------------------------------------------------------
    def is_entry_loaded(self, index: int) -> bool:
        return index in self.entries

    def find_entry_by_name(self, path: str) -> tuple[NPKEntry, int] | tuple[None, None]:
        path = path.replace("/", "\\").split(".", 1)[0]
        for ind in range(0, len(self.entries)):
            if path in self.entries[ind].basename:
                return (self.entries[ind], ind)
        return (None, None)

    def find_entry_by_id(self, index: int) -> NPKEntry:
        """Get an entry by its index.

        Assumes all the indexes have been read beforehand (by load_entry)

        Args:
            index: The index of the entry to load

        Returns:
            NPKEntry: The loaded entry
        """
        if 0 <= index < len(self.indices):
            return self.entries[index]

        entry = NPKEntry()
        get_logger().critical("Entry index out of range: %d", index)
        entry.data_flags |= NPKEntryDataFlags.ERROR
        return entry

    def load_entry(self, index: int):
        """Load an entry into the index through the BufferedReader

        Called by main_window.py, optimized to avoid opening
        and closing the data file thousands of times

        Args:
            index: The index of the entry to load
        """
        # Create a new entry based on the index
        entry = NPKEntry()
        idx = self.indices[index]

        entry.is_slot_file = False
        entry.source_mode = self.mode
        entry.stage1_decoded = False
        entry.stage1_tag = None

        # Copy index attributes to entry
        for attr in vars(idx):
            setattr(entry, attr, getattr(idx, attr))

        try:
            self._load_entry_data(entry)

        except MissingIndexedResourceError as exc:
            msg = str(exc)

            entry.data = b""
            entry.extension = "bin"
            entry.category = get_file_category(entry.extension)
            entry.data_flags |= NPKEntryDataFlags.ERROR
            entry.format_metadata["load_error"] = msg
            entry.format_metadata["missing_reason"] = "deleted_or_in_other_pack"
            entry.state = State.CACHED

            self._missing_entry_count += 1
            self.entries[index] = entry
            return entry

        except EOFError as exc:
            msg = f"{entry.filename}: resource data is incomplete, possibly due to a corrupted pack or mismatched index"

            entry.data = b""
            entry.extension = "bin"
            entry.category = get_file_category(entry.extension)
            entry.data_flags |= NPKEntryDataFlags.ERROR
            entry.format_metadata["load_error"] = msg
            entry.format_metadata["missing_reason"] = "partial_or_mismatched"
            entry.state = State.CACHED

            self._partial_entry_count += 1
            self.entries[index] = entry
            return entry

        except FileNotFoundError:
            msg = f"{entry.filename}: resource is missing or located in a different resource pack"

            entry.data = b""
            entry.extension = "bin"
            entry.category = get_file_category(entry.extension)
            entry.data_flags |= NPKEntryDataFlags.ERROR
            entry.format_metadata["load_error"] = msg
            entry.format_metadata["missing_reason"] = "deleted_or_in_other_pack"
            entry.state = State.CACHED

            self._missing_entry_count += 1
            self.entries[index] = entry
            return entry

        except Exception as exc:
            get_logger().exception("Failed to load IDX/WPK entry %d: %s", index, exc)
            entry.data = b""
            entry.extension = "bin"
            entry.category = get_file_category(entry.extension)
            entry.data_flags |= NPKEntryDataFlags.ERROR
            entry.state = State.CACHED
            self._other_error_count += 1
            self.entries[index] = entry
            return entry

        if "." not in entry.filename:
            entry.filename = f"{entry.filename}.{entry.extension}"

        entry.state = State.CACHED
        self.entries[index] = entry
        return entry

    def _load_entry_data(self, entry: NPKEntry):
        pkg_id = getattr(entry, "pkg_id", None)
        if pkg_id is None:
            raise ValueError("Entry missing pkg_id")

        raw_data: bytes | None
        payload: bytes

        alt_dir = getattr(entry, "_alt_dir", None)
        if self.mode == "idx" and self._is_slot_file_pkg(pkg_id):
            raw_data = self._read_slot_file_data(entry)
            if raw_data is None:
                raise FileNotFoundError(
                    f"Missing slot_file for pkg_id={pkg_id} in {self._get_slot_file_dir()}"
                )
            entry.is_slot_file = True
            entry.source_mode = "slot_file"
            entry.file_length = len(raw_data)
            entry.file_original_length = len(raw_data)
            hdr_size = getattr(entry, "hdr_size", 0)
            payload_size = getattr(entry, "payload_size", 0)
            if raw_data[:4] == b"1DPW":
                payload = (
                    raw_data[hdr_size : hdr_size + payload_size]
                    if payload_size > 0
                    else raw_data[hdr_size:]
                )
            else:
                payload = raw_data
        else:
            handle = (
                self._get_alt_wpk_handle(alt_dir, pkg_id)
                if alt_dir
                else self._get_wpk_handle(pkg_id)
            )
            if handle is None:
                raise FileNotFoundError(
                    f"Missing WPK for pkg_id={pkg_id} in {alt_dir or self.base_dir}"
                )

            hdr_size = getattr(entry, "hdr_size", 0)
            payload_size = getattr(entry, "payload_size", 0)
            total_size = (
                entry.file_length if entry.file_length > 0 else hdr_size + payload_size
            )

            handle.seek(entry.file_offset)
            raw_data = handle.read(total_size)

            if len(raw_data) == 0:
                raise MissingIndexedResourceError(
                    f"{entry.filename}: resource is missing or located in a different resource pack"
                )

            if len(raw_data) != total_size:
                raise EOFError(
                    f"Failed to read entry data: expected {total_size}, got {len(raw_data)}"
                )

            if hdr_size > 0 and hdr_size <= len(raw_data):
                payload = (
                    raw_data[hdr_size : hdr_size + payload_size]
                    if payload_size > 0
                    else raw_data[hdr_size:]
                )
            else:
                payload = raw_data

        entry.raw_data = raw_data
        entry.payload_data = payload

        stage1_context = f"{entry.filename} pkg={pkg_id} source={entry.source_mode}"
        used_skip_header_decode = False
        if entry.is_slot_file:
            processed, decoded, tag, used_skip_header_decode = (
                self._decode_slot_payload_auto(
                    payload,
                    context=stage1_context,
                )
            )
        else:
            processed, decoded, tag = try_decode_payload_stage1(
                payload,
                context=stage1_context,
                skip_header_decode=False,
            )

        entry.stage1_decoded = decoded
        entry.stage1_tag = tag
        entry.stage1_skip_header_decode = used_skip_header_decode

        if decoded:
            entry.data = processed
            get_logger().debug(
                "WPD1 stage1 decoded: %s pkg=%s source=%s slot=%s skip_header_decode=%s tag=0x%04X in=%d out=%d",
                entry.filename,
                pkg_id,
                entry.source_mode,
                entry.is_slot_file,
                used_skip_header_decode,
                tag if tag is not None else 0,
                len(payload),
                len(processed),
            )
        else:
            entry.data = payload
            get_logger().debug(
                "WPD1 stage1 fallback raw: %s pkg=%s source=%s len=%d",
                entry.filename,
                pkg_id,
                entry.source_mode,
                len(payload),
            )

        entry.none_header_stripped = False
        entry.dtsz_unpacked = False
        entry.enon_header_stripped = False
        entry.cobl_unpacked = False
        entry.unwrap_layers = []
        self._unwrap_nested_payloads(
            entry,
            context=f"{entry.filename} pkg={pkg_id} source={entry.source_mode}",
        )

        entry.file_length = len(entry.data)
        entry.file_original_length = len(entry.data)

        if not is_binary(entry.data):
            entry.data_flags |= NPKEntryDataFlags.TEXT

        entry.source_extension = get_ext(entry.data, entry.data_flags)

        processed = process_entry_with_processors(entry)
        if processed and not is_binary(entry.data):
            entry.data_flags |= NPKEntryDataFlags.TEXT

        entry.extension = entry.extension or get_ext(entry.data, entry.data_flags)
        entry.category = get_file_category(entry.extension)

        get_logger().debug(
            "Loaded IDX/WPK entry: %s pkg=%s off=0x%X len=%d ext=%s mode=%s source=%s slot=%s stage1=%s unwrap=%s",
            entry.filename,
            pkg_id,
            entry.file_offset,
            len(entry.data),
            entry.extension,
            self.mode,
            entry.source_mode,
            entry.is_slot_file,
            entry.stage1_decoded,
            ",".join(entry.unwrap_layers) if entry.unwrap_layers else "-",
        )
