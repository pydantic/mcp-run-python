"""Logic for installing dependencies in Pyodide.

Mostly taken from https://github.com/pydantic/pydantic.run/blob/main/src/frontend/src/prepare_env.py
"""

from __future__ import annotations as _annotations

import importlib
import logging
import sys
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal, cast

import micropip

__all__ = 'prepare_env', 'dump_json'


@dataclass
class Success:
    dependencies: list[str] | None
    kind: Literal['success'] = 'success'


@dataclass
class Error:
    message: str
    kind: Literal['error'] = 'error'


async def prepare_env(dependencies: list[str] | None) -> Success | Error:
    sys.setrecursionlimit(400)

    if dependencies:
        dependencies = _add_extra_dependencies(dependencies)

        with _micropip_logging() as logs_filename:
            try:
                await micropip.install(dependencies, keep_going=True)
                importlib.invalidate_caches()
            except Exception:
                with open(logs_filename) as f:
                    logs = f.read()
                return Error(message=f'{logs} {traceback.format_exc()}')

    return Success(dependencies=dependencies)


def dump_json(value: Any, alwasy_return_json: bool) -> str | None:
    from pydantic_core import to_json

    if value is None:
        return None
    if isinstance(value, str) and not alwasy_return_json:
        return value
    else:
        return to_json(value, indent=2, fallback=_json_fallback).decode()


def _json_fallback(value: Any) -> Any:
    tp = cast(type[Any], type(value))
    module = tp.__module__
    if module == 'numpy':
        if tp.__name__ in {'ndarray', 'matrix'}:
            return value.tolist()
        else:
            return value.item()
    elif module == 'pyodide.ffi':
        return value.to_py()
    else:
        return repr(value)


def _add_extra_dependencies(dependencies: list[str]) -> list[str]:
    """Add extra dependencies we know some packages need.

    Workaround for micropip not installing some required transitive dependencies.
    See https://github.com/pyodide/micropip/issues/204

    pygments seems to be required to get rich to work properly, ssl is required for FastAPI and HTTPX,
    pydantic_ai requires newest typing_extensions.
    """
    extras: list[str] = []
    for d in dependencies:
        if d.startswith(('logfire', 'rich')):
            extras.append('pygments')
        elif d.startswith(('fastapi', 'httpx', 'pydantic_ai')):
            extras.append('ssl')

        if d.startswith('pydantic_ai'):
            extras.append('typing_extensions>=4.12')

        if len(extras) == 3:
            break

    return dependencies + extras


@contextmanager
def _micropip_logging() -> Iterator[str]:
    from micropip import logging as micropip_logging

    micropip_logging.setup_logging()
    logger = logging.getLogger('micropip')
    logger.handlers.clear()
    logger.setLevel(logging.INFO)

    file_name = 'micropip.log'
    handler = logging.FileHandler(file_name)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)
    try:
        yield file_name
    finally:
        logger.removeHandler(handler)
