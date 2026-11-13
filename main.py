"""NeoXtractor launcher: a thin front for the package entry point.

All command-line handling lives in :mod:`neoxtractor.__main__`, so this
file and ``python -m neoxtractor`` start the identical program; a frozen
build analyses this script and follows the import to the same entry.
"""

from neoxtractor.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
