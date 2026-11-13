"""NeoX BXML viewer: decodes binary XML to text (a converting CodeEditor).

BXML is NeoX's on-disk binary XML encoding.  This viewer overrides only
the content transform: bytes are decoded through
``processors.read(..., format="bxml")`` and the resulting XML text uses
the inherited code-editor display unchanged.

The claimed extensions ``mtg``/``gim``/``ags`` are ambiguous: such files
may be binary BXML or plain XML text.  ``FormatMismatch`` (magic miss)
is not an error -- the bytes fall back to plain-text display.  Truly
corrupt BXML raises instead.

Decode note: ``INPUT = "stream"`` with conversion inside
:meth:`decode_content` is deliberate.  ``INPUT = "read"`` would make the
dispatcher run the facade before the viewer is chosen, so a plain-text
``.mtg`` would fail source production ("No reader claims it") instead of
reaching this fallback.
"""

from neoxtractor.core import processors
from neoxtractor.core.processors.errors import FormatMismatch
from neoxtractor.gui.viewers.base import ViewSource
from neoxtractor.gui.viewers.fallback_code.code_editor import CodeEditor


class BxmlViewer(CodeEditor):
    """Displays BXML payloads as decoded XML text."""

    name = "BXML Viewer"
    accepted_extensions = {"bxml", "mtg", "gim", "ags"}

    def decode_content(self, source: ViewSource) -> str:
        """Decode BXML to XML text; show any other payload as plain text."""
        data = source.content()
        try:
            result = processors.read(data, format="bxml")
        except FormatMismatch:
            # Not BXML: ambiguous extension, plain text. Display as-is.
            return data.decode(errors="replace")
        assert result is not None  # forced read() raises instead of None
        return str(result.data)


# Contract members read by the viewers registry.  The class attributes are
# the single source of truth; these module-level names only re-export them.
NAME = BxmlViewer.name
VIEWER_CLASS = BxmlViewer
ACCEPTED_EXTENSIONS = BxmlViewer.accepted_extensions
INPUT = "stream"
FALLBACK: str | None = None
PRIORITY = 100
