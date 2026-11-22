"""NPK File Reader"""

import io
import os
from typing import Dict, List, Tuple

from arc4 import ARC4

from core.binary_readers import read_uint16, read_uint32, read_uint64
from core.formats import process_entry_with_processors
from core.logger import get_logger
from core.npk.decompression import (
    check_lz4_like,
    check_nxs3,
    check_rotor,
    decompress_entry,
    strip_none_wrapper,
    unpack_lz4_like,
    unpack_nxs3,
    unpack_rotor,
)
from core.npk.decryption import decrypt_entry
from core.npk.enums import NPKFileType

from .class_types import (
    CompressionType,
    DecryptionType,
    NPKEntry,
    NPKEntryDataFlags,
    NPKIndex,
    NPKReadOptions,
    State,
)
from .decryption import decrypt_eggparty_index
from .detection import get_ext, get_file_category, is_binary
from .expkkeys import EXPKKeyGenerator


class NPKFile:
    """Main class for handling NPK files."""

    def __init__(self, file_path: str, options: NPKReadOptions | None = None):
        """Initialize the NPK file handler.

        Args:
            file_path: Optional path to an NPK file to open
        """
        self.file_path = file_path
        self.entries: Dict[int, NPKEntry] = {}
        self.indices: List[NPKIndex] = []

        # NPK header information
        self.file_count: int = 0
        self.index_offset: int = 0
        self.hash_mode: int = 0
        self.encrypt_mode: int = 0
        self.info_size: int = 0

        # Options when reading the NPK file
        self.options = options if options is not None else NPKReadOptions()

        # NXFN file information
        self.nxfn_files = None

        # Key generator for advanced decryption
        self.key_generator = None

        get_logger().info("Opening NPK file: %s", self.file_path)

        with open(self.file_path, "rb") as file:
            # Read NPK header.
            self._read_header(file)

            # Read the indices but don't load data yet
            self._read_indices(file)

    def _read_header(self, file: io.BufferedReader) -> bool:
        """Open an NPK file and read its header information.

        Args:
            file_path: Path to the NPK file

        Returns:
            bool: True if the file was successfully opened
        """

        get_logger().debug("Reading NPK header from %s", self.file_path)

        # Read NPK header
        magic = file.read(4)
        if magic == b"NXPK":
            self.file_type = NPKFileType.NXPK
        elif magic == b"EXPK":
            self.file_type = NPKFileType.EXPK
        else:
            file.close()
            raise ValueError(f"Not a valid NPK file: {self.file_path}")

        get_logger().debug("NPK type: %s", self.file_type)

        # Read basic header info
        self.file_count = read_uint32(file)
        var1 = read_uint32(file)  # Unknown variable
        self.encrypt_mode = read_uint32(file)
        self.hash_mode = read_uint32(file)
        self.index_offset = read_uint32(file)

        get_logger().info("NPK entry count: %d", self.file_count)
        get_logger().info("NPK unknown var: %d", var1)
        get_logger().info("NPK encryption mode: %s", self.encrypt_mode)
        get_logger().info("NPK hash mode: %s", self.hash_mode)
        get_logger().info("NPK index offset: 0x%X", self.index_offset)

        # Determine index entry size
        self.info_size = (
            self._determine_info_size(file)
            if self.options.info_size is None
            else self.options.info_size
        )

        get_logger().debug("NPK index entry size: %d", self.info_size)

        if self.hash_mode == 2:
            file.seek(self.index_offset + (self.file_count * self.info_size))
            self.nxfn_files = [x for x in (file.read()).split(b"\x00") if x != b""]
        elif self.hash_mode == 3:
            self.arc_key = ARC4(b"61ea476e-8201-11e5-864b-fcaa147137b7")
        elif self.encrypt_mode == 256:
            file.seek(self.index_offset + (self.file_count * self.info_size) + 16)
            self.nxfn_files = [x for x in (file.read()).split(b"\x00") if x != b""]

        return True

    def __enter__(self):
        return self

    def _determine_info_size(self, file: io.BufferedReader) -> int:
        """Determine the size of each index entry."""
        if self.encrypt_mode == 256 or self.hash_mode == 2:
            return 0x1C  # 28 bytes

        current_pos = file.tell()
        file.seek(self.index_offset)
        buf = file.read()
        file.seek(current_pos)

        # The total size of the index divided by number of files gives us the entry size
        return len(buf) // self.file_count

    def _read_indices(self, file: io.BufferedReader) -> None:
        """Read all the index entries from the NPK file."""
        self.indices = []

        file.seek(self.index_offset)
        index_data = file.read(self.file_count * self.info_size)

        if self.file_type == NPKFileType.EXPK:
            self.key_generator = EXPKKeyGenerator()
            index_data = self.key_generator.decrypt(index_data)
        if self.hash_mode == 3:
            index_data = self.arc_key.decrypt(index_data)
        if self.encrypt_mode == 3:
            index_data = decrypt_eggparty_index(index_data)

        with io.BytesIO(index_data) as buf:
            for i in range(self.file_count):
                index = NPKIndex()

                # Read the file signature
                if self.info_size == 28:
                    # 32-bit file signature
                    index.file_signature = read_uint32(buf)
                elif self.info_size == 32:
                    # 64-bit file signature (NeoX 2.0)
                    index.file_signature = read_uint64(buf)

                # Read the rest of the index entry
                index.file_offset = read_uint32(buf)
                index.file_length = read_uint32(buf)
                index.file_original_length = read_uint32(buf)
                index.zcrc = read_uint32(buf)
                index.crc = read_uint32(buf)

                zip_flag = read_uint16(buf)
                if zip_flag == 5:
                    # Still LZ4
                    zip_flag = 2
                index.zip_flag = CompressionType(zip_flag)

                encrypt_flag = read_uint16(buf)
                if encrypt_flag == 3:
                    # Still Advanced XOR
                    encrypt_flag = 2

                index.encrypt_flag = DecryptionType(encrypt_flag)

                # Store file structure name if available
                if self.nxfn_files and i < len(self.nxfn_files):
                    index.file_structure = self.nxfn_files[i]
                else:
                    index.file_structure = None

                get_logger().debug("Index %d: %s", i, index)

                # Generate a filename
                if index.file_structure:
                    try:
                        index.filename = index.file_structure.decode("utf-8")
                    except UnicodeDecodeError:
                        index.filename = hex(index.file_signature)
                else:
                    index.filename = hex(index.file_signature)

                self.indices.append(index)

    def is_entry_loaded(self, index: int) -> bool:
        """Check if an entry is already loaded.

        Args:
            index: The index of the entry to check

        Returns:
            bool: True if the entry is loaded, False otherwise
        """
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

    def load_entry(self, index: int, f: io.BufferedReader):
        """Load an entry into the index through the BufferedReader

        Called by main_window.py, optimized to avoid opening
        and closing the data file thousands of times

        Args:
            index: The index of the entry to load
        """
        # Create a new entry based on the index
        entry = NPKEntry()
        idx = self.indices[index]

        # Copy index attributes to entry
        for attr in vars(idx):
            setattr(entry, attr, getattr(idx, attr))

        # Load the actual data
        self._load_entry_data(entry, f)

        # Update filename with extension.
        # For NXFN-backed names, keep an existing extension, otherwise append the detected one.
        _base_name, existing_ext = os.path.splitext(entry.filename)
        if not existing_ext and entry.extension:
            entry.filename = f"{entry.filename}.{entry.extension}"

        # Store in the cache
        entry.state = State.CACHED
        self.entries[index] = entry

    def _load_entry_data(self, entry: NPKEntry, file: io.BufferedReader):
        """Load the data for an entry from the NPK file."""
        # Position file pointer to the file data
        file.seek(entry.file_offset)

        # Read the file data
        entry.data = file.read(entry.file_length)

        # Decrypt EXPK data if needed
        if self.file_type == NPKFileType.EXPK and self.key_generator is not None:
            entry.data = self.key_generator.decrypt(entry.data)

        # Normal flow: decrypt with Config key
        if entry.encrypt_flag != DecryptionType.NONE:
            entry.data = decrypt_entry(entry, self.options.decryption_key)

        # Decompress if needed
        if entry.zip_flag != CompressionType.NONE:
            try:
                entry.data = decompress_entry(entry)
            except Exception:
                if entry.encrypt_flag == DecryptionType.BASIC_XOR:
                    get_logger().error(
                        "Error decompressing the file, did you choose the correct key for this NPK?"
                    )
                    entry.data_flags |= NPKEntryDataFlags.ENCRYPTED
                else:
                    get_logger().critical(
                        "Error decompressing the file using %s compression, open a GitHub issue",
                        entry.zip_flag.get_name(entry.zip_flag),
                    )
                    entry.data_flags |= NPKEntryDataFlags.ERROR
                return

        # Continuously strip simple wrappers and unpack nested payloads.
        entry.unwrap_layers = []
        seen_signatures: set[tuple[int, bytes]] = set()
        max_layers = 32
        for _ in range(max_layers):
            signature = (len(entry.data), entry.data[:16])
            if signature in seen_signatures:
                break
            seen_signatures.add(signature)

            stripped = strip_none_wrapper(entry.data)
            if stripped != entry.data:
                entry.data = stripped
                entry.unwrap_layers.append("NONE")
                continue

            if check_lz4_like(entry.data):
                try:
                    unpacked = unpack_lz4_like(entry.data)
                except Exception:
                    break
                if unpacked and unpacked != entry.data:
                    entry.data = unpacked
                    entry.unwrap_layers.append("LZ4_LIKE")
                    continue

            if check_rotor(entry):
                entry.data_flags |= NPKEntryDataFlags.ROTOR_PACKED
                entry.data = unpack_rotor(entry.data)
                entry.unwrap_layers.append("ROTOR")
                continue

            if check_nxs3(entry):
                entry.data_flags |= NPKEntryDataFlags.NXS3_PACKED
                entry.data = unpack_nxs3(entry.data)
                entry.unwrap_layers.append("NXS3")
                continue

            break

        binary = is_binary(entry.data)
        if not binary:
            entry.data_flags |= NPKEntryDataFlags.TEXT

        processed = process_entry_with_processors(entry)
        if processed and not is_binary(entry.data):
            entry.data_flags |= NPKEntryDataFlags.TEXT

        entry.extension = get_ext(entry.data, entry.data_flags)
        entry.category = get_file_category(entry.extension)

        get_logger().debug("Entry %s: %s", entry.filename, entry.category)
