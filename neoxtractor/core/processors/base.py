"""Base types for special-format processors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class FormatDecodeResult:
    """Decoded result produced by a format processor."""

    data: bytes | str
    is_text: bool = False
    processor_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_bytes(self) -> bytes:
        if isinstance(self.data, bytes):
            return self.data
        return self.data.encode("utf-8")


class FormatProcessor:
    """Base class for special-format processors."""

    name = "processor"
    priority = 100

    def probe(self, data: bytes, entry) -> bool:
        raise NotImplementedError

    def decode(self, data: bytes, entry) -> FormatDecodeResult | None:
        raise NotImplementedError
