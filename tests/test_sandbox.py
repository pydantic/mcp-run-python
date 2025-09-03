from dataclasses import dataclass
from typing import Any

import pytest
from inline_snapshot import snapshot

from mcp_run_python import code_sandbox

pytestmark = pytest.mark.anyio


@dataclass
class Foobar:
    a: int
    b: str
    c: bytes


@pytest.mark.parametrize(
    'deps,code,locals,expected',
    [
        pytest.param(
            [],
            'a = 1\na + 1',
            {},
            snapshot({'status': 'success', 'output': [], 'return_value': 2}),
            id='return-value-success',
        ),
        pytest.param(
            [],
            'print(123)',
            {},
            snapshot({'status': 'success', 'output': ['123'], 'return_value': None}),
            id='print-success',
        ),
        pytest.param(
            [],
            'a',
            {'a': [1, 2, 3]},
            snapshot({'status': 'success', 'output': [], 'return_value': [1, 2, 3]}),
            id='access-local-variables',
        ),
        pytest.param(
            [],
            'a + b',
            {'a': 4, 'b': 5},
            snapshot({'status': 'success', 'output': [], 'return_value': 9}),
            id='multiple-locals',
        ),
        pytest.param(
            [],
            'print(f)',
            {'f': Foobar(1, '2', b'3')},
            snapshot({'status': 'success', 'output': ["{'a': 1, 'b': '2', 'c': '3'}"], 'return_value': None}),
            id='print-complex-local',
        ),
        pytest.param(
            [],
            'f',
            {'f': Foobar(1, '2', b'3')},
            snapshot({'status': 'success', 'output': [], 'return_value': {'a': 1, 'b': '2', 'c': '3'}}),
            id='return-complex-local',
        ),
        pytest.param(
            [],
            'print(unknown)',
            {},
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
            {},
            snapshot({'status': 'success', 'output': [], 'return_value': [1, 2, 3]}),
            id='return-numpy-success',
        ),
    ],
)
async def test_sandbox(deps: list[str], code: str, locals: dict[str, Any], expected: Any):
    async with code_sandbox(dependencies=deps) as sandbox:
        result = await sandbox.eval(code, locals)
        assert result == expected


async def test_multiple_commands():
    async with code_sandbox() as sandbox:
        result = await sandbox.eval('print(1)\n1')
        assert result == snapshot({'status': 'success', 'output': ['1'], 'return_value': 1})
        result = await sandbox.eval('print(2)\n2')
        assert result == snapshot({'status': 'success', 'output': ['2'], 'return_value': 2})
        result = await sandbox.eval('print(3)\n3')
        assert result == snapshot({'status': 'success', 'output': ['3'], 'return_value': 3})


async def test_multiple_sandboxes():
    async with code_sandbox(dependencies=['numpy']) as sandbox_a:
        async with code_sandbox(dependencies=['requests']) as sandbox_b:
            async with code_sandbox() as sandbox_c:
                result = await sandbox_a.eval('import numpy\nnumpy.array([1, 2, 3])')
                assert result == snapshot({'status': 'success', 'output': [], 'return_value': [1, 2, 3]})
                result = await sandbox_b.eval('import numpy\nnumpy.array([1, 2, 3])')
                assert result == snapshot(
                    {
                        'status': 'run-error',
                        'output': [],
                        'error': """\
Traceback (most recent call last):
  File "main.py", line 1, in <module>
    import numpy
ModuleNotFoundError: No module named 'numpy'
The module 'numpy' is included in the Pyodide distribution, but it is not installed.
You can install it by calling:
  await micropip.install("numpy") in Python, or
  await pyodide.loadPackage("numpy") in JavaScript
See https://pyodide.org/en/stable/usage/loading-packages.html for more details.
""",
                    }
                )
                result = await sandbox_c.eval('print(3)')
                assert result == snapshot({'status': 'success', 'output': ['3'], 'return_value': None})


async def test_print_handler():
    logs: list[tuple[str, str]] = []

    def log_handler(level: str, message: str):
        logs.append((level, message))

    async with code_sandbox(log_handler=log_handler) as sandbox:
        await sandbox.eval('print("hello", 123)')

    assert next(((level, msg) for level, msg in logs if level == 'debug'), None) == snapshot(
        (
            'debug',
            'loadPackage: Loading annotated-types, micropip, packaging, pydantic, pydantic_core, typing-extensions',
        ),
    )
    assert [(level, msg) for level, msg in logs if level == 'info'][-1] == snapshot(('info', 'hello 123'))


async def test_disallow_networking():
    code = """
import httpx
async with httpx.AsyncClient() as client:
    await client.get('http://localhost')
"""
    async with code_sandbox(dependencies=['httpx'], allow_networking=False) as sandbox:
        result = await sandbox.eval(code)

    assert 'error' in result
    assert result['error'].strip().splitlines()[-1] == snapshot(
        'httpx.ConnectError: Requires net access to "localhost:80", run again with the --allow-net flag'
    )
