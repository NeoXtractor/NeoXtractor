"""Packed Vorbis codebook library (port of CodebookLibrary.cs / Tremor lowmem)."""

from __future__ import annotations

import os

from .bitio import (
    BitReader,
    BitWriter,
    InvalidId,
    ParseErrorStr,
    SizeMismatch,
    WemError,
)


PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CODEBOOK_FILE = os.path.join(
    PACKAGE_DIR, "data", "packed_codebooks_aoTuV_603.bin"
)


def ilog(v: int) -> int:
    """Port of CodebookLibrary.ILog: number of significant bits."""
    ret = 0
    while v != 0:
        ret += 1
        v >>= 1
    return ret


def book_maptype1_quantvals(entries: int, dimensions: int) -> int:
    """Port of CodebookLibrary.BookMaptype1Quantvals."""
    bits = ilog(entries)
    vals = entries >> (((bits - 1) * (dimensions - 1)) // dimensions)

    while True:
        acc = 1
        acc1 = 1
        for _ in range(dimensions):
            acc *= vals
            acc1 *= vals + 1
        if acc <= entries and acc1 > entries:
            return vals
        if acc > entries:
            vals -= 1
        else:
            vals += 1


class CodebookLibrary:
    """Port of CodebookLibrary; loads the packed codebook database."""

    def __init__(self, filename: str | None = None):
        self._codebook_data: bytes | None = None
        self._codebook_offsets: list[int] | None = None
        self._codebook_count = 0

        if filename is None:
            return

        try:
            with open(filename, "rb") as f:
                data = f.read()
        except OSError as exc:
            raise WemError("Error opening %s" % filename) from exc

        file_size = len(data)
        if file_size < 8:
            raise ParseErrorStr("codebook library truncated")

        offset_offset = int.from_bytes(data[file_size - 4:file_size], "little")
        if offset_offset > file_size:
            raise ParseErrorStr("codebook library truncated")
        self._codebook_count = (file_size - offset_offset) // 4

        self._codebook_data = data[:offset_offset]
        self._codebook_offsets = [
            int.from_bytes(data[offset_offset + i * 4:offset_offset + i * 4 + 4], "little")
            for i in range(self._codebook_count)
        ]

    def get_codebook(self, i: int) -> tuple[bytes, int] | None:
        """Port of CodebookLibrary.GetCodebook; returns (data, size)."""
        if self._codebook_data is None or self._codebook_offsets is None:
            raise ParseErrorStr("codebook library not loaded")
        if i >= self._codebook_count - 1 or i < 0:
            return None
        start = self._codebook_offsets[i]
        size = self._codebook_offsets[i + 1] - start
        return self._codebook_data[start:start + size], size

    def rebuild(self, i: int, bos: BitWriter) -> None:
        """Port of CodebookLibrary.Rebuild(int, BitWriter)."""
        cb = self.get_codebook(i)
        if cb is None:
            raise InvalidId(i)
        data, size = cb
        bis = BitReader(_BytesStream(data))
        self._rebuild_stream(bis, size, bos)

    def copy(self, bis: BitReader, bos: BitWriter) -> None:
        """Port of CodebookLibrary.Copy: copy a full inline codebook."""
        identifier = bis.read(24)
        dimensions = bis.read(16)
        entries = bis.read(24)

        if identifier != 0x564342:
            raise ParseErrorStr("invalid codebook identifier")

        bos.write(identifier, 24)
        bos.write(dimensions, 16)
        bos.write(entries, 24)

        ordered = bis.read(1)
        bos.write(ordered, 1)
        if ordered != 0:
            initial_length = bis.read(5)
            bos.write(initial_length, 5)

            current_entry = 0
            while current_entry < entries:
                number_bits = ilog(entries - current_entry)
                number = bis.read(number_bits)
                bos.write(number, number_bits)
                current_entry += number
            if current_entry > entries:
                raise ParseErrorStr("current_entry out of range")
        else:
            sparse = bis.read(1)
            bos.write(sparse, 1)

            for _ in range(entries):
                present_bool = True
                if sparse != 0:
                    present = bis.read(1)
                    bos.write(present, 1)
                    present_bool = present != 0
                if present_bool:
                    codeword_length = bis.read(5)
                    bos.write(codeword_length, 5)

        lookup_type = bis.read(4)
        bos.write(lookup_type, 4)

        if lookup_type == 0:
            pass
        elif lookup_type == 1:
            minimum = bis.read(32)
            maximum = bis.read(32)
            value_length = bis.read(4)
            sequence_flag = bis.read(1)
            bos.write(minimum, 32)
            bos.write(maximum, 32)
            bos.write(value_length, 4)
            bos.write(sequence_flag, 1)

            quantvals = book_maptype1_quantvals(entries, dimensions)
            for _ in range(quantvals):
                val = bis.read(value_length + 1)
                bos.write(val, value_length + 1)
        elif lookup_type == 2:
            raise ParseErrorStr("didn't expect lookup type 2")
        else:
            raise ParseErrorStr("invalid lookup type")

    def _rebuild_stream(self, bis: BitReader, cb_size: int, bos: BitWriter) -> None:
        """Port of CodebookLibrary.Rebuild(BitReader, ulong, BitWriter)."""
        dimensions = bis.read(4)
        entries = bis.read(14)

        bos.write(0x564342, 24)
        bos.write(dimensions, 16)
        bos.write(entries, 24)

        ordered = bis.read(1)
        bos.write(ordered, 1)
        if ordered != 0:
            initial_length = bis.read(5)
            bos.write(initial_length, 5)

            current_entry = 0
            while current_entry < entries:
                number_bits = ilog(entries - current_entry)
                number = bis.read(number_bits)
                bos.write(number, number_bits)
                current_entry += number
            if current_entry > entries:
                raise ParseErrorStr("current_entry out of range")
        else:
            codeword_length_length = bis.read(3)
            sparse = bis.read(1)

            if codeword_length_length == 0 or codeword_length_length > 5:
                raise ParseErrorStr("nonsense codeword length")

            bos.write(sparse, 1)

            for _ in range(entries):
                present_bool = True
                if sparse != 0:
                    present = bis.read(1)
                    bos.write(present, 1)
                    present_bool = present != 0
                if present_bool:
                    codeword_length = bis.read(codeword_length_length)
                    bos.write(codeword_length, 5)

        lookup_type = bis.read(1)
        bos.write(lookup_type, 4)

        if lookup_type == 0:
            pass
        elif lookup_type == 1:
            minimum = bis.read(32)
            maximum = bis.read(32)
            value_length = bis.read(4)
            sequence_flag = bis.read(1)
            bos.write(minimum, 32)
            bos.write(maximum, 32)
            bos.write(value_length, 4)
            bos.write(sequence_flag, 1)

            quantvals = book_maptype1_quantvals(entries, dimensions)
            for _ in range(quantvals):
                val = bis.read(value_length + 1)
                bos.write(val, value_length + 1)
        elif lookup_type == 2:
            raise ParseErrorStr("didn't expect lookup type 2")
        else:
            raise ParseErrorStr("invalid lookup type")

        # check that we used exactly all bytes
        if cb_size != 0 and bis.total_bits_read // 8 + 1 != cb_size:
            raise SizeMismatch(cb_size, bis.total_bits_read // 8 + 1)


class _BytesStream:
    """Tiny adapter so BitReader can consume an in-memory codebook."""

    __slots__ = ("_data", "_pos", "_len")

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0
        self._len = len(data)

    def read_byte(self) -> int:
        if self._pos >= self._len:
            return -1
        value = self._data[self._pos]
        self._pos += 1
        return value
