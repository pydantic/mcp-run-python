from __future__ import annotations as _annotations

from importlib.metadata import version as _metadata_version

from .main import deno_args, run_deno_server

__version__ = _metadata_version('mcp_run_python')
__all__ = '__version__', 'deno_args', 'run_deno_server'
