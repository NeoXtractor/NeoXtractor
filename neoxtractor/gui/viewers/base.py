"""Viewer contract: the uniform input form and the base class.

A viewer is a stateless module of ``neoxtractor.gui.viewers``:
module-level data is immutable constants only; all per-use state lives
in the widget instance.  Each module declares its contract members -- ``NAME``,
``VIEWER_CLASS``, ``ACCEPTED_EXTENSIONS``, ``INPUT``, ``FALLBACK``,
``PRIORITY`` -- and the registry in this package discovers, validates,
selects, and lazily instantiates it.

``ViewSource`` is the single value object handed to a viewer.  Each viewer
declares which payload half it consumes (``INPUT``) and the dispatcher
produces exactly that half and nothing else:

- ``INPUT = "stream"``: the viewer reads the entry's bytes itself through
  ``source.content()`` (decoded) or ``source.raw()`` (stored bytes, only
  when ``raw_available``).  No processor runs.
- ``INPUT = "read"``: the dispatcher runs ``processors.read`` and the viewer
  consumes ``source.read_result``.  Reserved for future viewers and plugins.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Iterator, Literal, Optional, Protocol

from PySide6 import QtWidgets

from neoxtractor.core import processors
from neoxtractor.core.packages.base import Archive, RefKind
from neoxtractor.core.packages.errors import PackageError
from neoxtractor.core.processors import ReadResult
from neoxtractor.core.utils.content import is_binary
from neoxtractor.core.utils.filelike import Seekable

if TYPE_CHECKING:
    from neoxtractor.gui.windows.viewer_tab_window import ViewerTabWindow

InputForm = Literal["read", "stream"]
FallbackKind = Literal["text", "binary"]


class SiblingAccess(Protocol):
    """Cross-entry read capability inside one package, without naming it.

    A viewer that needs a *different* entry of the same package asks this
    object, never an archive.  ``open`` returns a lazily decoded
    :class:`Seekable` the caller must close; nothing reads a whole package
    up front.

    ``None`` from ``open`` means the name is not in the package.
    """

    def names(self) -> Iterator[str]:
        """Display names of the package's addressable entries."""
        ...

    def open(self, name: str) -> Seekable | None:
        """Open one sibling's decoded content; the caller owns the stream."""
        ...


class _ArchiveSiblings:
    """SiblingAccess backed by an Archive -- the dispatcher's adapter.

    The only place a viewer-side object touches ``get_res``; viewers see
    the protocol, never this.  Name resolution follows the archive's NAME
    channel (first duplicate wins, per the archive contract).
    """

    def __init__(self, archive: Archive) -> None:
        self._archive = archive
        self._names: dict[str, None] = {}
        for info in archive.entries():
            if info.name is not None:
                self._names.setdefault(info.name, None)

    def names(self) -> Iterator[str]:
        return iter(self._names)

    def open(self, name: str) -> Seekable | None:
        if name not in self._names:
            return None
        try:
            return self._archive.get_res(name, RefKind.NAME)
        except PackageError:
            # NoSuchEntry, UnsupportedRef and PayloadDecodeError all mean
            # "not openable"; the ``None`` contract covers the caller.
            return None


@dataclass(frozen=True)
class ViewSource:
    """One preview request, produced by the dispatcher for a viewer.

    ``name`` is the display name (extension included where known).
    ``archive`` and ``key`` are **dispatcher-private**: a viewer must not
    touch them; every archive capability arrives through a neutral face --
    stored bytes through :meth:`raw`, cross-entry reads through
    ``siblings``.  Sources made from external files leave all of those
    empty and have no raw or sibling variant.  The viewer owns ``stream``
    and closes it on ``unload`` (or when displaced); ``content()`` is a
    re-seek convenience, not a second owner.
    """

    name: str
    input: InputForm
    read_result: ReadResult | None = None
    stream: Seekable | None = None
    archive: Archive | None = None
    key: int | None = None
    siblings: SiblingAccess | None = None
    raw_available: bool = False
    content_is_text: bool = False

    def extension(self) -> str:
        """Lowercase extension of the display name, without a leading dot."""
        return os.path.splitext(self.name)[1].lstrip(".").lower()

    def content(self) -> bytes:
        """The stream's bytes, re-seeked to the start for each call."""
        if self.stream is None:
            raise ValueError("source carries no stream")
        self.stream.seek(0)
        return self.stream.read()

    def raw(self) -> bytes:
        """The entry's stored bytes (skips decryption and decompression).

        Raises ``ValueError`` when the source has no archive or the format
        has no stored-bytes variant for this entry.
        """
        if self.archive is None or self.key is None or not self.raw_available:
            raise ValueError("no raw variant for this source")
        with self.archive.get_res(self.key, decoded=False) as stream:
            return stream.read()


