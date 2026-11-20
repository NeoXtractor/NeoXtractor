"""Neutral vocabulary for the packages layer: enums, value objects, helpers.

Everything a container module needs to describe itself lives here, and nothing
here knows about any concrete format.  The three relationship channels
(``wants`` / ``parents`` / ``companions``) are all *declarations*: a module
states what it is and what it would like nearby, and never touches the
filesystem beyond the single file it was shown.  Existence is always the
driver's verdict, because only the driver holds the consent hook.

IO is authorised by possession.  :class:`PackageFile` is the only object that
grants reading, and none of its verbs takes a path, so a module cannot consume
a neighbouring file even by accident -- the invariant lives in the
parameterless verbs, not in hidden strings.  The same :class:`Seekable` stream
contract is shared with the processors layer and is imported from ``utils``
(``utils`` is the neutral base and must never import ``packages``).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Any, Callable, Iterator, Mapping, NamedTuple, Protocol

from neoxtractor.core.packages.errors import ProtocolError
from neoxtractor.core.utils.filelike import Seekable

# Hard ceiling on the pool a single scan may grow, so a pathological glob can
# never expand without bound.
MAX_POOL = 4096

# A read-only query a declaring module offers about itself.  The driver
# hands it to a pulled child as the second element of matches()'s want_ask.
# Arguments are agreed by the two modules (keyword arguments recommended);
# a child that calls it with an incompatible signature degrades via the
# driver's guard, never faults the scan.
Ask = Callable[..., Any]


class Role(StrEnum):
    """The job a container performs inside a unit.

    ``INDEX`` = entry table (idx); ``DATA`` = payload store (a wpk part or a
    bare slot file); ``NAMES`` = a name registry that fills in entry names
    for its parent (a thd component, a sidecar list).  A self-sufficient
    format (npk) declares ``None`` and never attaches.  Formats may extend
    the vocabulary; attach matches on equality only and never interprets an
    unknown value.
    """

    INDEX = "index"
    DATA = "data"
    NAMES = "names"


class RefKind(Enum):
    """What the reference argument of :meth:`Archive.get_res` denotes."""

    INDEX = "index"                # positional key (EntryInfo.key), the default
    NAME = "name"                  # the presented name, whatever its name_source
    SIGNATURE = "signature"        # 32-bit name hash (npk file_signature / resid32)
    CONTENT_HASH = "content_hash"  # 16-byte content hash


class NameSource(StrEnum):
    """How an entry's presented ``name`` was obtained (never a guess)."""

    NXFN = "nxfn"                  # plaintext name table inside the npk
    SIDECAR = "sidecar"            # plaintext name from a sidecar name list
    RESID32 = "resid32"            # recovered via thd plus a name dictionary
    CONTENT_HASH = "content_hash"  # hex of the 16B hash standing in for a name
    MAP = "map"                    # user-provided name_map override
    NONE = "none"


class Status(StrEnum):
    """Per-entry availability, decided from index metadata alone."""

    OK = "ok"
    MISSING = "missing"            # record exists, payload not locally present
    CORRUPTED = "corrupted"        # payload is here but contradicts the index
                                   #   that named it: the store is truncated
                                   #   under the block, or the block header's
                                   #   hash or sizes disagree with the record
    DELETED = "deleted"            # thh slot with no idx record (hidden by default)
    ENCRYPTED = "encrypted"        # payload needs an option the caller withheld
    UNRECOGNIZED = "unrecognized"  # index names an encryption/compression
                                   # algorithm this build does not know; the
                                   # stored bytes are complete but decoding
                                   # falls through untouched


class Capability(StrEnum):
    """What a loaded archive can offer, a pure function of its attached roles."""

    DATA = "data"                          # get_res can return bytes
    NAMES_PLAINTEXT = "names_plaintext"    # some entries carry real names
    NAMES_RESID32 = "names_resid32"        # entries carry name hashes
    DECODED_SIZE = "decoded_size"          # decoded_size is trustworthy


class CompanionState(StrEnum):
    """How one declared companion resolved during folding.

    The UI keys its messaging off these states; ``Accepted.notes`` carries only
    debug detail.  FOLDED puts the file in ``consumed``; the other states leave
    it out (a REJECTED file stays available for other modules to consider).
    The set may grow; consumers must fall back to a generic incomplete display
    for states they do not know.
    """

    FOLDED = "folded"      # exists and passes the module's matches()
    ABSENT = "absent"      # no such file
    REJECTED = "rejected"  # exists, but the module's matches() declined it
    TAKEN = "taken"        # an earlier Accepted already consumed it


