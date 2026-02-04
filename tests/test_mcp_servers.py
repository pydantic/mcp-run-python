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

if TYPE_CHECKING:
    from mcp import ClientSession

pytestmark = pytest.mark.anyio


@pytest.fixture(name='run_mcp_session', params=['stdio', 'streamable_http', 'streamable_http_stateless'])
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
            assert request.param in ('streamable_http', 'streamable_http_stateless'), request.param
            port = 3101
            async with async_prepare_deno_env(request.param, http_port=port, dependencies=deps) as env:
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
        assert len(tools.tools) == 2

        # Find tools by name since order may vary
        tool_by_name = {t.name: t for t in tools.tools}

        # Check discover_available_tools tool
        discover_tool = tool_by_name['discover_available_tools']
        assert discover_tool.description
        assert 'Discover what tools are available' in discover_tool.description
        assert discover_tool.inputSchema['properties'] == {}

        # Check run_python_code tool
        run_tool = tool_by_name['run_python_code']
        assert run_tool.description
        assert run_tool.description.startswith(
            'Tool to execute Python code and return stdout, stderr, and return value.'
        )
        assert run_tool.inputSchema == snapshot(
            {
                'type': 'object',
                'properties': {
                    'python_code': {'type': 'string', 'description': 'Python code to run'},
                    'global_variables': {
                        'type': 'object',
                        'additionalProperties': {},
                        'default': {},
                        'description': 'Map of global variables in context when the code is executed',
                    },
                },
                'required': ['python_code'],
                'additionalProperties': False,
                '$schema': 'http://json-schema.org/draft-07/schema#',
            }
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


async def test_discover_available_tools(
    run_mcp_session: Callable[[list[str]], AbstractAsyncContextManager[ClientSession]],
) -> None:
    """Test the discover_available_tools functionality."""
    async with run_mcp_session([]) as mcp_session:
        await mcp_session.initialize()
        result = await mcp_session.call_tool('discover_available_tools', {})

        assert len(result.content) == 1
        content = result.content[0]
        assert isinstance(content, types.TextContent)

        # Parse the response to verify structure
        import json

        response_data = json.loads(content.text)

        assert 'available_tools' in response_data
        assert 'tool_schemas' in response_data
        assert 'usage_note' in response_data
        assert isinstance(response_data['available_tools'], list)
        assert isinstance(response_data['tool_schemas'], dict)

        # Should initially return empty tools since no elicitation callback is set up
        assert response_data['available_tools'] == []
        assert response_data['tool_schemas'] == {}
