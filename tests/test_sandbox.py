import asyncio
from typing import Any

import pytest
from inline_snapshot import snapshot

from mcp_run_python import code_sandbox

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    'deps,code,expected',
    [
        pytest.param(
            [],
            'a = 1\na + 1',
            snapshot({'status': 'success', 'output': [], 'return_value': 2}),
            id='return-value-success',
        ),
        pytest.param(
            [],
            'print(123)',
            snapshot({'status': 'success', 'output': ['123'], 'return_value': None}),
            id='print-success',
        ),
        pytest.param(
            [],
            'print(unknown)',
            snapshot(
                {
                    'status': 'run-error',
                    'output': [],
                    'error': """\
Traceback (most recent call last):
  File "main.py", line 1, in <module>
    print(unknown)
          ^^^^^^^
NameError: name 'unknown' is not defined
""",
                }
            ),
            id='print-error',
        ),
        pytest.param(
            ['numpy'],
            'import numpy\nnumpy.array([1, 2, 3])',
            snapshot({'status': 'success', 'output': [], 'return_value': [1, 2, 3]}),
            id='return-numpy-success',
        ),
    ],
)
async def test_sandbox(deps: list[str], code: str, expected: Any):
    async with code_sandbox(dependencies=deps) as sandbox:
        result = await sandbox.eval(code)
        assert result == expected


async def test_multiple_commands():
    async with code_sandbox() as sandbox:
        result = await sandbox.eval('print(1)')
        assert result == snapshot({'status': 'success', 'output': ['1'], 'return_value': None})
        result = await sandbox.eval('print(2)')
        assert result == snapshot({'status': 'success', 'output': ['2'], 'return_value': None})
        result = await sandbox.eval('print(3)')
        assert result == snapshot({'status': 'success', 'output': ['3'], 'return_value': None})


async def test_sync_print_handler():
    logs: list[tuple[str, str]] = []

    def print_handler(level: str, message: str):
        logs.append((level, message))

    async with code_sandbox(print_handler=print_handler) as sandbox:
        await sandbox.eval('print("hello", 123)')

    assert logs == snapshot([('info', 'hello 123')])


async def test_async_print_handler():
    logs: list[tuple[str, str]] = []

    async def print_handler(level: str, message: str):
        await asyncio.sleep(0.1)
        logs.append((level, message))

    async with code_sandbox(print_handler=print_handler) as sandbox:
        await sandbox.eval('print("hello", 123)')

    assert logs == snapshot([('info', 'hello 123')])
