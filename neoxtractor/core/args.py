"""Argument management module."""

import argparse

from neoxtractor.core.build_info import BuildInfo

parser = argparse.ArgumentParser(description="NeoXtractor")
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

_subparsers = parser.add_subparsers(help="subcommand help", dest="subcommand")

gui_parser = _subparsers.add_parser("gui", help="Run the NeoXtractor GUI")

arguments = argparse.Namespace()


def parse_args():
    """Parse arguments."""
    parser.parse_args(namespace=arguments)