class Want:
    """Pool expansion: a glob the driver should resolve and add to the pool.

    Validation lives in construction: an ill-formed Want cannot exist.  The
    class is read-only (no setters), and ``pattern`` is given as a regex
    string or a pre-compiled pattern but always stored compiled, so consumers
    see only ``re.Pattern | None``.

    ``rel`` is a fnmatch-style glob relative to the entry file's directory;
    ``..`` is legal only as a leading run of any depth (anything above the
    anchor routes through ask), a ``..`` anywhere later raises
    :class:`ProtocolError`.  A bad regex raises :class:`ProtocolError`.  The
    driver fullmatches ``pattern`` against each candidate's basename,
    narrowing a deliberately loose glob without touching the path.

    ``child_ask`` is an optional read-only query the declaring module offers
    about itself.  When the driver offers a file this want pulled to a
    module, it passes ``(declaring format, child_ask)`` as that module's
    ``want_ask``; a child that verifies itself against the declarer calls it
    before claiming.  ``None`` means the declarer offers no query, and what
    a child does then is the child's own policy: one whose identity depends
    on the declarer's confirmation declines, one whose does not claims on
    its own bytes alone.
    """

    __slots__ = ("role", "rel", "max_files", "pattern", "child_ask")

    role: Role
    rel: str
    max_files: int
    pattern: "re.Pattern[str] | None"
    child_ask: "Ask | None"

    def __init__(
        self,
        role: Role,
        rel: str,
        max_files: int = 32,
        pattern: "str | re.Pattern[str] | None" = None,
        child_ask: "Ask | None" = None,
    ) -> None:
        if not want_rel_is_valid(rel):
            raise ProtocolError(
                f"Want rel {rel!r} has '..' outside its leading run; "
                f"only a contiguous prefix of '..' hops is allowed."
            )
        if isinstance(pattern, str):
            try:
                pattern = re.compile(pattern)
            except re.error as exc:
                raise ProtocolError(
                    f"Want pattern {pattern!r} is not a valid regex: {exc}"
                ) from exc
        if child_ask is not None and not callable(child_ask):
            raise ProtocolError(
                f"Want child_ask must be callable or None, "
                f"not {type(child_ask).__name__}"
            )
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "rel", rel)
        object.__setattr__(self, "max_files", max_files)
        object.__setattr__(self, "pattern", pattern)
        object.__setattr__(self, "child_ask", child_ask)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"Want is read-only: cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"Want is read-only: cannot delete {name!r}")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Want):
            return NotImplemented
        return (
            self.role == other.role
            and self.rel == other.rel
            and self.max_files == other.max_files
            and self.pattern == other.pattern
            and self.child_ask == other.child_ask
        )

    def __hash__(self) -> int:
        return hash(
            (self.role, self.rel, self.max_files, self.pattern, self.child_ask)
        )

    def __repr__(self) -> str:
        pat = self.pattern.pattern if self.pattern is not None else None
        return (
            f"Want(role={self.role!r}, rel={self.rel!r}, "
            f"max_files={self.max_files!r}, pattern={pat!r}, "
            f"child_ask={self.child_ask!r})"
        )


class ParentPref(NamedTuple):
    """Attach target: the shape of a parent this child would belong to.

    Never expands the pool and never needs to exist; attach resolves these in
    order against claims actually made this scan.  The stem is derived from the
    child's own name, which keeps the match exact without consulting the
    filesystem.  ``role`` names the parent's job; ``None`` targets a
    self-sufficient parent that performs no role at all.
    """

    role: Role | None
    rel: str


