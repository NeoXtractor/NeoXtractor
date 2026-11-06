"""Payload decoding and nested wrapper unwrapping for WPK entries."""

from __future__ import annotations

import struct
import zlib

from core.logger import get_logger
from core.npk.class_types import NPKEntry, NPKEntryDataFlags
from core.npk.decompression import (
    check_lz4_like,
    check_nxs3,
    check_rotor,
    log_rotor_failure,
    strip_none_wrapper,
    unpack_lz4_like,
    unpack_nxs3,
    unpack_rotor,
)
from core.npk.detection import get_ext, is_binary
from core.wpk.decryption import try_decode_payload_stage1


class WPKPayloadProcessor:
    """Decode WPK payloads, including slot_file heuristics and nested wrappers."""

    def maybe_unpack_dtsz(self, data: bytes, *, context: str) -> tuple[bytes, bool]:
        if len(data) < 8 or data[:4] != b"DTSZ" or data[4:8] != b"\x28\xb5\x2f\xfd":
            return data, False

        try:
            import zstandard
        except Exception as exc:
            get_logger().debug("DTSZ support unavailable for %s: %s", context, exc)
            return data, False

        try:
            unpacked = zstandard.ZstdDecompressor().decompress(data[4:])
            get_logger().debug(
                "DTSZ unpacked: %s in=%d out=%d", context, len(data), len(unpacked)
            )
            return unpacked, True
        except Exception as exc:
            get_logger().warning("DTSZ decompress failed for %s: %s", context, exc)
            return data, False

    def maybe_strip_enon_header(
        self, data: bytes, *, context: str
    ) -> tuple[bytes, bool]:
        if len(data) < 4 or data[:4] != b"ENON":
            return data, False

        stripped = data[4:]
        get_logger().debug(
            "ENON header stripped: %s in=%d out=%d", context, len(data), len(stripped)
        )
        return stripped, True

    def unwrap_nested_payloads(self, entry: NPKEntry, *, context: str) -> None:
        entry.none_header_stripped = bool(getattr(entry, "none_header_stripped", False))
        entry.enon_header_stripped = bool(getattr(entry, "enon_header_stripped", False))
        entry.dtsz_unpacked = bool(getattr(entry, "dtsz_unpacked", False))
        entry.cobl_unpacked = bool(getattr(entry, "cobl_unpacked", False))
        entry.unwrap_layers = list(getattr(entry, "unwrap_layers", []))

        seen_signatures: set[tuple[int, bytes]] = set()
        max_layers = 32
        for _ in range(max_layers):
            signature = (len(entry.data), entry.data[:16])
            if signature in seen_signatures:
                get_logger().debug(
                    "Nested payload unwrap stopped on repeated signature: %s len=%d head=%s layers=%s",
                    context,
                    len(entry.data),
                    entry.data[:8].hex().upper(),
                    ",".join(entry.unwrap_layers) if entry.unwrap_layers else "-",
                )
                break
            seen_signatures.add(signature)

            stripped = strip_none_wrapper(entry.data)
            if stripped != entry.data:
                get_logger().debug(
                    "NONE header stripped: %s in=%d out=%d",
                    context,
                    len(entry.data),
                    len(stripped),
                )
                entry.data = stripped
                entry.none_header_stripped = True
                entry.unwrap_layers.append("NONE")
                continue

            stripped, did_strip = self.maybe_strip_enon_header(
                entry.data, context=context
            )
            if did_strip:
                entry.data = stripped
                entry.enon_header_stripped = True
                entry.unwrap_layers.append("ENON")
                continue

            unpacked, did_unpack = self.maybe_unpack_dtsz(entry.data, context=context)
            if did_unpack:
                entry.data = unpacked
                entry.dtsz_unpacked = True
                entry.unwrap_layers.append("DTSZ")
                continue

            unpacked, did_unpack = self.maybe_unpack_cobl(entry.data, context=context)
            if did_unpack:
                entry.data = unpacked
                entry.cobl_unpacked = True
                entry.unwrap_layers.append("COBL")
                continue

            if check_lz4_like(entry.data):
                before_len = len(entry.data)
                try:
                    unpacked = unpack_lz4_like(entry.data)
                except Exception as exc:
                    get_logger().warning(
                        "LZ4-like unpack failed for %s: %s", context, exc
                    )
                    break

                if unpacked and unpacked != entry.data:
                    entry.data = unpacked
                    get_logger().debug(
                        "LZ4-like unpacked: %s in=%d out=%d",
                        context,
                        before_len,
                        len(entry.data),
                    )
                    entry.unwrap_layers.append("LZ4_LIKE")
                    continue

            if check_rotor(entry):
                before_len = len(entry.data)
                try:
                    unpacked = unpack_rotor(entry.data)
                except Exception as exc:
                    # The 2-byte marker is only a hint and can collide with an
                    # ordinary payload; keep the bytes instead of failing the
                    # whole entry.
                    log_rotor_failure(context, exc)
                    break

                entry.data_flags |= NPKEntryDataFlags.ROTOR_PACKED
                entry.data = unpacked
                get_logger().debug(
                    "ROTOR unpacked: %s in=%d out=%d",
                    context,
                    before_len,
                    len(entry.data),
                )
                entry.unwrap_layers.append("ROTOR")
                continue

            if check_nxs3(entry):
                before_len = len(entry.data)
                entry.data_flags |= NPKEntryDataFlags.NXS3_PACKED
                entry.data = unpack_nxs3(entry.data)
                get_logger().debug(
                    "NXS3 unpacked: %s in=%d out=%d",
                    context,
                    before_len,
                    len(entry.data),
                )
                entry.unwrap_layers.append("NXS3")
                continue

            break
        else:
            get_logger().warning(
                "Nested payload unwrap hit layer limit: %s layers=%s final_len=%d head=%s",
                context,
                ",".join(entry.unwrap_layers) if entry.unwrap_layers else "-",
                len(entry.data),
                entry.data[:8].hex().upper(),
            )

    def maybe_unpack_cobl(self, data: bytes, *, context: str) -> tuple[bytes, bool]:
        if len(data) < 16 or data[:4] not in (b"LBOC", b"COBL"):
            return data, False

        try:
            unpacked = self.decode_cobl_concat(data, context=context)
            if not unpacked:
                get_logger().debug("COBL decode produced empty output for %s", context)
                return data, False

            get_logger().debug(
                "COBL unpacked: %s in=%d out=%d", context, len(data), len(unpacked)
            )
            return unpacked, True
        except Exception as exc:
            get_logger().warning("COBL decode failed for %s: %s", context, exc)
            return data, False

    def decode_cobl_concat(self, data: bytes, *, context: str) -> bytes:
        magic, field04, field08, block_count = struct.unpack_from("<4I", data, 0)
        if magic != 0x434F424C:
            raise ValueError(f"bad COBL magic 0x{magic:08X}")

        data_base = 16 + block_count * 8
        if len(data) < data_base:
            raise ValueError(
                f"COBL truncated block table: need >= {data_base}, got {len(data)}"
            )

        rel_offset = 0
        out = bytearray()

        for block_index in range(block_count):
            size, extra, unk = struct.unpack_from("<IHH", data, 16 + block_index * 8)
            start = data_base + rel_offset
            end = start + size
            if end > len(data):
                raise ValueError(
                    f"COBL block {block_index} out of range: start={start} end={end} size={len(data)}"
                )

            payload = data[start:end]
            decoded = self.decode_cobl_block(
                payload, context=f"{context} block={block_index}"
            )
            out.extend(decoded)
            rel_offset += size + extra

        return bytes(out)

    def decode_cobl_block(self, data: bytes, *, context: str) -> bytes:
        if not data:
            return b""

        patched, probe_len = self.deobfuscate_cobl_probe_region(data)
        if probe_len < 4 or len(patched) < 4:
            return data

        tag = struct.unpack_from("<I", patched, 0)[0]
        payload = patched[4:]

        if tag == 0x4E4F4E45:
            return payload

        if tag == 0x5A4C4942:
            return zlib.decompress(payload)

        if tag == 0x5A535444:
            try:
                import zstandard
            except Exception as exc:
                raise RuntimeError(
                    f"COBL block requires zstandard for {context}: {exc}"
                ) from exc
            return zstandard.ZstdDecompressor().decompress(payload)

        if tag == 0x4C5A3446:
            try:
                import lz4.frame as lz4f
            except Exception as exc:
                raise RuntimeError(
                    f"COBL block requires lz4.frame for {context}: {exc}"
                ) from exc
            return lz4f.decompress(payload)

        if tag == 0x4F4F444C:
            raise RuntimeError(f"COBL block uses unsupported Oodle codec for {context}")

        return data

    def deobfuscate_cobl_probe_region(self, data: bytes) -> tuple[bytes, int]:
        if not data:
            return data, 0

        probe_len = min(64, len(data))
        if probe_len <= 3:
            return data, 0

        chunk = data[:probe_len]
        fixed = bytes((b ^ 0x5A) for b in chunk[::-1])

        patched = bytearray(data)
        patched[:probe_len] = fixed
        return bytes(patched), probe_len

    def score_slot_stage1_candidate(self, data: bytes) -> tuple[int, str]:
        if not data:
            return -1000, "empty"

        score = 0
        reasons: list[str] = []

        known_heads = {
            b"ENON": 140,
            b"NXS3": 140,
            b"LBOC": 140,
            b"RIFF": 120,
            b"OggS": 120,
            b"FSB5": 120,
            b"BKHD": 120,
            b"DDS ": 120,
            b"PVR": 120,
            b"RGIS": 120,
            b"VANT": 120,
            b"NTRK": 120,
            b"PK\x03\x04": 120,
            b"CompBlks": 120,
            b"SKELETON": 120,
            b"NEOXBIN1": 120,
            b"NEOXMESH": 120,
            b"RAWANIMA": 120,
        }

        for magic, bonus in known_heads.items():
            if data.startswith(magic):
                score += bonus
                reasons.append(f"magic={magic.decode('latin1', errors='ignore')}")
                break

        head = data[:64]
        if head:
            z5a = head.count(0x5A)
            if z5a >= 16:
                penalty = min(80, z5a * 2)
                score -= penalty
                reasons.append(f"z5a={z5a}")

            unique = len(set(head))
            if unique <= 8:
                score -= 40
                reasons.append(f"uniq={unique}")
            elif unique >= 24:
                score += 15
                reasons.append(f"uniq={unique}")

        flags = NPKEntryDataFlags(0)
        if not is_binary(data):
            flags |= NPKEntryDataFlags.TEXT

        ext = get_ext(data, flags)
        if ext != "dat":
            score += 80
            reasons.append(f"ext={ext}")
        elif data[:4].isalpha():
            score += 20
            reasons.append("alpha_head")

        nul_ratio = data[:64].count(0) / max(1, len(data[:64]))
        if 0.10 <= nul_ratio <= 0.75:
            score += 10
            reasons.append("structured_nuls")

        return score, ",".join(reasons) if reasons else "none"

    def decode_slot_payload_auto(
        self, payload: bytes, *, context: str
    ) -> tuple[bytes, bool, int | None, bool]:
        with_header, with_decoded, with_tag = try_decode_payload_stage1(
            payload,
            context=f"{context} slot_auto with_header",
            skip_header_decode=False,
        )
        no_header, no_decoded, no_tag = try_decode_payload_stage1(
            payload,
            context=f"{context} slot_auto no_header",
            skip_header_decode=True,
        )

        if with_decoded and no_decoded:
            with_score, with_reason = self.score_slot_stage1_candidate(with_header)
            no_score, no_reason = self.score_slot_stage1_candidate(no_header)

            choose_no_header = no_score > with_score
            chosen = no_header if choose_no_header else with_header
            chosen_tag = no_tag if choose_no_header else with_tag

            get_logger().debug(
                "slot_file auto-select: %s with_header(score=%d,%s) no_header(score=%d,%s) -> %s",
                context,
                with_score,
                with_reason,
                no_score,
                no_reason,
                "no_header" if choose_no_header else "with_header",
            )
            return chosen, True, chosen_tag, choose_no_header

        if no_decoded:
            get_logger().debug(
                "slot_file auto-select: %s only no_header decoded", context
            )
            return no_header, True, no_tag, True

        if with_decoded:
            get_logger().debug(
                "slot_file auto-select: %s only with_header decoded", context
            )
            return with_header, True, with_tag, False

        return payload, False, None, False
