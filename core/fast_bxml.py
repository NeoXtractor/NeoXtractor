"""
Faster binary XML (BXML) decoding, with the same text out.

core.formats.bxml (bytecode only in the exe) turns every .gim / .mtg / .sfx style binary XML into text, and
"Scanning same-name archive metadata" does that for tens of thousands of files. Two parts of it were slow:

- its string reader read one byte per call until the zero byte; the new body reads a chunk and finds the zero
  byte in it, and string tables are split in one pass (same strings, same position afterwards);
- it pretty-printed by serialising the tree, parsing that text again with minidom and writing it back out
  (minidom.parseString(ET.tostring(root)).toprettyxml(indent="  ")). pretty_xml() writes minidom's exact
  format straight from the tree instead. Trees it can't vouch for (text next to child elements, tails,
  namespaces, characters XML can't carry or would normalise such as \\r) take the original round trip.

install() swaps the string readers' bodies in place and gives the bxml module stand-ins for its ET and minidom
names that take the fast path only for that one ET.tostring -> minidom.parseString pair.
"""

import re
import xml.dom.minidom as _minidom
import xml.etree.ElementTree as _ET

from core.formats import bxml as _bxml
from core.formats.bxml import _read_leb128  # used by _read_string_pool, which runs with bxml's globals

# characters that make the round trip change or fail: control characters (except \t and \n), \r (expat turns
# it into \n in text), lone surrogates and the two non-characters XML forbids
_UNSAFE = re.compile("[\x00-\x08\x0b\x0c\x0d\x0e-\x1f\ud800-\udfff￾￿]")


def _read_null_terminated_utf8(buf):
    # Runs with core.formats.bxml's globals; uses builtins only
    start = buf.tell()
    parts = []
    while True:
        chunk = buf.read(64)
        if not chunk:
            break
        end = chunk.find(b"\x00")
        if end >= 0:
            parts.append(chunk[:end])
            buf.seek(start + end + 1)
            break
        parts.append(chunk)
        start += len(chunk)
    return b"".join(parts).decode("utf-8", "ignore")


def _read_string_pool(buf):
    # Runs with core.formats.bxml's globals (_read_leb128): the count, then that many zero-terminated strings,
    # split in one pass; at the end of the data the missing strings are empty, as with the byte-wise reader
    count = _read_leb128(buf)
    start = buf.tell()
    data = buf.read()
    strings, pos = [], 0
    for _ in range(count):
        end = data.find(b"\x00", pos)
        if end < 0:
            strings.append(data[pos:].decode("utf-8", "ignore"))
            pos = len(data)
        else:
            strings.append(data[pos:end].decode("utf-8", "ignore"))
            pos = end + 1
    buf.seek(start + pos)
    return strings


class _Unusual(Exception):
    """A tree pretty_xml() leaves to minidom."""


def _escape(text: str) -> str:
    """minidom's _write_data, for text and attribute values alike."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace("\"", "&quot;").replace(">", "&gt;")


def _checked(value) -> str:
    if type(value) is not str:  # pylint: disable=unidiomatic-typecheck
        raise _Unusual
    return value


def pretty_xml(root) -> str | None:
    """
    What minidom.parseString(ET.tostring(root, encoding="utf-8")).toprettyxml(indent="  ") gives without its
    first (<?xml ...?>) line, or None when the tree is one to leave to that round trip.
    """
    out = []

    def write(element, indent):
        tag = _checked(element.tag)
        if "{" in tag or element.tail:
            raise _Unusual
        out.append(f"{indent}<{tag}")
        for name, value in element.attrib.items():
            if "{" in _checked(name):
                raise _Unusual
            out.append(f' {name}="{_escape(_checked(value))}"')
        text = element.text
        children = list(element)
        if text:
            if children:
                raise _Unusual
            out.append(f">{_escape(_checked(text))}</{tag}>\n")
        elif children:
            out.append(">\n")
            for child in children:
                write(child, indent + "  ")
            out.append(f"{indent}</{tag}>\n")
        else:
            out.append("/>\n")

    try:
        write(root, "")
    except (_Unusual, TypeError, AttributeError, RecursionError):
        return None
    text = "".join(out)
    # escaping adds no such characters, so one search over the result covers every tag, name, value and text
    return None if _UNSAFE.search(text) else text


class _Deferred(bytes):
    """Stands in for ET.tostring(root, encoding="utf-8") until minidom.parseString decides it needs the bytes."""

    root = None


class _Pretty:
    def __init__(self, text):
        self.text = text

    def toprettyxml(self, indent="\t", newl="\n", encoding=None, standalone=None):
        if indent == "  " and newl == "\n" and encoding is None and standalone is None:
            return '<?xml version="1.0" ?>\n' + self.text
        raise ValueError("fast_bxml only formats with indent='  '")


class _ETNames:
    """The bxml module's ET: ET.tostring(root, encoding="utf-8") is deferred, everything else is ElementTree."""

    def __getattr__(self, name):
        return getattr(_ET, name)

    @staticmethod
    def tostring(element, *args, **kwargs):
        if args or kwargs != {"encoding": "utf-8"}:
            return _ET.tostring(element, *args, **kwargs)
        deferred = _Deferred()
        deferred.root = element
        return deferred


class _MinidomNames:
    """The bxml module's minidom: a deferred tree is written by pretty_xml() when it can be, else parsed."""

    def __getattr__(self, name):
        return getattr(_minidom, name)

    @staticmethod
    def parseString(string, *args, **kwargs):  # pylint: disable=invalid-name
        if isinstance(string, _Deferred):
            text = pretty_xml(string.root) if not args and not kwargs else None
            if text is not None:
                return _Pretty(text)
            string = _ET.tostring(string.root, encoding="utf-8")
        return _minidom.parseString(string, *args, **kwargs)


def install() -> bool:
    """False when core.formats.bxml isn't the module this was written for (or it is installed already)."""
    reader = getattr(_bxml, "_read_null_terminated_utf8", None)
    pool = getattr(_bxml, "_read_string_pool", None)
    if reader is None or pool is None or getattr(reader, "_fast", False):
        return False
    if any(f.__code__.co_argcount != 1 or f.__code__.co_freevars for f in (reader, pool)):
        return False
    if not hasattr(_bxml, "_read_leb128"):
        return False
    if getattr(_bxml, "ET", None) is not _ET or getattr(_bxml, "minidom", None) is not _minidom:
        return False
    reader.__code__ = _read_null_terminated_utf8.__code__
    pool.__code__ = _read_string_pool.__code__
    reader._fast = True  # pylint: disable=protected-access
    _bxml.ET = _ETNames()
    _bxml.minidom = _MinidomNames()
    return True
