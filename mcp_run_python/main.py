import asyncio
import logging
import shutil
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Literal, ParamSpec, TypeVar, cast

__all__ = 'run_mcp_server', 'DenoEnv', 'prepare_deno_env', 'async_prepare_deno_env'

logger = logging.getLogger(__name__)
LoggingLevel = Literal['debug', 'info', 'notice', 'warning', 'error', 'critical', 'alert', 'emergency']
Mode = Literal['stdio', 'streamable_http', 'streamable_http_stateless', 'example']
LogHandler = Callable[[LoggingLevel, str], None]


def run_mcp_server(
    mode: Mode,
    *,
    http_port: int | None = None,
    dependencies: list[str] | None = None,
    return_mode: Literal['json', 'xml'] = 'xml',
    deps_log_handler: LogHandler | None = None,
    allow_networking: bool = True,
    verbose: bool = False,
) -> int:
    """Install dependencies then run the mcp-run-python server.

    Args:
        mode: The mode to run the server in.
        http_port: The port to run the server on if mode is `streamable_http`.
        dependencies: The dependencies to install.
        return_mode: The mode to return tool results in.
        deps_log_handler: Optional function to receive logs emitted while installing dependencies.
        allow_networking: Whether to allow networking when running provided python code.
        verbose: Log deno outputs to CLI
    """

    stdout, stderr = None, None
    if verbose:
        stdout, stderr = sys.stdout, sys.stderr

    with prepare_deno_env(
        mode,
        dependencies=dependencies,
        http_port=http_port,
        return_mode=return_mode,
        deps_log_handler=deps_log_handler,
        allow_networking=allow_networking,
    ) as env:
        if mode in ('streamable_http', 'streamable_http_stateless'):
            logger.info('Running mcp-run-python via %s on port %d...', mode, http_port)
        else:
            logger.info('Running mcp-run-python via %s...', mode)

        try:
            p = subprocess.run(('deno', *env.args), cwd=env.cwd, stdout=stdout, stderr=stderr)
        except KeyboardInterrupt:  # pragma: no cover
            logger.warning('Server stopped.')
            return 0
        else:
            return p.returncode


@dataclass
class DenoEnv:
    cwd: Path
    args: list[str]


@contextmanager
def prepare_deno_env(
    mode: Mode,
    *,
    http_port: int | None = None,
    dependencies: list[str] | None = None,
    return_mode: Literal['json', 'xml'] = 'xml',
    deps_log_handler: LogHandler | None = None,
    allow_networking: bool = True,
) -> Iterator[DenoEnv]:
    """Prepare the deno environment for running the mcp-run-python server with Deno.

    Copies deno files to a new directory and installs dependencies.

    Exiting the context manager will remove the temporary directory used for the deno environment.

    Args:
        mode: The mode to run the server in.
        http_port: The port to run the server on if mode is `streamable_http`.
        dependencies: The dependencies to install.
        return_mode: The mode to return tool results in.
        deps_log_handler: Optional function to receive logs emitted while installing dependencies.
        allow_networking: Whether the prepared DenoEnv should allow networking when running code.
            Note that we always allow networking during environment initialization to install dependencies.

    Returns:
        Yields the deno environment details.
    """
    cwd = Path(tempfile.mkdtemp()) / 'mcp-run-python'
    try:
        src = Path(__file__).parent / 'deno'
        logger.debug('Copying from %s to %s...', src, cwd)
        shutil.copytree(src, cwd)
        logger.info('Installing dependencies %s...', dependencies)

        args = 'deno', *_deno_install_args(dependencies)
        p = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        stdout: list[str] = []
        if p.stdout is not None:
            for line in p.stdout:
                line = line.strip()
                if deps_log_handler:
                    parts = line.split('|', 1)
                    level, msg = parts if len(parts) == 2 else ('info', line)
                    deps_log_handler(cast(LoggingLevel, level), msg)
                stdout.append(line)
        p.wait()
        if p.returncode != 0:
            raise RuntimeError(f'`deno run ...` returned a non-zero exit code {p.returncode}: {"".join(stdout)}')

        args = _deno_run_args(
            mode,
            http_port=http_port,
            dependencies=dependencies,
            return_mode=return_mode,
            allow_networking=allow_networking,
        )
        yield DenoEnv(cwd, args)

    finally:
        shutil.rmtree(cwd)


@asynccontextmanager
async def async_prepare_deno_env(
    mode: Mode,
    *,
    http_port: int | None = None,
    dependencies: list[str] | None = None,
    return_mode: Literal['json', 'xml'] = 'xml',
    deps_log_handler: LogHandler | None = None,
    allow_networking: bool = True,
) -> AsyncIterator[DenoEnv]:
    """Async variant of `prepare_deno_env`."""
    ct = await _asyncify(
        prepare_deno_env,
        mode,
        http_port=http_port,
        dependencies=dependencies,
        return_mode=return_mode,
        deps_log_handler=deps_log_handler,
        allow_networking=allow_networking,
    )
    try:
        yield await _asyncify(ct.__enter__)
    finally:
        await _asyncify(ct.__exit__, None, None, None)


def _deno_install_args(dependencies: list[str] | None = None) -> list[str]:
    args = [
        'run',
        '--allow-net',
        '--allow-read=./node_modules',
        '--allow-write=./node_modules',
        '--node-modules-dir=auto',
        'src/main.ts',
        'noop',
    ]
    if dependencies is not None:
        args.append(f'--deps={",".join(dependencies)}')
    return args


def _deno_run_args(
    mode: Mode,
    *,
    http_port: int | None = None,
    dependencies: list[str] | None = None,
    return_mode: Literal['json', 'xml'] = 'xml',
    allow_networking: bool = True,
) -> list[str]:
    args = ['run']
    if allow_networking:
        args += ['--allow-net']
    args += [
        '--allow-read=./node_modules',
        '--node-modules-dir=auto',
        'src/main.ts',
        mode,
        f'--return-mode={return_mode}',
    ]
    if dependencies is not None:
        args.append(f'--deps={",".join(dependencies)}')
    if http_port is not None:
        if mode in ('streamable_http', 'streamable_http_stateless'):
            args.append(f'--port={http_port}')
        else:
            raise ValueError('Port is only supported for `streamable_http` modes')
    return args


P = ParamSpec('P')
T = TypeVar('T')


async def _asyncify(func: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    return await asyncio.get_event_loop().run_in_executor(None, partial(func, *args, **kwargs))
