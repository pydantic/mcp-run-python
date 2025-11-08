from __future__ import annotations as _annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from . import __version__
from .main import LoggingLevel, run_mcp_server


def cli():
    sys.exit(cli_logic())


def cli_logic(args_list: Sequence[str] | None = None) -> int:
    """Run the CLI."""
    parser = argparse.ArgumentParser(
        prog='mcp-run-python',
        description=f'mcp-run-python CLI v{__version__}\n\nMCP server for running untrusted Python code.\n',
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument('--port', type=int, help='Port to run the server on, default 3001.')
    parser.add_argument('--deps', '--dependencies', help='Comma separated list of dependencies to install')
    parser.add_argument(
        '--disable-networking', action='store_true', help='Disable networking during execution of python code'
    )
    parser.add_argument('--verbose', action='store_true', help='Enable verbose logging')
    parser.add_argument('--enable-file-outputs', action='store_true', help='Enable file output functionality')
    parser.add_argument(
        '--pyodide-max-workers',
        help='How many pyodide workers should be spawned at a time max. This is the amount of concurrent function executions you can have. Default: 10',
        default=10,
        type=int,
    )
    parser.add_argument(
        '--pyodide-code-run-timeout-sec',
        help='How long the code execution is allowed to last. Default: 60 seconds',
        default=60,
        type=int,
    )
    parser.add_argument(
        '--pyodide-worker-wait-timeout-sec',
        help='How many long pyodide should wait for a free worker. Default: 60 seconds',
        default=60,
        type=int,
    )
    parser.add_argument('--version', action='store_true', help='Show version and exit')
    parser.add_argument(
        'mode',
        choices=['stdio', 'streamable-http', 'example'],
        nargs='?',
        help='Mode to run the server in.',
    )

    args = parser.parse_args(args_list)
    if args.version:
        print(f'mcp-run-python {__version__}')
        return 0
    elif args.mode:
        logging.basicConfig(
            level=logging.DEBUG if args.verbose else logging.INFO,
            stream=sys.stderr,
            format='%(message)s',
        )

        deps: list[str] = args.deps.split(',') if args.deps else []
        return_code = run_mcp_server(
            args.mode.replace('-', '_'),
            allow_networking=not args.disable_networking,
            http_port=args.port,
            dependencies=deps,
            deps_log_handler=deps_log_handler,
            enable_file_outputs=args.enable_file_outputs,
            pyodide_max_workers=args.pyodide_max_workers,
            pyodide_worker_wait_timeout_sec=args.pyodide_worker_wait_timeout_sec,
            pyodide_code_run_timeout_sec=args.pyodide_code_run_timeout_sec,
        )
        return return_code
    else:
        parser.error('Mode is required')


logger = logging.getLogger('mcp-run-python-install')


def deps_log_handler(level: LoggingLevel, msg: str):
    if level == 'debug':
        logger.debug('install: %s', msg)
    elif level == 'info':
        logger.info('install: %s', msg)
    else:
        logger.warning('install: %s', msg)
