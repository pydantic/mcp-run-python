import subprocess
import sys
from pathlib import Path
from typing import Literal

Mode = Literal['stdio', 'streamable_http', 'warmup']
THIS_DIR = Path(__file__).parent


def run_deno_server(mode: Mode, *, port: int | None = None):
    print('Running mcp-run-python server...', file=sys.stderr)
    try:
        subprocess.run(('deno', *deno_args(mode, port=port)), cwd=THIS_DIR)
    except KeyboardInterrupt:
        print('Server stopped.', file=sys.stderr)


def deno_args(mode: Mode, *, port: int | None = None) -> list[str]:
    args = [
        'run',
        '-N',
        f'-R={THIS_DIR / "node_modules"}',
        f'-W={THIS_DIR / "node_modules"}',
        '--node-modules-dir=auto',
        str(THIS_DIR / 'deno/main.ts'),
        mode,
    ]
    if port is not None:
        args.append(f'--port={port}')
    return args
