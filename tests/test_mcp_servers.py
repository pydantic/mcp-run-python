from __future__ import annotations as _annotations

import asyncio
import re
import subprocess
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, AsyncContextManager

import pytest
from httpx import AsyncClient, HTTPError
from inline_snapshot import snapshot
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from mcp_run_python import deno_run_args

if TYPE_CHECKING:
    from mcp import ClientSession

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture(name='run_mcp_session', params=['stdio', 'streamable_http'])
def fixture_run_mcp_session(
    request: pytest.FixtureRequest,
) -> Callable[[list[str]], AsyncContextManager[ClientSession]]:
    @asynccontextmanager
    async def run_mcp(deps: list[str]) -> AsyncIterator[ClientSession]:
        if request.param == 'stdio':
            server_params = StdioServerParameters(command='deno', args=deno_run_args('stdio', deps=deps))
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    yield session
        else:
            assert request.param == 'streamable_http', request.param
            port = 3101
            p = subprocess.Popen(['deno', *deno_run_args('streamable_http', port=3101, deps=deps)])
            try:
                url = f'http://localhost:{port}/mcp'

                async with AsyncClient() as client:
                    for _ in range(10):
                        try:
                            await client.get(url, timeout=0.01)
                        except HTTPError:
                            await asyncio.sleep(0.1)
                        else:
                            break

                async with streamablehttp_client(url) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as session:
                        yield session

            finally:
                p.terminate()
                exit_code = p.wait()
                if exit_code > 0:
                    pytest.fail(f'Process exited with code {exit_code}')

    return run_mcp


async def test_list_tools(run_mcp_session: Callable[[list[str]], AsyncContextManager[ClientSession]]) -> None:
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
            id='magic-comment-import',
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
    run_mcp_session: Callable[[list[str]], AsyncContextManager[ClientSession]],
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
    node_modules = Path(__file__).parent.parent / 'mcp_run_python/node_modules'
    if node_modules.exists():
        # shutil.rmtree can't delete node_modules :-(
        subprocess.run(['rm', '-r', node_modules], check=True)

    logs: list[str] = []

    async def logging_callback(params: types.LoggingMessageNotificationParams) -> None:
        logs.append(f'{params.level}: {params.data}')

    server_params = StdioServerParameters(command='deno', args=deno_run_args('stdio', deps=['numpy']))
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write, logging_callback=logging_callback) as mcp_session:
            await mcp_session.initialize()
            await mcp_session.set_logging_level('debug')
            result = await mcp_session.call_tool(
                'run_python_code', {'python_code': 'import numpy\nnumpy.array([1, 2, 3])'}
            )
            assert len(result.content) == 1
            content = result.content[0]
            assert isinstance(content, types.TextContent)
            expected_output = """\
<status>success</status>
<return_value>
[
  1,
  2,
  3
]
</return_value>\
"""
            assert content.text == expected_output
            assert len(logs) >= 18
            assert re.search(
                r"debug: Didn't find package numpy\S+?\.whl locally, attempting to load from", '\n'.join(logs)
            )