class Claim(NamedTuple):
    """What ``matches()`` returns: a declaration about the one file it saw.

    A claim carries exactly one file's identity plus what it would like nearby.
    It holds no existence verdicts and no resolved objects -- the driver turns
    it into an :class:`Accepted`.

    ``private`` is a format-private pre-parse cache (e.g. a decoded name
    list) that the driver never touches: the owning module fills it in at
    ``matches`` time and the parent that pulled this file consumes it from
    the unit at ``load`` time; the two modules agree on its shape.  A
    :class:`Want` may additionally expose a read-only query over the
    declarer via ``child_ask``.  Defaults to ``None``.
    """

    format: str
    entry: str                       # the file this claim is about (absolute)
    role: Role | None                # job performed when attached to a parent
    key: int | None                  # wpk: pkg_id parsed from its own header
    companions: tuple[str, ...]      # same-format siblings this module reads
                                     #   itself: declared blind via
                                     #   companion_for; the driver folds in
                                     #   every declared sibling that exists and
                                     #   passes the module's own matches(),
                                     #   transitively through those claims'
                                     #   companions (a closure); each declared
                                     #   sibling is recorded in
                                     #   Accepted.companion_states
    parents: tuple[ParentPref, ...]  # ordered preference, may all be absent
    wants: tuple[Want, ...]          # pool-expansion globs
    private: Any = None              # format-private pre-parse cache; the
                                     #   driver never reads it, the owning
                                     #   format casts it, Want.child_ask may
                                     #   expose a read-only query over it


class Accepted(NamedTuple):
    """The driver's verdict on a Claim: the files actually consumed.

    ``consumed`` holds the entry file plus one :class:`PackageFile` per
    companion the driver folded in.  Folding is a closure: each declared
    sibling that exists is offered to the claiming module's own ``matches()``,
    and a pass folds it in -- along with the companions its claim declares --
    until the closure converges.  Membership is the format's verdict, not the
    symmetry of declarations; ``companion_for`` is name-derived and symmetric,
    so a well-formed module's closure is the same group from any seed.

    ``companion_states`` records one :class:`CompanionState` per companion
    path the closure declared -- the structured fact the UI presents; the
    ``notes`` carry only the debug prose behind it.  ``complete`` is derived:
    every declared companion folded in.  Role, key, parents and wants come
    from the entry claim; companion claims contribute only their pass and
    their companion declarations.  Whether an incomplete group is survivable
    (open degraded, get_res reports the absence) or fatal (load raises
    IncompleteUnit) is a per-format policy decided in ``load()``, where the
    states and notes are visible.
    """

    claim: Claim
    consumed: tuple[PackageFile, ...]
    companion_states: Mapping[str, CompanionState]
    notes: tuple[str, ...]

    @property
    def complete(self) -> bool:
        """True when every companion the closure declared folded in."""
        return all(
            state is CompanionState.FOLDED
            for state in self.companion_states.values()
        )

    @property
    def paths(self) -> tuple[str, ...]:
        """Every file this record consumed: entry first, then folded siblings."""
        return tuple(package_file.path for package_file in self.consumed)

    def file_at(self, path: str) -> PackageFile | None:
        """The authorised object for ``path``, if this record consumed it.

        ``None`` means the file is not part of this unit -- cross-check
        ``companion_states`` for why (absent, rejected, or taken).  The path
        is a lookup key into objects already held, never an IO request.
        """
        wanted = os.path.normcase(path)
        for package_file in self.consumed:
            if package_file.path == wanted:
                return package_file
        return None


class Partner(NamedTuple):
    """A child as its parent sees it, synthesised by attach from an Accepted.

    ``paths`` is the child's consumed set: entry first, then its companions.
    Order carries no semantics beyond entry-first, so a consuming module tells
    multi-half files apart from their own headers (thd: type number and file
    seed), keeping half identity inside the owning format.
    """

    role: Role
    key: int | None
    paths: tuple[PackageFile, ...]


class Unit(NamedTuple):
    """attach() result: one root Accepted plus the children hung on it.

    A unit's full physical footprint is ``root.consumed`` plus every child's
    consumed set; companions are already inside those tuples, so nothing else
    has to be threaded through to reach a file.
    """

    root: Accepted
    children: tuple[Accepted, ...]

    def partners_for(self, role: Role) -> tuple[Partner, ...]:
        """Children with the given role, projected as parent-facing views."""
        return tuple(
            Partner(child.claim.role, child.claim.key, child.consumed)
            for child in self.children
            if child.claim.role == role
        )

    def owner_of(self, path: str) -> str | None:
        """The format that consumed ``path`` within this unit, or ``None``."""
        wanted = os.path.normcase(path)
        for accepted in (self.root, *self.children):
            for package_file in accepted.consumed:
                if package_file.path == wanted:
                    return accepted.claim.format
        return None

    def file_at(self, path: str) -> PackageFile | None:
        """The authorised object for ``path`` anywhere in this unit.

        Searches the root and every child's consumed set; ``None`` means no
        record of this unit holds it.  Lookup key, not an IO request -- the
        returned object is the same grant the driver handed the records.
        """
        wanted = os.path.normcase(path)
        for accepted in (self.root, *self.children):
            found = accepted.file_at(wanted)
            if found is not None:
                return found
        return None

    @property
    def all_paths(self) -> tuple[str, ...]:
        """Every physical path in the footprint (identity/dedup, not IO grant)."""
        return tuple(
            package_file.path
            for accepted in (self.root, *self.children)
            for package_file in accepted.consumed
        )

    @property
    def complete(self) -> bool:
        """True only when the root and every child met all their companions."""
        if not self.root.complete:
            return False
        return all(child.complete for child in self.children)


