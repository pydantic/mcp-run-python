import logging
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Literal

Mode = Literal['stdio', 'streamable_http', 'example']
THIS_DIR = Path(__file__).parent
NODE_MODULES = THIS_DIR / 'node_modules'
logger = logging.getLogger(__name__)


def deno_run_server(
    mode: Mode,
    *,
    port: int | None = None,
    deps: list[str] | None = None,
    prep_log_handler: Callable[[str], None] | None = None,
):
    deno_install_deps(deps, prep_log_handler)
    logger.info('Running mcp-run-python server...')
    try:
        subprocess.run(('deno', *deno_run_args(mode, port=port, deps=deps)), cwd=THIS_DIR)
    except KeyboardInterrupt:  # pragma: no cover
        logger.warning('Server stopped.')


def deno_args_prepare(
    mode: Mode,
    *,
    port: int | None = None,
    deps: list[str] | None = None,
    return_mode: Literal['json', 'xml'] = 'xml',
    prep_log_handler: Callable[[str], None] | None = None,
) -> list[str]:
    deno_install_deps(deps, prep_log_handler)
    return deno_run_args(mode, port=port, deps=deps, return_mode=return_mode)


def deno_install_deps(
    deps: list[str] | None = None,
    prep_log_handler: Callable[[str], None] | None = None,
):
    if NODE_MODULES.exists():
        logger.debug('Deleting existing dependencies in node_modules...')
        shutil.rmtree(NODE_MODULES)

    logger.debug('Installing dependencies %s...', deps)
    args = 'deno', *deno_install_args(deps)
    p = subprocess.run(args, cwd=THIS_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if prep_log_handler is not None:
        prep_log_handler(p.stdout.decode().strip())


def deno_install_args(deps: list[str] | None = None) -> list[str]:
    args = [
        'run',
        '-N',
        f'-R={NODE_MODULES}',
        f'-W={NODE_MODULES}',
        '--node-modules-dir=auto',
        str(THIS_DIR / 'deno/main.ts'),
        'noop',
    ]
    if deps is not None:
        args.append(f'--deps={",".join(deps)}')
    return args


def deno_run_args(
    mode: Mode,
    *,
    port: int | None = None,
    deps: list[str] | None = None,
    return_mode: Literal['json', 'xml'] = 'xml',
) -> list[str]:
    args = [
        'run',
        '-N',
        f'-R={NODE_MODULES}',
        '--node-modules-dir=auto',
        str(THIS_DIR / 'deno/main.ts'),
        mode,
        f'--return-mode={return_mode}',
    ]
    if deps is not None:
        args.append(f'--deps={",".join(deps)}')
    if port is not None:
        if mode == 'streamable_http':
            args.append(f'--port={port}')
        else:
            raise ValueError('Port is only supported for `streamable_http` mode')
    return args
