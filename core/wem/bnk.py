"""Wwise sound bank (.bnk) reader shared by every game."""

from __future__ import annotations

import struct


def extract_wem_media(bank_data: bytes) -> list[tuple[int, bytes]]:
    """Return ``(shortID, wem bytes)`` for every inline media of a bank."""
    if len(bank_data) < 8 or bank_data[:4] != b"BKHD":
        return []

    didx: tuple[int, int] | None = None
    data_chunk: tuple[int, int] | None = None
    offset = 0
    while offset + 8 <= len(bank_data):
        tag = bank_data[offset:offset + 4]
        size = struct.unpack_from("<I", bank_data, offset + 4)[0]
        payload = offset + 8
        if tag == b"DIDX" and size >= 12:
            didx = (payload, size)
        elif tag == b"DATA":
            data_chunk = (payload, size)
        offset = payload + size

    if didx is None or data_chunk is None:
        return []

    data_start, data_size = data_chunk
    media: list[tuple[int, bytes]] = []
    for index in range(didx[1] // 12):
        record = didx[0] + 12 * index
        short_id, rel_offset, length = struct.unpack_from("<III", bank_data, record)
        if not length or rel_offset + length > data_size:
            continue
        start = data_start + rel_offset
        media.append((short_id, bank_data[start:start + length]))
    return media


class BnkContainer:
    """Lists and extracts the embedded WEM payloads of a BNK file."""

    def __init__(self, data: bytes):
        self.files: list[tuple[str, bytes]] = [
            (str(short_id), payload) for short_id, payload in extract_wem_media(data)
        ]

    def list_files(self) -> list[str]:
        return [file_id for file_id, _ in self.files]

    def get_file_content(self, file_id: str) -> bytes | None:
        for embedded_id, content in self.files:
            if embedded_id == file_id:
                return content
        return None