class ViewerBase(QtWidgets.QWidget):
    """Base class for all viewers.

    A viewer displays one :class:`ViewSource` at a time.  ``load`` replaces
    any previous source and raises ``ValueError`` when it cannot show this
    one (the message is for the user); ``unload`` releases the current
    source.

    Subclasses call ``super().load(source)`` first and ``super().unload()``:
    the base closes the previous source's stream on replace or release, so
    no stream outlives the preview that owns it.
    """

    VIEWER_NAME: ClassVar[str] = "Base Viewer"

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._source: ViewSource | None = None

    def load(self, source: ViewSource) -> None:
        """Adopt ``source``, releasing the previous one's stream.

        Subclasses override this, call ``super().load(source)`` first, then
        read ``source.content()`` / ``source.read_result`` as their ``INPUT``
        declares.  Raise ``ValueError`` to reject the source.
        """
        self._release_stream()
        self._source = source

    def unload(self) -> None:
        """Release the current source and close any stream it owns."""
        self._release_stream()
        self._source = None

    def _release_stream(self) -> None:
        if self._source is not None and self._source.stream is not None:
            try:
                self._source.stream.close()
            except Exception:  # a stream that will not close is already gone
                pass

    @property
    def source(self) -> ViewSource | None:
        return self._source


class ICustomTabWindow:
    """Mixin for viewers that add menus to a standalone :class:`ViewerTabWindow`.

    The consuming window imports this mixin, never the reverse.
    """

    @staticmethod
    def setup_tab_window(window: ViewerTabWindow) -> None:
        """Add custom actions to ``window``.

        Subclasses implement this; the window calls it when the viewer class
        is an :class:`ICustomTabWindow`.
        """
        raise NotImplementedError("Subclasses must implement setup_tab_window()")


def make_entry_source(
    archive: Archive,
    key: int,
    name: str,
    input: InputForm = "stream",
) -> ViewSource:
    """Produce a source for one archive entry.

    A ``"stream"`` source hands over the decoded stream plus the archive
    reference (stored bytes stay re-fetchable through ``raw()``).  A
    ``"read"`` source runs ``processors.read`` on a freshly opened stream
    and keeps only the ``ReadResult``.  ``ValueError`` from a viewer's
    ``load`` is the caller's signal to show a message; decoding faults here
    propagate for the caller to report.
    """
    if input == "read":
        with archive.get_res(key) as stream:
            result = processors.read(stream)
        if result is None:
            raise ValueError(f"No reader claims {name!r}")
        return ViewSource(
            name=name,
            input="read",
            read_result=result,
            archive=archive,
            key=key,
            siblings=_ArchiveSiblings(archive),
            raw_available=True,
        )

    stream = archive.get_res(key)
    data = stream.read()
    stream.seek(0)
    return ViewSource(
        name=name,
        input="stream",
        stream=stream,
        archive=archive,
        key=key,
        siblings=_ArchiveSiblings(archive),
        raw_available=True,
        content_is_text=not is_binary(data),
    )


def make_file_source(path: str, data: bytes) -> ViewSource:
    """Produce a source for an external file's bytes (no archive behind it)."""
    stream = io.BytesIO(data)
    return ViewSource(
        name=os.path.basename(path),
        input="stream",
        stream=stream,
        raw_available=False,
        content_is_text=not is_binary(data),
    )
