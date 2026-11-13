Place custom format-processor plugins in this folder.

A plugin is a single importable file -- .py source, .pyc bytecode, or a
compiled extension (.pyd on Windows, .so on Linux, .dylib on macOS) -- that
follows the same protocol as the built-in resource modules under
neoxtractor/core/resources/.  There is no base class to inherit.  The
accepted types come from the running interpreter's import system, so a
source plugin works on every platform while a compiled one must match the
interpreter version/architecture.

Required module-level attributes:

    FORMAT = "myformat"        # unique registry key (lowercase recommended)
    def probe(f) -> ProbeResult: ...
        # f is a seekable binary stream (read/seek/tell, seek supports
        # io.SEEK_END) or plain bytes.  Return
        # neoxtractor.core.utils.result.ProbeResult(score, ext, category):
        #   score    0..100  (100 exact magic, 50 strong heuristic,
        #                     10 weak guess, 0 = not my format)
        #   ext      guessed extension without a leading dot ("" if unknown)
        #   category neoxtractor.core.utils.result.Category value (or "")
        # probe must only read a bounded header and must never raise.

Optional attributes (all discovered with getattr, declare what you need):

    def read(f):               # decode to the canonical object; return None
        ...                    # when not this format, raise when corrupt
    def convert(canonical, target_name):  # canonical -> bytes/str/ndarray
        ...
    TARGETS = ("png", "np_rgb")  # str or Target(name, kind, ext, desc)
    CANONICAL_TYPE = "PIL.Image.Image"  # display hint for UIs

Value-object imports (from the package, available to any plugin):

    from neoxtractor.core.utils.result import ProbeResult, Category
    from neoxtractor.core.utils.target import Target
    from neoxtractor.core.utils.filelike import read_head, read_all

    # Target(name, kind="file"|"view", ext, desc) describes one convert()
    # output.  Bare strings in TARGETS are accepted too (a facade helper
    # turns them into kind="file", ext="." + name), so you only need Target
    # when you want a view target or a custom extension/label.
    # Category is a str enum: TEXTURE, MESH, BXML, BANK, CSB, CHARACTER,
    # SKIN, OTHER ("" when you cannot classify).

Third-party plugin scores are weighted as raw*2+1 and built-in scores as
raw*2, so a plugin wins a confidence tie against a built-in.

Loading channels:

- Automatic: this directory is scanned (non-recursive; subfolders such as
  __pycache__ are not walked) on first use of the processors facade.  Every
  file whose type the interpreter can import is attempted; anything else
  (README.txt, a .dll that is not a Python extension, a stray .json) is
  skipped without an attempt, and a file that fails to import or does not
  satisfy the protocol is logged and skipped.  Set the environment variable
  NEOXTRACTOR_PLUGIN_DIR to scan another directory first.
- Manual refresh: neoxtractor.core.processors.reload_environment() re-runs
  the exact same scan on demand (a UI "refresh" button).

Both scan channels are NON-OVERRIDING: a scanned plugin whose FORMAT is
already registered (by a built-in or an earlier plugin) is skipped with a
warning and never replaces the incumbent.  Only the explicit load below,
with override=True, may replace an existing FORMAT.

- Explicit (UI/CLI): neoxtractor.core.processors.load_plugin(path) loads
  one file immediately and reports precise errors; unload_plugin(FORMAT)
  removes it again (add override=True to replace a built-in of the same
  FORMAT name).

Note: .py/.pyc plugin files are executed fresh on every load and are never
added to sys.modules, so editing the file and reloading picks up the new
content.  Compiled extensions cannot be hot-reloaded (the OS keeps them
mapped for the life of the process), so replacing a .pyd/.so/.dylib requires
restarting the application.  Classes defined in a source plugin are not
importable by name from outside the module.

State discipline: probe/read/convert must operate only on the file-like they
are handed and return fresh objects.  Do not keep mutable data at module
level -- scans can run concurrently and caches keyed at module scope leak
across runs.  Immutable constants (magic tables, lists) are fine anywhere.

See _example_plugin.py in this folder for a minimal working plugin.  It is
underscore-prefixed on purpose: the scanner skips "_" files, so the template
never registers itself -- copy it to a non-underscore name to activate.
