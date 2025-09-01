from __future__ import annotations as _annotations

from importlib.metadata import version as _metadata_version

from .main import deno_install_deps, deno_run_args, deno_run_server

__version__ = _metadata_version('mcp_run_python')
__all__ = '__version__', 'deno_run_args', 'deno_run_server', 'deno_install_deps'
