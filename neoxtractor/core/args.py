"""Common command-line argument definitions.

Only options that apply to every launch mode live here (the version stamp
and the log level).  Launch modes are subcommands registered by the
package entry point :mod:`neoxtractor.__main__`, which imports this
module -- never the other way round, so core keeps no knowledge of the
GUI.
"""

from __future__ import annotations

import argparse

from neoxtractor.core.build_info import BuildInfo


def build_base_parser() -> argparse.ArgumentParser:
    """Create the top-level parser carrying the common options only.

    A fresh parser per call: importing has no side effects and each
    caller (including tests) parses its own argv with no shared state.
    Subcommands are added by the entry point, not here.
    """
    parser = argparse.ArgumentParser(
        prog="neoxtractor",
        description="NeoXtractor",
    )
    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version=f"{BuildInfo.version if BuildInfo.version else 'development'} (Build: {
            BuildInfo.commit_hash[:7] if BuildInfo.commit_hash else 'unknown'
        })",
    )
    parser.add_argument(
        "--log-level",
        help="Set the logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL or integer values)",
        dest="log_level",
    )
    return parser