class PackageFile(Protocol):
    """One file the driver has authorised a module to touch.

    ``path`` is the full absolute, normcased path: modules derive companions,
    stems for wants/parents, and the driver's dedup key from it.  Seeing a path
    grants nothing -- no verb takes a path, so no API can consume a neighbouring
    one.  The same object serves ``matches()`` and ``load()``/``get_res()``; a
    later implementation can hand out the identical type from inside a zip
    without extraction and without any caller change.

    ``matches()`` stays on ``read_head()``; the whole-file verbs serve
    ``load()``, which reads the small index tables it was handed.

    Thread-safety contract: one object may be shared across a parallel
    matches pass, so the cache-backed verbs (``read_head``, ``size``) must be
    safe to call concurrently (a per-instance lock is enough).  ``open()``
    returns an independent handle per call with its own position; concurrent
    read-only handles on one file never interfere, so no global read lock is
    wanted -- serialising reads would only slow parallel previews down.
    """

    path: str                                  # absolute, normcased
    def read_head(self, n: int) -> bytes: ...  # cached across modules
    def size(self) -> int: ...
    def open(self) -> Seekable: ...            # caller closes (with)


class EntryInfo(NamedTuple):
    """Neutral, presentation-facing metadata for one entry.

    Storage coordinates (offset/length inside a physical file) stay private to
    the Archive; callers address content through ``get_res(key)``.
    """

    key: int
    name: str | None
    name_source: NameSource
    signature: int | None          # 32-bit name hash, when the format has one
    content_hash: bytes | None     # 16 bytes, when the format has one
    ext_hint: str | None           # from the NAME only, never from content sniffing
    stored_size: int
    decoded_size: int | None
    container: str                 # physical file holding the payload (a member)
    status: Status
    extra: Mapping[str, Any]       # format-private: pkg_id/zip_flag/encrypt_mode/...


@dataclass
class ReadOptions:
    """Neutral options; each format subclasses it and declares OPTIONS_TYPE."""

    name_map: Mapping[int, str] | None = None   # entry signature -> display name


class EntryStream(Seekable, Protocol):
    """Seekable view of one entry's bytes (decoded, or stored when requested).

    A :class:`Seekable`, and ``Seekable`` is a member of the processors
    ``FileLike`` union, so ``processors.read(archive.get_res(key))`` passes with
    no adapter and no stream pretends to be writable.  The current build
    materialises the whole entry on open; it will upgrade to
    fixed-window on-demand decoding behind this same contract, so callers rely
    only on read/seek/tell/close/closed.
    """

    ...


