"""NPK entry type definitions for the NPK file format."""

import os
from dataclasses import dataclass
from enum import IntFlag, auto

from neoxtractor.core.file import IFile

from .enums import CompressionType, DecryptionType, NPKEntryFileCategories


class State(IntFlag):
    UNLOADED = 0
    CACHED = auto()
    PRIMARY_LOAD = auto()
    SECONDARY_LOAD = auto()


class NPKEntryDataFlags(IntFlag):
    """Flags for NPK entry data."""

    NONE = 0
    TEXT = auto()
    NXS3_PACKED = auto()
    ROTOR_PACKED = auto()
    ENCRYPTED = auto()
    ERROR = auto()


@dataclass
class NPKReadOptions:
    """Options for reading NPK files."""

    decryption_key: int | None = None
    aes_key: bytes | None = None
    info_size: int | None = None


@dataclass
class WPKIndexExtra:
    pkg_id: int = 0
    hdr_size: int = 0
    payload_size: int = 0
    raw_hash_hex: str = ""


@dataclass
class NPKIndex(WPKIndexExtra):
    """Represents an index entry in an NPK file."""

    filename = ""

    file_signature: int = 0
    file_offset: int = 0
    file_length: int = 0
    file_original_length: int = 0
    zcrc: int = 0  # compressed CRC
    crc: int = 0  # decompressed CRC
    file_structure: bytes | None = None
    zip_flag: CompressionType = CompressionType.NONE
    encrypt_flag: DecryptionType = DecryptionType.NONE

    data_flags = NPKEntryDataFlags.NONE

    def __repr__(self) -> str:
        return (
            f"NPKIndex(offset=0x{self.file_offset:X}, "
            f"length={self.file_length}, "
            f"orig_length={self.file_original_length}, "
            f"compression={CompressionType.get_name(self.zip_flag)}, "
            f"encryption={DecryptionType.get_name(self.encrypt_flag)})"
        )


@dataclass
class WPKExtra:
    """Extra data we appoint to an WPK file when decompressing it."""

    none_header_stripped: bool
    enon_header_stripped: bool
    is_slot_file: bool
    source_mode: str
    stage1_decoded: bool
    stage1_tag: int | None
    raw_data: bytes
    payload_data: bytes
    dtsz_unpacked: bool
    cobl_unpacked: bool
    unwrap_layers: list[str]
    stage1_skip_header_decode: bool


class NPKEntry(NPKIndex, IFile, WPKExtra):
    """Represents a file entry in an NPK file, including the actual file data."""

    def __init__(self):
        super().__init__()
        self._data: bytes = b""
        self._extension: str | None = None
        self.category: NPKEntryFileCategories = NPKEntryFileCategories.OTHER
        self.source_data: bytes | None = None
        self.source_extension: str = ""
        self.processed_by: str | None = None
        self.has_decoded_view: bool = False
        self.format_metadata: dict = {}
        self.state: State = State.UNLOADED

    @property
    def is_compressed(self) -> bool:
        """Check if the entry is compressed."""
        return self.zip_flag != 0

    @property
    def is_encrypted(self) -> bool:
        """Check if the entry is encrypted."""
        return self.encrypt_flag != 0

    def save_to_file(self, path: str, decoded: bool = True) -> None:
        """Save the file data to the specified path."""
        dir_name = os.path.dirname(path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        with open(path, "wb") as f:
            f.write(self.get_export_data(decoded=decoded))

    def get_export_data(self, decoded: bool = True) -> bytes:
        """Return decoded data by default, or the original source bytes when available."""
        if decoded or not self.has_decoded_view or self.source_data is None:
            return self.data
        return self.source_data

    def get_export_filename(self, decoded: bool = True) -> str:
        """Return a filename that matches the chosen export payload."""
        filename = self.filename
        base_name, existing_ext = os.path.splitext(filename)

        if decoded:
            if self.has_decoded_view and self.extension:
                return (
                    f"{base_name}.{self.extension}"
                    if existing_ext
                    else f"{filename}.{self.extension}"
                )
            return filename

        if not self.has_decoded_view:
            return filename

        source_ext = self.source_extension or self.extension
        if existing_ext:
            return f"{base_name}.{source_ext}" if source_ext else filename
        return f"{filename}.{source_ext}" if source_ext else filename

    def __repr__(self) -> str:
        return (
            f"NPKEntry(filename='{self.filename}', "
            f"length={self.file_length}, "
            f"compression={CompressionType.get_name(self.zip_flag)}, "
            f"encryption={DecryptionType.get_name(self.encrypt_flag)})"
        )

    # IFile implementations
    @property
    def name(self) -> str:
        return self.filename

    @property
    def data(self) -> bytes:
        return self._data

    @data.setter
    def data(self, value: bytes) -> None:
        self._data = value

    @property
    def extension(self) -> str:
        return self._extension if self._extension else super().extension

    @extension.setter
    def extension(self, value: str) -> None:
        self._extension = value
