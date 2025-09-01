from __future__ import annotations as _annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__
from .main import deno_run_server


def cli() -> int:  # pragma: no cover
    """Run the CLI."""
    sys.exit(cli_logic())


def cli_logic(args_list: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='mcp-run-python',
        description=f'mcp-run-python CLI v{__version__}\n\nMCP server for running untrusted Python code.\n',
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument('--version', action='store_true', help='Show version and exit')

    parser.add_argument('--port', type=int, help='Port to run the server on, default 3001.')
    parser.add_argument('--deps', help='Comma separated list of dependencies to install')
    parser.add_argument(
        'mode',
        choices=['stdio', 'streamable-http', 'example'],
        help='Mode to run the server in.',
    )

    args = parser.parse_args(args_list)
    if args.version:
        print(f'mcp-run-python {__version__}')
        return 0
    else:
        deps: list[str] = args.deps.split(',') if args.deps else []
        deno_run_server(args.mode.replace('-', '_'), port=args.port, deps=deps, install_log_handler=print)
        return 0
