"""Wwise Vorbis WEM -> Ogg Vorbis.

Port of WwiseRiffVorbis.cs. Wwise stores standard Vorbis audio packets with a
rewritten (stripped) setup header and, depending on the build, rearranged
packet fields. This module parses the RIFF/RIFX container, rebuilds the setup
header (inline or from the packed codebook database), restores the packet
headers and writes a standard Ogg Vorbis stream.
"""

from __future__ import annotations

from .bitio import (
    BitReader,
    BitWriter,
    ByteStream,
    InvalidId,
    ParseErrorStr,
    fourcc_equals,
)
from .codebooks import CodebookLibrary, DEFAULT_CODEBOOK_FILE, ilog

# ForcePacketFormat values (port of the C# enum)
FORCE_NONE = None
FORCE_MOD_PACKETS = "mod"
FORCE_NO_MOD_PACKETS = "no_mod"


class WwiseRiffVorbis:
    def __init__(
        self,
        input_data: bytes,
        name: str = "",
        codebook_file: str | None = None,
        inline_codebooks: bool = False,
        full_setup: bool = False,
        force_packet_format: str | None = None,
        shared_codebooks: CodebookLibrary | None = None,
    ):
        self.name = name
        self.codebook_file = codebook_file or DEFAULT_CODEBOOK_FILE
        self.shared_codebooks = shared_codebooks

        self._stream = ByteStream(input_data)
        self._little_endian = True

        self._riff_size = -1
        self._fmt_offset = -1
        self._cue_offset = -1
        self._list_offset = -1
        self._smpl_offset = -1
        self._vorb_offset = -1
        self._data_offset = -1
        self._fmt_size = -1
        self._cue_size = -1
        self._list_size = -1
        self._smpl_size = -1
        self._vorb_size = -1
        self._data_size = -1

        self.channels = 0
        self.sample_rate = 0
        self.avg_bytes_per_second = 0
        self.sample_count = 0

        self._ext_unk = 0
        self._subtype = 0
        self._cue_count = 0
        self._loop_count = 0
        self._loop_start = 0
        self._loop_end = 0

        self._setup_packet_offset = 0
        self._first_audio_packet_offset = 0
        self._uid = 0
        self._blocksize0_pow = 0
        self._blocksize1_pow = 0

        self.inline_codebooks = inline_codebooks
        self.full_setup = full_setup
        self.header_triad_present = False
        self.old_packet_headers = False
        self.no_granule = False
        self.mod_packets = False

        self._file_size = len(input_data)

        self._parse_header()

        if force_packet_format == FORCE_NO_MOD_PACKETS:
            self.mod_packets = False
        elif force_packet_format == FORCE_MOD_PACKETS:
            self.mod_packets = True

        # check/set loops now that we know total sample count
        if self._loop_count != 0:
            if self._loop_end == 0:
                self._loop_end = self.sample_count
            else:
                self._loop_end += 1
            if (
                self._loop_start >= self.sample_count
                or self._loop_end > self.sample_count
                or self._loop_start > self._loop_end
            ):
                raise ParseErrorStr("loops out of range")

    # ------------------------------------------------------------------
    # header parsing
    # ------------------------------------------------------------------
    def _read16(self) -> int:
        return self._stream.read16(self._little_endian)

    def _read32(self) -> int:
        return self._stream.read32(self._little_endian)

    def _parse_header(self) -> None:
        s = self._stream
        # check RIFF header
        s.seek(0)
        riff_head = s.read_fourcc()
        if fourcc_equals(riff_head, "RIFX"):
            self._little_endian = False
        elif fourcc_equals(riff_head, "RIFF"):
            self._little_endian = True
        else:
            raise ParseErrorStr("missing RIFF")

        self._riff_size = self._read32() + 8
        if self._riff_size > self._file_size:
            raise ParseErrorStr("RIFF truncated")

        wave_head = s.read_fourcc()
        if not fourcc_equals(wave_head, "WAVE"):
            raise ParseErrorStr("missing WAVE")

        # read chunks
        chunk_offset = 12
        while chunk_offset < self._riff_size:
            s.seek(chunk_offset)
            if chunk_offset + 8 > self._riff_size:
                raise ParseErrorStr("chunk header truncated")

            chunk_type = s.read_fourcc()
            chunk_size = self._read32()

            if fourcc_equals(chunk_type, "fmt "):
                self._fmt_offset = chunk_offset + 8
                self._fmt_size = chunk_size
            elif fourcc_equals(chunk_type, "cue "):
                self._cue_offset = chunk_offset + 8
                self._cue_size = chunk_size
            elif fourcc_equals(chunk_type, "LIST"):
                self._list_offset = chunk_offset + 8
                self._list_size = chunk_size
            elif fourcc_equals(chunk_type, "smpl"):
                self._smpl_offset = chunk_offset + 8
                self._smpl_size = chunk_size
            elif fourcc_equals(chunk_type, "vorb"):
                self._vorb_offset = chunk_offset + 8
                self._vorb_size = chunk_size
            elif fourcc_equals(chunk_type, "data"):
                self._data_offset = chunk_offset + 8
                self._data_size = chunk_size

            chunk_offset = chunk_offset + 8 + chunk_size

        if chunk_offset > self._riff_size:
            raise ParseErrorStr("chunk truncated")

        # check that we have the chunks we're expecting
        if self._fmt_offset == -1 or self._data_offset == -1:
            raise ParseErrorStr("expected fmt, data chunks")

        # read fmt
        if self._vorb_offset == -1 and self._fmt_size != 0x42:
            raise ParseErrorStr("expected 0x42 fmt if vorb missing")

        if self._vorb_offset != -1 and self._fmt_size not in (0x28, 0x18, 0x12):
            raise ParseErrorStr("bad fmt size")

        if self._vorb_offset == -1 and self._fmt_size == 0x42:
            # fake it out
            self._vorb_offset = self._fmt_offset + 0x18

        s.seek(self._fmt_offset)
        if self._read16() != 0xFFFF:
            raise ParseErrorStr("bad codec id")
        self.channels = self._read16()
        self.sample_rate = self._read32()
        self.avg_bytes_per_second = self._read32()
        if self._read16() != 0:
            raise ParseErrorStr("bad block align")
        if self._read16() != 0:
            raise ParseErrorStr("expected 0 bps")
        if self._fmt_size - 0x12 != self._read16():
            raise ParseErrorStr("bad extra fmt length")

        if self._fmt_size - 0x12 >= 2:
            # read extra fmt
            self._ext_unk = self._read16()
            if self._fmt_size - 0x12 >= 6:
                self._subtype = self._read32()

        if self._fmt_size == 0x28:
            whoknowsbuf = s.read_exactly(16)
            whoknowsbuf_check = bytes(
                (1, 0, 0, 0, 0, 0, 0x10, 0, 0x80, 0, 0, 0xAA, 0, 0x38, 0x9B, 0x71)
            )
            if whoknowsbuf != whoknowsbuf_check:
                raise ParseErrorStr("expected signature in extra fmt?")

        # read cue
        if self._cue_offset != -1:
            s.seek(self._cue_offset)
            self._cue_count = self._read32()

        # read smpl
        if self._smpl_offset != -1:
            s.seek(self._smpl_offset + 0x1C)
            self._loop_count = self._read32()

            if self._loop_count != 1:
                raise ParseErrorStr("expected one loop")

            s.seek(self._smpl_offset + 0x2C)
            self._loop_start = self._read32()
            self._loop_end = self._read32()

        # read vorb
        if self._vorb_size not in (-1, 0x28, 0x2A, 0x32, 0x34):
            raise ParseErrorStr("bad vorb size")

        s.seek(self._vorb_offset + 0x00)
        self.sample_count = self._read32()

        if self._vorb_size in (-1, 0x2A):
            self.no_granule = True

            s.seek(self._vorb_offset + 0x4)
            mod_signal = self._read32()

            # seems to be 0xD9 when _mod_packets should be set
            # also seen 0xCB, 0xBC, 0xB2
            if mod_signal not in (0x4A, 0x4B, 0x69, 0x70):
                self.mod_packets = True
            s.seek(self._vorb_offset + 0x10)
        else:
            s.seek(self._vorb_offset + 0x18)

        self._setup_packet_offset = self._read32()
        self._first_audio_packet_offset = self._read32()

        if self._vorb_size in (-1, 0x2A):
            s.seek(self._vorb_offset + 0x24)
        elif self._vorb_size in (0x32, 0x34):
            s.seek(self._vorb_offset + 0x2C)

        if self._vorb_size in (0x28, 0x2C):
            # ok to leave uid / blocksizes unset
            self.header_triad_present = True
            self.old_packet_headers = True
        else:
            self._uid = self._read32()
            b0 = s.read_byte()
            b1 = s.read_byte()
            if b0 < 0 or b1 < 0:
                raise ParseErrorStr("file truncated")
            self._blocksize0_pow = b0
            self._blocksize1_pow = b1

    # ------------------------------------------------------------------
    # packet helpers
    # ------------------------------------------------------------------
    def _read_packet(self, offset: int, no_granule: bool):
        """Modern 2 or 6 byte header."""
        s = self._stream
        s.seek(offset)

        size = self._read16()
        granule = 0
        header_size = 2 if no_granule else 6
        if not no_granule:
            granule = self._read32()

        return header_size, size, granule, offset + header_size, offset + header_size + size

    def _read_packet8(self, offset: int):
        """Old 8 byte header."""
        s = self._stream
        s.seek(offset)

        size = self._read32()
        granule = self._read32()

        return 8, size, granule, offset + 8, offset + 8 + size

    @staticmethod
    def _write_vorbis_packet_header(os_: BitWriter, packet_type: int) -> None:
        os_.write(packet_type, 8)
        for ch in b"vorbis":
            os_.write(ch, 8)

    # ------------------------------------------------------------------
    # output generation
    # ------------------------------------------------------------------
    def generate_ogg(self) -> bytes:
        """Port of WwiseRiffVorbis.GenerateOgg(Stream)."""
        out = bytearray()
        os_ = BitWriter(out)

        mode_blockflag = None
        mode_bits = 0
        prev_blockflag = False

        if self.header_triad_present:
            self._generate_ogg_header_with_triad(os_)
        else:
            mode_blockflag, mode_bits = self._generate_ogg_header(os_)

        # Audio pages
        offset = self._data_offset + self._first_audio_packet_offset
        end_offset = self._data_offset + self._data_size

        computed_granule = 0
        prev_blocksize = 0

        while offset < end_offset:
            if self.old_packet_headers:
                header_size, size, granule, payload_offset, next_offset = (
                    self._read_packet8(offset)
                )
            else:
                header_size, size, granule, payload_offset, next_offset = (
                    self._read_packet(offset, self.no_granule)
                )

            if offset + header_size > end_offset:
                raise ParseErrorStr("page header truncated")

            offset = payload_offset
            self._stream.seek(offset)

            # HACK: don't know what to do here
            if granule == 0xFFFFFFFF:
                os_.set_granule(1)
            else:
                os_.set_granule(granule)

            cur_blocksize = 0

            if self.mod_packets:
                # need to rebuild packet type and window info
                if mode_blockflag is None:
                    raise ParseErrorStr("didn't load mode_blockflag")

                # OUT: 1 bit packet type (0 == audio)
                os_.write(0, 1)

                ss = BitReader(self._stream)
                # IN/OUT: N bit mode number (max 6 bits)
                mode_number = ss.read(mode_bits)
                os_.write(mode_number, mode_bits)

                # IN: remaining bits of first (input) byte
                remainder = ss.read(8 - mode_bits)

                if mode_blockflag[mode_number]:
                    # long window, peek at next frame
                    self._stream.seek(next_offset)
                    next_blockflag = False
                    if next_offset + header_size <= end_offset:
                        # mod_packets always goes with 6-byte headers
                        (
                            _nh,
                            next_packet_size,
                            _ng,
                            next_payload_offset,
                            _nn,
                        ) = self._read_packet(next_offset, self.no_granule)
                        if next_packet_size > 0:
                            self._stream.seek(next_payload_offset)
                            nss = BitReader(self._stream)
                            next_mode_number = nss.read(mode_bits)
                            next_blockflag = mode_blockflag[next_mode_number]

                    # OUT: previous window type bit
                    os_.write(1 if prev_blockflag else 0, 1)
                    # OUT: next window type bit
                    os_.write(1 if next_blockflag else 0, 1)

                    # fix seek for rest of stream
                    self._stream.seek(offset + 1)

                prev_blockflag = mode_blockflag[mode_number]
                cur_blocksize = 1 << (
                    self._blocksize1_pow
                    if mode_blockflag[mode_number]
                    else self._blocksize0_pow
                )

                # OUT: remaining bits of first (input) byte
                os_.write(remainder, 8 - mode_bits)
            else:
                # nothing unusual for first byte
                v = self._stream.read_byte()
                if v < 0:
                    raise ParseErrorStr("file truncated")

                # for granule position computation on "no granule" streams
                if mode_blockflag is not None and mode_bits > 0:
                    mode_number = (v >> 1) & ((1 << mode_bits) - 1)
                    cur_blocksize = 1 << (
                        self._blocksize1_pow
                        if mode_blockflag[mode_number]
                        else self._blocksize0_pow
                    )

                os_.write(v, 8)

            # remainder of packet
            if size > 1:
                os_.write_bytes(self._stream.read_exactly(size - 1))

            offset = next_offset

            if self.no_granule and not self.old_packet_headers and cur_blocksize > 0:
                # running granule before this block was added
                prev_granule = computed_granule

                if prev_blocksize == 0:
                    computed_granule += cur_blocksize // 4
                else:
                    computed_granule += (prev_blocksize + cur_blocksize) // 4
                prev_blocksize = cur_blocksize

                # Refine the final position with the RIFF sample count, but
                # only when it stays non-regressing and does not run past the
                # last block's end.
                if offset == end_offset and self.sample_count != 0:
                    reported = self.sample_count
                    if prev_granule <= reported <= computed_granule:
                        computed_granule = reported

                os_.set_granule(computed_granule)

            os_.flush_page(False, offset == end_offset)

        if offset > end_offset:
            raise ParseErrorStr("page truncated")

        os_.finish()
        return bytes(out)

    def _generate_ogg_header(self, os_: BitWriter):
        # generate identification packet
        self._write_vorbis_packet_header(os_, 1)
        os_.write(0, 32)                    # version
        os_.write(self.channels, 8)
        os_.write(self.sample_rate, 32)
        os_.write(0, 32)                    # bitrate_max
        os_.write(self.avg_bytes_per_second * 8, 32)  # bitrate_nominal
        os_.write(0, 32)                    # bitrate_minimum
        os_.write(self._blocksize0_pow, 4)
        os_.write(self._blocksize1_pow, 4)
        os_.write(1, 1)                     # framing

        # identification packet on its own page
        os_.flush_page()

        # generate comment packet
        self._write_vorbis_packet_header(os_, 3)

        # empty vendor string
        os_.write(0, 32)

        if self._loop_count == 0:
            os_.write(0, 32)
        else:
            os_.write(2, 32)

            loop_start = ("LoopStart=%d" % self._loop_start).encode("ascii")
            os_.write(len(loop_start), 32)
            for ch in loop_start:
                os_.write(ch, 8)

            loop_end = ("LoopEnd=%d" % self._loop_end).encode("ascii")
            os_.write(len(loop_end), 32)
            for ch in loop_end:
                os_.write(ch, 8)

        os_.write(1, 1)  # framing
        os_.flush_page()

        # generate setup packet
        self._write_vorbis_packet_header(os_, 5)

        header_size, setup_size, setup_granule, payload_offset, next_offset = (
            self._read_packet(self._data_offset + self._setup_packet_offset, self.no_granule)
        )

        self._stream.seek(payload_offset)
        if setup_granule != 0:
            raise ParseErrorStr("setup packet granule != 0")
        ss = BitReader(self._stream)

        # codebook count
        codebook_count_less1 = ss.read(8)
        codebook_count = codebook_count_less1 + 1
        os_.write(codebook_count_less1, 8)

        # rebuild codebooks
        if self.inline_codebooks:
            cbl = CodebookLibrary()

            for _ in range(codebook_count):
                if self.full_setup:
                    cbl.copy(ss, os_)
                else:
                    cbl._rebuild_stream(ss, 0, os_)
        else:
            # external codebooks
            cbl = self.shared_codebooks or CodebookLibrary(self.codebook_file)

            for _ in range(codebook_count):
                codebook_id = ss.read(10)
                try:
                    cbl.rebuild(codebook_id, os_)
                except InvalidId:
                    if codebook_id == 0x342:
                        codebook_identifier = ss.read(14)
                        if codebook_identifier == 0x1590:
                            # starts with BCV, probably --full-setup
                            raise ParseErrorStr(
                                "invalid codebook id 0x342, try --full-setup"
                            )
                    raise

        # Time Domain transforms (placeholder)
        os_.write(0, 6)   # time_count_less1
        os_.write(0, 16)  # dummy time value

        mode_blockflag = None
        mode_bits = 0

        if self.full_setup:
            while ss.total_bits_read < setup_size * 8:
                bit = ss.read(1)
                os_.write(bit, 1)
        else:
            # floor count
            floor_count_less1 = ss.read(6)
            floor_count = floor_count_less1 + 1
            os_.write(floor_count_less1, 6)

            # rebuild floors
            for _ in range(floor_count):
                # Always floor type 1
                os_.write(1, 16)

                floor1_partitions = ss.read(5)
                os_.write(floor1_partitions, 5)

                floor1_partition_class_list = [0] * floor1_partitions

                maximum_class = 0
                for j in range(floor1_partitions):
                    floor1_partition_class = ss.read(4)
                    os_.write(floor1_partition_class, 4)

                    floor1_partition_class_list[j] = floor1_partition_class

                    if floor1_partition_class > maximum_class:
                        maximum_class = floor1_partition_class

                floor1_class_dimensions_list = [0] * (maximum_class + 1)

                for j in range(maximum_class + 1):
                    class_dimensions_less1 = ss.read(3)
                    os_.write(class_dimensions_less1, 3)

                    floor1_class_dimensions_list[j] = class_dimensions_less1 + 1

                    class_subclasses = ss.read(2)
                    os_.write(class_subclasses, 2)

                    if class_subclasses != 0:
                        masterbook = ss.read(8)
                        os_.write(masterbook, 8)

                        if masterbook >= codebook_count:
                            raise ParseErrorStr("invalid floor1 masterbook")

                    for _k in range(1 << class_subclasses):
                        subclass_book_plus1 = ss.read(8)
                        os_.write(subclass_book_plus1, 8)

                        subclass_book = subclass_book_plus1 - 1
                        if subclass_book >= 0 and subclass_book >= codebook_count:
                            raise ParseErrorStr("invalid floor1 subclass book")

                floor1_multiplier_less1 = ss.read(2)
                os_.write(floor1_multiplier_less1, 2)

                rangebits = ss.read(4)
                os_.write(rangebits, 4)

                for j in range(floor1_partitions):
                    current_class_number = floor1_partition_class_list[j]
                    for _k in range(floor1_class_dimensions_list[current_class_number]):
                        x = ss.read(rangebits)
                        os_.write(x, rangebits)

            # residue count
            residue_count_less1 = ss.read(6)
            residue_count = residue_count_less1 + 1
            os_.write(residue_count_less1, 6)

            # rebuild residues
            for _ in range(residue_count):
                residue_type = ss.read(2)
                os_.write(residue_type, 16)

                if residue_type > 2:
                    raise ParseErrorStr("invalid residue type")

                residue_begin = ss.read(24)
                residue_end = ss.read(24)
                residue_partition_size_less1 = ss.read(24)
                residue_classifications_less1 = ss.read(6)
                residue_classbook = ss.read(8)
                residue_classifications = residue_classifications_less1 + 1
                os_.write(residue_begin, 24)
                os_.write(residue_end, 24)
                os_.write(residue_partition_size_less1, 24)
                os_.write(residue_classifications_less1, 6)
                os_.write(residue_classbook, 8)

                if residue_classbook >= codebook_count:
                    raise ParseErrorStr("invalid residue classbook")

                residue_cascade = [0] * residue_classifications

                for j in range(residue_classifications):
                    high_bits = 0
                    low_bits = ss.read(3)
                    os_.write(low_bits, 3)

                    bitflag = ss.read(1)
                    os_.write(bitflag, 1)
                    if bitflag != 0:
                        high_bits = ss.read(5)
                        os_.write(high_bits, 5)

                    residue_cascade[j] = high_bits * 8 + low_bits

                for j in range(residue_classifications):
                    for k in range(8):
                        if residue_cascade[j] & (1 << k):
                            residue_book = ss.read(8)
                            os_.write(residue_book, 8)

                            if residue_book >= codebook_count:
                                raise ParseErrorStr("invalid residue book")

            # mapping count
            mapping_count_less1 = ss.read(6)
            mapping_count = mapping_count_less1 + 1
            os_.write(mapping_count_less1, 6)

            for _ in range(mapping_count):
                # always mapping type 0, the only one
                os_.write(0, 16)

                submaps_flag = ss.read(1)
                os_.write(submaps_flag, 1)

                submaps = 1
                if submaps_flag != 0:
                    submaps_less1 = ss.read(4)
                    submaps = submaps_less1 + 1
                    os_.write(submaps_less1, 4)

                square_polar_flag = ss.read(1)
                os_.write(square_polar_flag, 1)

                if square_polar_flag != 0:
                    coupling_steps_less1 = ss.read(8)
                    coupling_steps = coupling_steps_less1 + 1
                    os_.write(coupling_steps_less1, 8)

                    pair_bits = ilog(self.channels - 1)
                    for _j in range(coupling_steps):
                        magnitude = ss.read(pair_bits)
                        angle = ss.read(pair_bits)
                        os_.write(magnitude, pair_bits)
                        os_.write(angle, pair_bits)

                        if (
                            angle == magnitude
                            or magnitude >= self.channels
                            or angle >= self.channels
                        ):
                            raise ParseErrorStr("invalid coupling")

                # a rare reserved field not removed by Ak!
                mapping_reserved = ss.read(2)
                os_.write(mapping_reserved, 2)
                if mapping_reserved != 0:
                    raise ParseErrorStr("mapping reserved field nonzero")

                if submaps > 1:
                    for _j in range(self.channels):
                        mapping_mux = ss.read(4)
                        os_.write(mapping_mux, 4)

                        if mapping_mux >= submaps:
                            raise ParseErrorStr("mapping_mux >= submaps")

                for _j in range(submaps):
                    # Another! Unused time domain transform configuration placeholder!
                    time_config = ss.read(8)
                    os_.write(time_config, 8)

                    floor_number = ss.read(8)
                    os_.write(floor_number, 8)
                    if floor_number >= floor_count:
                        raise ParseErrorStr("invalid floor mapping")

                    residue_number = ss.read(8)
                    os_.write(residue_number, 8)
                    if residue_number >= residue_count:
                        raise ParseErrorStr("invalid residue mapping")

            # mode count
            mode_count_less1 = ss.read(6)
            mode_count = mode_count_less1 + 1
            os_.write(mode_count_less1, 6)

            mode_blockflag = [False] * mode_count
            mode_bits = ilog(mode_count - 1)

            for i in range(mode_count):
                block_flag = ss.read(1)
                os_.write(block_flag, 1)

                mode_blockflag[i] = block_flag != 0

                # only 0 valid for windowtype and transformtype
                os_.write(0, 16)  # windowtype
                os_.write(0, 16)  # transformtype

                mapping = ss.read(8)
                os_.write(mapping, 8)
                if mapping >= mapping_count:
                    raise ParseErrorStr("invalid mode mapping")

            os_.write(1, 1)  # framing

        os_.flush_page()

        if (ss.total_bits_read + 7) // 8 != setup_size:
            raise ParseErrorStr("didn't read exactly setup packet")

        if next_offset != self._data_offset + self._first_audio_packet_offset:
            raise ParseErrorStr("first audio packet doesn't follow setup packet")

        return mode_blockflag, mode_bits

    def _generate_ogg_header_with_triad(self, os_: BitWriter) -> None:
        # Header page triad
        offset = self._data_offset + self._setup_packet_offset

        # copy information packet
        header_size, size, granule, payload_offset, next_offset = self._read_packet8(
            offset
        )
        if granule != 0:
            raise ParseErrorStr("information packet granule != 0")

        self._stream.seek(payload_offset)
        first = self._stream.read_byte()
        if first != 1:
            raise ParseErrorStr("wrong type for information packet")
        os_.write(first, 8)
        if size > 1:
            os_.write_bytes(self._stream.read_exactly(size - 1))

        # identification packet on its own page
        os_.flush_page()
        offset = next_offset

        # copy comment packet
        header_size, size, granule, payload_offset, next_offset = self._read_packet8(
            offset
        )
        if granule != 0:
            raise ParseErrorStr("comment packet granule != 0")

        self._stream.seek(payload_offset)
        first = self._stream.read_byte()
        if first != 3:
            raise ParseErrorStr("wrong type for comment packet")
        os_.write(first, 8)
        if size > 1:
            os_.write_bytes(self._stream.read_exactly(size - 1))

        os_.flush_page()
        offset = next_offset

        # copy setup packet
        header_size, size, granule, payload_offset, next_offset = self._read_packet8(
            offset
        )
        self._stream.seek(payload_offset)
        if granule != 0:
            raise ParseErrorStr("setup packet granule != 0")
        ss = BitReader(self._stream)

        c = ss.read(8)
        if c != 5:
            raise ParseErrorStr("wrong type for setup packet")
        os_.write(c, 8)

        # 'vorbis'
        for _ in range(6):
            c = ss.read(8)
            os_.write(c, 8)

        # codebook count
        codebook_count_less1 = ss.read(8)
        codebook_count = codebook_count_less1 + 1
        os_.write(codebook_count_less1, 8)

        cbl = CodebookLibrary()

        # rebuild codebooks
        for _ in range(codebook_count):
            cbl.copy(ss, os_)

        while ss.total_bits_read < size * 8:
            bit = ss.read(1)
            os_.write(bit, 1)

        os_.flush_page()
        offset = next_offset

        if offset != self._data_offset + self._first_audio_packet_offset:
            raise ParseErrorStr("first audio packet doesn't follow setup packet")
