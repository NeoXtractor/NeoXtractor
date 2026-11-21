"""Bit-level I/O and Ogg page framing.

Port of BitIo.cs (BinIo / BitReader / BitWriter) and Crc32.cs from ww2ogg.
The Ogg CRC uses the non-reflected polynomial 0x04C11DB7 with zero init and
xorout, so zlib.crc32 cannot be used here.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Errors (port of Errors.cs). All derive from ValueError so the public API
# can honour the "raise ValueError" contract.
# ---------------------------------------------------------------------------
class WemError(ValueError):
    """Base class for all WEM conversion errors."""


class ParseErrorStr(WemError):
    def __init__(self, message: str):
        super().__init__("Parse error: " + message)


class SizeMismatch(WemError):
    def __init__(self, real_size: int, read_size: int):
        super().__init__(
            "Parse error: expected %d bits, read %d" % (real_size, read_size)
        )


class InvalidId(WemError):
    def __init__(self, codebook_id: int):
        self.codebook_id = codebook_id
        super().__init__(
            "Parse error: invalid codebook id %d, try --inline-codebooks"
            % codebook_id
        )


# ---------------------------------------------------------------------------
# Ogg framing CRC (port of crc.c from Tremor lowmem)
# ---------------------------------------------------------------------------
def _build_crc_table() -> tuple:
    table = []
    for i in range(256):
        reg = i << 24
        for _ in range(8):
            if reg & 0x80000000:
                reg = ((reg << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
            else:
                reg = (reg << 1) & 0xFFFFFFFF
        table.append(reg)
    return tuple(table)


CRC_TABLE = _build_crc_table()


def crc32(data, length: int = -1) -> int:
    """Ogg framing checksum (non-reflected 0x04C11DB7)."""
    if length is None or length < 0:
        length = len(data)
    reg = 0
    table = CRC_TABLE
    for i in range(length):
        reg = ((reg << 8) ^ table[((reg >> 24) & 0xFF) ^ data[i]]) & 0xFFFFFFFF
    return reg


class Crc32:
    """Port of Crc32.Checksum."""

    @staticmethod
    def checksum(data, bytes: int) -> int:
        return crc32(data, bytes)


# ---------------------------------------------------------------------------
# Byte stream helpers (port of BinIo, on an in-memory bytes buffer)
# ---------------------------------------------------------------------------
class ByteStream:
    """Minimal seekable byte stream with endian-explicit integer reads."""

    def __init__(self, data: bytes):
        self.data = data
        self.position = 0
        self.length = len(data)

    def seek(self, offset: int) -> None:
        self.position = offset

    def tell(self) -> int:
        return self.position

    def read_byte(self) -> int:
        if self.position >= self.length:
            return -1
        value = self.data[self.position]
        self.position += 1
        return value

    def read_exactly(self, count: int) -> bytes:
        start = self.position
        end = start + count
        if end > self.length:
            raise ParseErrorStr("file truncated")
        self.position = end
        return self.data[start:end]

    def read_fourcc(self) -> bytes:
        return self.read_exactly(4)

    def read16(self, little_endian: bool) -> int:
        raw = self.read_exactly(2)
        return int.from_bytes(raw, "little" if little_endian else "big")

    def read32(self, little_endian: bool) -> int:
        raw = self.read_exactly(4)
        return int.from_bytes(raw, "little" if little_endian else "big")


def fourcc_equals(buf: bytes, name: str) -> bool:
    return (
        buf[0] == ord(name[0])
        and buf[1] == ord(name[1])
        and buf[2] == ord(name[2])
        and buf[3] == ord(name[3])
    )


# ---------------------------------------------------------------------------
# BitReader (port of Bit_stream, LSB-first output / MSB-first input)
# ---------------------------------------------------------------------------
class OutOfBits(Exception):
    pass


class BitReader:
    def __init__(self, stream: ByteStream):
        self._s = stream
        self._bit_buffer = 0
        self._bits_left = 0
        self.total_bits_read = 0

    def get_bit(self) -> bool:
        if self._bits_left == 0:
            c = self._s.read_byte()
            if c < 0:
                raise OutOfBits()
            self._bit_buffer = c
            self._bits_left = 8
        self.total_bits_read += 1
        self._bits_left -= 1
        return (self._bit_buffer & (0x80 >> self._bits_left)) != 0

    def read(self, bit_size: int) -> int:
        if bit_size > 32:
            raise ValueError("too many bits")
        total = 0
        for i in range(bit_size):
            if self.get_bit():
                total |= 1 << i
        return total


# ---------------------------------------------------------------------------
# BitWriter (port of Bit_oggstream, LSB-first, one packet per page)
# ---------------------------------------------------------------------------
HEADER_BYTES = 27
MAX_SEGMENTS = 255
SEGMENT_SIZE = 255
MAX_PAYLOAD = SEGMENT_SIZE * MAX_SEGMENTS


class BitWriter:
    def __init__(self, out: bytearray):
        self._out = out
        self._bit_buffer = 0
        self._bits_stored = 0

        self._payload = bytearray()
        self._first = True
        self._continued = False
        self._granule = 0
        self._seqno = 0
        self._page = bytearray(HEADER_BYTES + MAX_SEGMENTS + MAX_PAYLOAD)

    # -- bit output -----------------------------------------------------
    def put_bit(self, bit: bool) -> None:
        if bit:
            self._bit_buffer |= 1 << self._bits_stored
        self._bits_stored += 1
        if self._bits_stored == 8:
            self.flush_bits()

    def write(self, value: int, n: int) -> None:
        if n > 32:
            raise ParseErrorStr("too many bits")
        if n > 0 and (value >> (n - 1)) > 1:
            raise ParseErrorStr("integer too big")

        # Fast path for byte-aligned single bytes (used by packet copying).
        if n == 8 and self._bits_stored == 0:
            if len(self._payload) == MAX_PAYLOAD:
                raise ParseErrorStr("ran out of space in an Ogg packet")
            self._payload.append(value & 0xFF)
            return

        for i in range(n):
            self.put_bit((value & (1 << i)) != 0)

    def write_bytes(self, data: bytes) -> None:
        """Write whole bytes at the current bit position.

        Equivalent to ``write(b, 8)`` for every byte, but fast enough for
        ~1 MB payloads.
        """
        count = len(data)
        if count == 0:
            return
        if len(self._payload) + count > MAX_PAYLOAD:
            raise ParseErrorStr("ran out of space in an Ogg packet")

        if self._bits_stored == 0:
            self._payload.extend(data)
            return

        bits = self._bits_stored
        cur = self._bit_buffer
        append = self._payload.append
        # Every input byte emits exactly one output byte; `bits` remain buffered.
        for value in data:
            append((cur | (value << bits)) & 0xFF)
            cur = (value >> (8 - bits)) & 0xFF
        self._bit_buffer = cur

    def flush_bits(self) -> None:
        if self._bits_stored != 0:
            if len(self._payload) == MAX_PAYLOAD:
                raise ParseErrorStr("ran out of space in an Ogg packet")
            self._payload.append(self._bit_buffer)
            self._bits_stored = 0
            self._bit_buffer = 0

    # -- page output ----------------------------------------------------
    def set_granule(self, granule: int) -> None:
        self._granule = granule

    def flush_page(self, next_continued: bool = False, last: bool = False) -> None:
        payload = self._payload
        if len(payload) != MAX_PAYLOAD:
            self.flush_bits()

        if not payload:
            return

        payload_size = len(payload)
        segments = (payload_size + SEGMENT_SIZE) // SEGMENT_SIZE  # round up
        if segments == MAX_SEGMENTS + 1:
            segments = MAX_SEGMENTS  # at max skip the final 0

        page = self._page
        page[0] = ord("O")
        page[1] = ord("g")
        page[2] = ord("g")
        page[3] = ord("S")
        page[4] = 0  # stream_structure_version
        page[5] = (
            (1 if self._continued else 0)
            | (2 if self._first else 0)
            | (4 if last else 0)
        )
        granule = self._granule
        low = granule & 0xFFFFFFFF
        if granule == 0xFFFFFFFF:
            high = 0xFFFFFFFF
        elif granule > 0xFFFFFFFF:
            high = (granule >> 32) & 0xFFFFFFFF
        else:
            high = 0
        page[6:10] = low.to_bytes(4, "little")
        page[10:14] = high.to_bytes(4, "little")
        page[14:18] = (1).to_bytes(4, "little")  # stream serial number
        page[18:22] = self._seqno.to_bytes(4, "little")
        page[22:26] = b"\x00\x00\x00\x00"  # checksum placeholder
        page[26] = segments

        # lacing values
        bytes_left = payload_size
        base = HEADER_BYTES
        for i in range(segments):
            if bytes_left >= SEGMENT_SIZE:
                bytes_left -= SEGMENT_SIZE
                page[base + i] = SEGMENT_SIZE
            else:
                page[base + i] = bytes_left
        base += segments

        page[base:base + payload_size] = payload

        page_length = HEADER_BYTES + segments + payload_size
        page[22:26] = crc32(page, page_length).to_bytes(4, "little")

        self._out.extend(page[:page_length])

        self._seqno += 1
        self._first = False
        self._continued = next_continued
        payload.clear()

    def finish(self) -> None:
        self.flush_page()