class Archive(Protocol):
    """A loaded container session: index in RAM, payload reached by possession.

    Each format implements this as a concrete class -- explicitly inheriting
    the Protocol so mypy checks the shape at definition time -- and its
    ``load()`` returns an instance.  The Archive is the ONLY place a format
    keeps per-session data: its tables, physical-handle LRU, and caches live
    in the instance and are released on ``close()``.  Container modules stay
    stateless -- they outlive every session in ``sys.modules``, so
    module-level mutable data would leak past ``close()`` and race when two
    scans run concurrently.

    One session may be read from several threads (preview while a scan
    runs), so an implementation guards its own mutable state -- the handle
    LRU, caches -- with an internal lock; ``get_res`` returns a stream the
    calling thread owns outright.

    Implementations keep one ordered list plus lookup dicts, built once at load
    and never mutated: position == key (INDEX), a name dict, a signature dict,
    a content-hash dict.  All four reference kinds resolve to the same key, then
    the same storage lookup, then the same decode path.  Payload is reached only
    through the :class:`PackageFile` objects the unit was handed.
    """

    format: str
    path: str                                # entry file
    members: tuple[str, ...]                 # every physical file consumed
    count: int                               # entries enumerated by default (no DELETED)
    capabilities: frozenset[Capability]      # pure function of attached roles

    @property
    def closed(self) -> bool:
        """True once :meth:`close` has run; the session is finished."""
        ...

    def entries(self, *, include_deleted: bool = False) -> Iterator[EntryInfo]: ...

    def get_res(
        self, ref: int | str | bytes, kind: RefKind = RefKind.INDEX,
        *, decoded: bool = True,
    ) -> EntryStream:
        """Open one entry's content; the caller owns the stream.

        ``kind`` selects what ``ref`` means; a format that cannot resolve that
        kind raises :class:`UnsupportedRef`.  MISSING raises
        :class:`MissingIndexedResourceError` (the payload is not here) and
        CORRUPTED raises :class:`PayloadDecodeError` with reason ``corrupt``
        (the payload is here but contradicts the index that named it -- the
        store is truncated under the block, or the block header's hash or sizes
        disagree with the record); both surface at the call, never as a silent
        short read.  A corrupted entry is a content fault, so it groups with
        the unreadable entries rather than the missing ones.

        ``decoded`` selects the content variant.  ``True`` (default) applies the
        full pipeline -- decryption, decompression, and wrapper stripping -- and
        hands back the entry's decoded bytes.  ``False`` skips decryption and
        decompression entirely and hands back the stored bytes exactly as they
        sit in the container, so a caller can inspect what is on disk next to
        the decoded form.

        A ``decoded=True`` read promises decoded content, so it raises
        :class:`PayloadDecodeError` when the pipeline cannot deliver it: a
        withheld option (an ENCRYPTED entry read without its key), an algorithm
        this build does not recognise, a corrupt payload, or a missing codec
        dependency -- the error's ``reason`` field says which.  It never
        returns undecoded bytes that would pass themselves off as plaintext.
        :class:`EntryInfo.status` is a display hint for the entry list, never a
        licence to serve ciphertext as content -- read with ``decoded=False``
        for the stored bytes instead.  A container's load path raises
        :class:`IntegrityMismatch` or :class:`VersionUnsupported` for a store
        whose header contradicts its filename or index, or names a version this
        build cannot read; those are load-time faults, not per-entry ones.

        A closed archive resolves nothing: reading it raises ``ValueError``.
        Check :attr:`closed` first when the caller may hold a stale reference.
        """
        ...

    def close(self) -> None: ...
    def __enter__(self) -> "Archive": ...
    def __exit__(self, *exc: object) -> None: ...


# --- base.py sanctioned helpers: the only places these rules live ---

GLOB_METACHARS = "*?[]"


def want_rel_is_valid(rel: str) -> bool:
    """True when ``..`` appears only as a contiguous leading run (any depth).

    The single expression of the want-glob position rule; :class:`Want`'
    constructor enforces it, so an ill-formed want is unconstructable rather
    than merely refused.
    """
    parts = rel.split("/")
    index = 0
    while index < len(parts) and parts[index] == "..":
        index += 1
    return not any(part == ".." for part in parts[index:])


def companion_is_exact(companion_path: str, entry_path: str) -> bool:
    """True when a companion is a plain same-directory sibling of the entry.

    Rejects glob metacharacters, any ``..`` hop, and a different directory, so
    a companion can never smuggle a search or reach across into another file.
    """
    if any(ch in companion_path for ch in GLOB_METACHARS):
        return False
    if ".." in companion_path.replace("\\", "/").split("/"):
        return False
    return os.path.dirname(companion_path) == os.path.dirname(entry_path)


def companion_for(path: str, ext: str) -> str:
    """Exact same-directory sibling with a new extension (x.thh -> x.thy).

    The only sanctioned way to declare a companion, so the pairing rule stays
    inside the declaring module.  ``ext`` must be a bare extension: any glob
    metacharacter, path separator, or ``..`` is a :class:`ProtocolError`.  The
    result is symmetric, so either half as seed yields the same unit.
    """
    if any(ch in ext for ch in GLOB_METACHARS) or "/" in ext or "\\" in ext or ".." in ext:
        raise ProtocolError(
            f"companion_for ext {ext!r} must be a bare extension (no glob, "
            f"separator, or '..')."
        )
    if not ext.startswith("."):
        ext = "." + ext
    directory = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    return os.path.join(directory, stem + ext)
