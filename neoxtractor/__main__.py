"""Package entry point: ``python -m neoxtractor`` (and the root ``main.py``).

The command line is parsed in two ``parse_known_args`` passes:

    neoxtractor --log-level DEBUG gui -style fusion --log-level INFO
                ^^^^^^^^^^^^^^^^^^^^^ ^^^          ^^^^^^^^^^^^^^^^^
                pass 1: subcommand    pass 2:      leftovers, in order,
                and globals anywhere  globals      go to the subcommand

Pass 1 (full parser) extracts the subcommand and any global before it;
pass 2 (base parser) extracts globals from the remainder at any
position, its last-one-wins rule overriding pass 1.  The tail keeps its
original order and is handed to the subcommand's ``main(argv)`` as a
``sys.argv``-style list -- program name first, stripped by the
subcommand.  Unrecognised tokens are the subcommand's problem to report,
which keeps Qt's single-dash options working and every launch mode on
one pipeline.
"""

from __future__ import annotations

import argparse
import sys

from neoxtractor.core.args import build_base_parser
from neoxtractor.core.utils.logger import setup_logger

# Known subcommands and their help.  Each name needs a dispatch branch in
# main(); the table is the single source for the parser's subcommands.
_SUBCOMMAND_HELP = {
    "gui": "Run the NeoXtractor GUI (unrecognised arguments go to Qt)",
}


def build_parser() -> argparse.ArgumentParser:
    """The full command line: common options plus the launch subcommands."""
    parser = build_base_parser()
    sub = parser.add_subparsers(dest="subcommand", help="subcommand help")
    for name, help_text in _SUBCOMMAND_HELP.items():
        sub.add_parser(name, help=help_text)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse the common options and dispatch the requested launch mode."""
    if argv is None:
        argv = list(sys.argv[1:])
    prog = sys.argv[0] if sys.argv else "neoxtractor"

    ns, rest = build_parser().parse_known_args(argv)
    # Pass 2: globals from the remainder at any position; last-one-wins
    # makes it override pass 1.
    ns_rest, tail = build_base_parser().parse_known_args(rest)
    log_level = getattr(ns_rest, "log_level", None) or ns.log_level
    setup_logger(log_level)
    subcommand = ns.subcommand
    if subcommand in (None, "gui"):
        # Imported only once the GUI is actually requested: ``-v`` and
        # ``--log-level`` must not drag PySide6 into the process.
        from neoxtractor.gui import main as run_gui

        return run_gui([prog] + tail)
    # Defensive: the parser rejects unknown subcommand names before
    # dispatch, so only the gui branch is reachable.
    raise SystemExit(f"unhandled subcommand: {subcommand}")


if __name__ == "__main__":
    raise SystemExit(main())
