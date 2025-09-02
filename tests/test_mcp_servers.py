from __future__ import annotations as _annotations

import asyncio
import re
import subprocess
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING

import pytest
from httpx import AsyncClient, HTTPError
from inline_snapshot import snapshot
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from mcp_run_python import async_prepare_deno_env
from mcp_run_python._cli import cli_logic

if TYPE_CHECKING:
    from mcp import ClientSession

pytestmark = pytest.mark.anyio


@pytest.fixture(name='run_mcp_session', params=['stdio', 'streamable_http'])
def fixture_run_mcp_session(
    request: pytest.FixtureRequest,
) -> Callable[[list[str]], AbstractAsyncContextManager[ClientSession]]:
    @asynccontextmanager
    async def run_mcp(deps: list[str]) -> AsyncIterator[ClientSession]:
        if request.param == 'stdio':
            async with async_prepare_deno_env('stdio', dependencies=deps) as env:
                server_params = StdioServerParameters(command='deno', args=env.args, cwd=env.cwd)
                async with stdio_client(server_params) as (read, write):
                    async with ClientSession(read, write) as session:
                        yield session
        else:
            assert request.param == 'streamable_http', request.param
            port = 3101
            async with async_prepare_deno_env('streamable_http', http_port=port, dependencies=deps) as env:
                p = subprocess.Popen(['deno', *env.args], cwd=env.cwd)
                try:
                    url = f'http://localhost:{port}/mcp'
                    await wait_for_server(url, 8)

                    async with streamablehttp_client(url) as (read_stream, write_stream, _):
                        async with ClientSession(read_stream, write_stream) as session:
                            yield session

                finally:
                    p.terminate()
                    exit_code = p.wait()
                    if exit_code > 0:
                        pytest.fail(f'Process exited with code {exit_code}')

    return run_mcp


async def wait_for_server(url: str, timeout: float):
    sleep = 0.1
    steps = int(timeout / sleep)

    async with AsyncClient() as client:
        for _ in range(steps):
            try:
                await client.get(url, timeout=0.01)
            except HTTPError:
                await asyncio.sleep(sleep)
            else:
                return

    raise TimeoutError(f'URL {url} did not become available within {timeout} seconds')


async def test_list_tools(run_mcp_session: Callable[[list[str]], AbstractAsyncContextManager[ClientSession]]) -> None:
    async with run_mcp_session([]) as mcp_session:
        await mcp_session.initialize()
        tools = await mcp_session.list_tools()
        assert len(tools.tools) == 1
        tool = tools.tools[0]
        assert tool.name == 'run_python_code'
        assert tool.description
        assert tool.description.startswith('Tool to execute Python code and return stdout, stderr, and return value.')
        assert tool.inputSchema['properties'] == snapshot(
            {'python_code': {'type': 'string', 'description': 'Python code to run'}}
        )


@pytest.mark.parametrize(
    'deps,code,expected_output',
    [
        pytest.param(
            [],
            [
                'x = 4',
                "print(f'{x=}')",
                'x',
            ],
            snapshot("""\
<status>success</status>
<output>
x=4
</output>
<return_value>
4
</return_value>\
"""),
            id='basic-code',
        ),
        pytest.param(
            ['numpy'],
            [
                'import numpy',
                'numpy.array([1, 2, 3])',
            ],
            snapshot("""\
<status>success</status>
<return_value>
[
  1,
  2,
  3
]
</return_value>\
"""),
            id='import-numpy',
        ),
        pytest.param(
            ['pydantic', 'email-validator'],
            [
                'import pydantic',
                'class Model(pydantic.BaseModel):',
                '    email: pydantic.EmailStr',
                "Model(email='hello@pydantic.dev')",
            ],
            snapshot("""\
<status>success</status>
<return_value>
{
  "email": "hello@pydantic.dev"
}
</return_value>\
"""),
            id='pydantic-dependency',
        ),
        pytest.param(
            [],
            [
                'print(unknown)',
            ],
            snapshot("""\
<status>run-error</status>
<error>
Traceback (most recent call last):
  File "main.py", line 1, in <module>
    print(unknown)
          ^^^^^^^
NameError: name 'unknown' is not defined

</error>\
"""),
            id='undefined-variable',
        ),
    ],
)
async def test_run_python_code(
    run_mcp_session: Callable[[list[str]], AbstractAsyncContextManager[ClientSession]],
    deps: list[str],
    code: list[str],
    expected_output: str,
) -> None:
    async with run_mcp_session(deps) as mcp_session:
        await mcp_session.initialize()
        result = await mcp_session.call_tool('run_python_code', {'python_code': '\n'.join(code)})
        assert len(result.content) == 1
        content = result.content[0]
        assert isinstance(content, types.TextContent)
        assert content.text == expected_output


async def test_install_run_python_code() -> None:
    logs: list[str] = []

    def logging_callback(level: str, message: str) -> None:
        logs.append(f'{level}: {message}')

    async with async_prepare_deno_env('stdio', dependencies=['numpy'], deps_log_handler=logging_callback) as env:
        assert len(logs) >= 10
        assert re.search(r"debug: Didn't find package numpy\S+?\.whl locally, attempting to load from", '\n'.join(logs))

        server_params = StdioServerParameters(command='deno', args=env.args, cwd=env.cwd)
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as mcp_session:
                await mcp_session.initialize()
                await mcp_session.set_logging_level('debug')
                result = await mcp_session.call_tool(
                    'run_python_code', {'python_code': 'import numpy\nnumpy.array([1, 2, 3])'}
                )
                assert len(result.content) == 1
                content = result.content[0]
                assert isinstance(content, types.TextContent)
                assert (
                    content.text
                    == """\
<status>success</status>
<return_value>
[
  1,
  2,
  3
]
</return_value>\
"""
                )


def test_cli_version(capsys: pytest.CaptureFixture[str]):
    assert cli_logic(['--version']) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith('mcp-run-python ')


def test_cli_example():
    assert cli_logic(['--deps', 'numpy', 'example']) == 0
