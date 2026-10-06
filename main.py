"""NeoXtractor entrypoint"""

from neoxtractor.core.args import arguments, parse_args
from neoxtractor.core.logger import setup_logger
from neoxtractor.gui import run as run_gui

def run_cli():
    """Run NeoXtractor as a CLI application."""
    raise NotImplementedError("CLI mode is not implemented yet.")

if __name__ == "__main__":
    parse_args()
    setup_logger()
    if arguments.subcommand == "gui" or arguments.subcommand is None:
        run_gui()
