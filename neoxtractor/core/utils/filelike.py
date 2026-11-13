"""File-like helpers and the shared stream contract.

The file-like contract: a ``probe``/``read`` argument is either ``bytes``-like
or a :class:`Seekable` stream (``seek`` must support ``io.SEEK_END``).
:class:`Seekable` is the one stream type shared by the container layer and
the resource protocol layer; it lives here because ``utils`` is the neutral
base and must never import ``packages``.

Invariants:
- :func:`read_head` reads only the first *n* bytes and leaves a stream at
  position 0, so a source can be probed repeatedly.
- :func:`read_all` materializes the whole payload (block codecs such as
  PVR/KTX/ASTC cannot decode from a partial view).
"""

from __future__ import annotations

import io
from types import TracebackType
from typing import Protocol, TypeVar, Union

_S = TypeVar("_S")


class Seekable(Protocol):
    """Read-only seekable stream: the shared file-like contract.

    Narrower than ``typing.BinaryIO`` (no write/flush/fileno), yet ``BytesIO``,
    an ``open()`` file, and ``BinaryIO`` all satisfy it structurally.

    The ``__enter__``/``__exit__`` shapes are load-bearing (mypy-verified):
    ``__enter__`` must return an unbound self type, otherwise ``BinaryIO``
    fails to conform; ``__exit__`` must mirror typeshed's narrow signature
    for contravariant-safe implementations. Do not simplify either.
    """

    def read(self, n: int = -1) -> bytes: ...
    def seek(self, offset: int, whence: int = 0) -> int: ...
    def tell(self) -> int: ...
    def close(self) -> None: ...
    @property
    def closed(self) -> bool: ...
    def __enter__(self: _S) -> _S: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


FileLike = Union[bytes, bytearray, memoryview, Seekable]


def as_stream(f: FileLike) -> Seekable:
    """Coerce bytes-like input to a BytesIO; pass real streams through."""
    if isinstance(f, (bytes, bytearray, memoryview)):
        return io.BytesIO(f)
    return f


def read_head(f: FileLike, n: int) -> bytes:
    """Return at most the first *n* bytes, leaving a stream at position 0."""
    if isinstance(f, (bytes, bytearray, memoryview)):
        return bytes(f)[:n]
    f.seek(0)
    data = f.read(n)
    f.seek(0)
    return data


def read_all(f: FileLike) -> bytes:
    """Return the full payload via the file-like interface (seek(0)+read)."""
    if isinstance(f, (bytes, bytearray, memoryview)):
        return bytes(f)
    f.seek(0)
    return f.read()
