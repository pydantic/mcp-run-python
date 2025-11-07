import asyncio
import logging
import shutil
import subprocess
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
Mode = Literal['stdio', 'streamable_http', 'example']
LogHandler = Callable[[LoggingLevel, str], None]


def run_mcp_server(
    mode: Mode,
    *,
    http_port: int | None = None,
    dependencies: list[str] | None = None,
    return_mode: Literal['json', 'xml'] = 'xml',
    deps_log_handler: LogHandler | None = None,
    allow_networking: bool = True,
    enable_file_outputs: bool = False,
    pyodide_max_workers: int = 10,
    pyodide_worker_wait_timeout_sec: int = 60,
    pyodide_code_run_timeout_sec: int = 60,
) -> int:
    """Install dependencies then run the mcp-run-python server.

    Args:
        mode: The mode to run the server in.
        http_port: The port to run the server on if mode is `streamable_http`.
        dependencies: The dependencies to install.
        return_mode: The mode to return tool results in.
        deps_log_handler: Optional function to receive logs emitted while installing dependencies.
        allow_networking: Whether to allow networking when running provided python code.
        enable_file_outputs: Whether to enable output files
        pyodide_max_workers: How many pyodide workers to max use at the same time
        pyodide_code_run_timeout_sec: How long to wait for pyodide code to run in seconds.
        pyodide_worker_wait_timeout_sec: How long to wait for a free pyodide worker in seconds.
    """
    with prepare_deno_env(
        mode,
        dependencies=dependencies,
        http_port=http_port,
        return_mode=return_mode,
        deps_log_handler=deps_log_handler,
        allow_networking=allow_networking,
        enable_file_outputs=enable_file_outputs,
        pyodide_max_workers=pyodide_max_workers,
        pyodide_worker_wait_timeout_sec=pyodide_worker_wait_timeout_sec,
        pyodide_code_run_timeout_sec=pyodide_code_run_timeout_sec,
    ) as env:
        logger.info(f'Running with file output support {"enabled" if enable_file_outputs else "disabled"}.')
        if mode == 'streamable_http':
            logger.info('Running mcp-run-python via %s on port %d...', mode, str(http_port))
        else:
            logger.info('Running mcp-run-python via %s...', mode)

        try:
            p = subprocess.run(('deno', *env.args), cwd=env.cwd)
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
    enable_file_outputs: bool = False,
    pyodide_max_workers: int = 10,
    pyodide_worker_wait_timeout_sec: int = 60,
    pyodide_code_run_timeout_sec: int = 60,
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
        enable_file_outputs: Whether to enable output files
        pyodide_max_workers: How many pyodide workers to max use at the same time
        pyodide_code_run_timeout_sec: How long to wait for pyodide code to run in seconds.
        pyodide_worker_wait_timeout_sec: How long to wait for a free pyodide worker in seconds.

    Returns:
        Yields the deno environment details.
    """
    cwd = Path(tempfile.mkdtemp()) / 'mcp-run-python'
    try:
        src = Path(__file__).parent / 'deno'
        logger.debug('Copying from %s to %s...', src, cwd)
        shutil.copytree(src, cwd)
        (cwd / 'output_files').mkdir()
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
            enable_file_outputs=enable_file_outputs,
            pyodide_max_workers=pyodide_max_workers,
            pyodide_worker_wait_timeout_sec=pyodide_worker_wait_timeout_sec,
            pyodide_code_run_timeout_sec=pyodide_code_run_timeout_sec,
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
    enable_file_outputs: bool = False,
    pyodide_max_workers: int = 10,
    pyodide_worker_wait_timeout_sec: int = 60,
    pyodide_code_run_timeout_sec: int = 60,
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
        enable_file_outputs=enable_file_outputs,
        pyodide_max_workers=pyodide_max_workers,
        pyodide_worker_wait_timeout_sec=pyodide_worker_wait_timeout_sec,
        pyodide_code_run_timeout_sec=pyodide_code_run_timeout_sec,
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
    enable_file_outputs: bool = False,
    pyodide_max_workers: int = 10,
    pyodide_worker_wait_timeout_sec: int = 60,
    pyodide_code_run_timeout_sec: int = 60,
) -> list[str]:
    args = ['run']
    if allow_networking:
        args += ['--allow-net']
    args += [
        '--allow-read=./node_modules,./output_files',
        '--allow-write=./output_files',
        '--node-modules-dir=auto',
        'src/main.ts',
        mode,
        f'--return-mode={return_mode}',
        f'--pyodide-max-workers={pyodide_max_workers}',
        f'--pyodide-worker-wait-timeout-sec={pyodide_worker_wait_timeout_sec}',
        f'--pyodide-code-run-timeout-sec={pyodide_code_run_timeout_sec}',
    ]
    if enable_file_outputs:
        args += ['--enable-file-outputs']
    if dependencies is not None:
        args.append(f'--deps={",".join(dependencies)}')
    if http_port is not None:
        if mode == 'streamable_http':
            args.append(f'--port={http_port}')
        else:
            raise ValueError('Port is only supported for `streamable_http` mode')
    return args


P = ParamSpec('P')
T = TypeVar('T')


async def _asyncify(func: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    return await asyncio.get_event_loop().run_in_executor(None, partial(func, *args, **kwargs))
