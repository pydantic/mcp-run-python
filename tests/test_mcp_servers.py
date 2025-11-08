from __future__ import annotations as _annotations

import asyncio
import re
import subprocess
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING, Any

import pytest
from httpx import AsyncClient, HTTPError
from inline_snapshot import snapshot
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import BlobResourceContents, EmbeddedResource

from mcp_run_python import async_prepare_deno_env

if TYPE_CHECKING:
    from mcp import ClientSession

pytestmark = pytest.mark.anyio


@pytest.fixture(name='run_mcp_session', params=['stdio', 'streamable_http'])
def fixture_run_mcp_session(
    request: pytest.FixtureRequest,
) -> Callable[[list[str]], AbstractAsyncContextManager[ClientSession]]:
    @asynccontextmanager
    async def run_mcp(deps: list[str], enable_file_outputs: bool = True) -> AsyncIterator[ClientSession]:
        if request.param == 'stdio':
            async with async_prepare_deno_env(
                'stdio', dependencies=deps, enable_file_outputs=enable_file_outputs
            ) as env:
                server_params = StdioServerParameters(command='deno', args=env.args, cwd=env.cwd)
                async with stdio_client(server_params) as (read, write):
                    async with ClientSession(read, write) as session:
                        yield session
        else:
            assert request.param == 'streamable_http', request.param
            port = 3101
            async with async_prepare_deno_env(
                'streamable_http', http_port=port, dependencies=deps, enable_file_outputs=enable_file_outputs
            ) as env:
                p = subprocess.Popen(['deno', *env.args], cwd=env.cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
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
        assert tool.inputSchema == snapshot(
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


@pytest.mark.parametrize(
    'deps,code,expected_output,expected_resources',
    [
        pytest.param(
            [],
            [
                'from pathlib import Path',
                'Path("/output_files/hello.txt").write_text("hello world!")',
            ],
            snapshot("""\
<status>success</status>
<return_value>
12
</return_value>\
"""),
            [
                EmbeddedResource(
                    type='resource',
                    resource=BlobResourceContents(
                        uri='file://_',
                        mimeType='text/plain',
                        name='hello.txt',  # pyright: ignore[reportCallIssue]
                        blob='aGVsbG8gd29ybGQh',
                    ),
                )
            ],
            id='hello-world-file',
        ),
    ],
)
async def test_run_python_code_with_output_resource(
    run_mcp_session: Callable[[list[str]], AbstractAsyncContextManager[ClientSession]],
    deps: list[str],
    code: list[str],
    expected_output: str,
    expected_resources: list[EmbeddedResource],
) -> None:
    async with run_mcp_session(deps) as mcp_session:
        await mcp_session.initialize()
        result = await mcp_session.call_tool('run_python_code', {'python_code': '\n'.join(code)})
        assert len(result.content) >= 2
        text_content = result.content[0]
        resource_content = result.content[1:]
        assert isinstance(text_content, types.TextContent)
        assert text_content.text == expected_output
        assert len(resource_content) == len(expected_resources)
        for got, expected in zip(resource_content, expected_resources):
            assert got == expected


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


@pytest.mark.parametrize('enable_file_outputs', [pytest.param(True), pytest.param(False)])
@pytest.mark.parametrize(
    'code_list,multiplicator,max_time_needed',
    [
        pytest.param(
            [
                """
                import time
                time.sleep(5)
                x=11
                x
                """,
                """
                import asyncio
                await asyncio.sleep(5)
                x=11
                x
                """,
            ],
            10,
            30,
        ),
        pytest.param(
            [
                """
                x=11
                x
                """,
            ],
            500,
            30,
        ),
    ],
)
async def test_run_parallel_python_code(
    run_mcp_session: Callable[[list[str], bool], AbstractAsyncContextManager[ClientSession]],
    enable_file_outputs: bool,
    code_list: list[str],
    multiplicator: int,
    max_time_needed: int,
) -> None:
    # Run this a couple times (10) in parallel
    # As we have 10 pyodide workers by default, this should finish in under the needed time if you add the tasks itself (first initialisation takes a bit - especially for 10 workers)
    code_list = code_list * multiplicator

    concurrency_limiter = asyncio.Semaphore(50)

    async def run_wrapper(code: str):
        # limit concurrency to avoid overwhelming the server with 500 tasks at once :D
        async with concurrency_limiter:
            return await mcp_session.call_tool('run_python_code', {'python_code': code})

    async with run_mcp_session([], enable_file_outputs) as mcp_session:
        await mcp_session.initialize()

        start = time.perf_counter()

        tasks: set[Any] = set()
        for code in code_list:
            tasks.add(run_wrapper(code))

        # await the tasks
        results: list[types.CallToolResult] = await asyncio.gather(*tasks)

        # check parallelism
        end = time.perf_counter()
        run_time = end - start
        assert run_time < max_time_needed
        assert run_time > 5

        # check that all outputs are fine too
        for result in results:
            assert len(result.content) == 1
            content = result.content[0]

            assert isinstance(content, types.TextContent)
            assert (
                content.text.strip()
                == """<status>success</status>
<return_value>
11
</return_value>""".strip()
            )


async def test_run_parallel_python_code_with_files(
    run_mcp_session: Callable[[list[str], bool], AbstractAsyncContextManager[ClientSession]],
) -> None:
    """Check that the file system works between runs and keeps files to their runs"""
    code_list = [
        """
        import time
        from pathlib import Path
        for i in range(5):
            Path(f"/output_files/run1_file{i}.txt").write_text("hi")
            time.sleep(1)
        """,
        """
        import time
        from pathlib import Path
        for i in range(5):
            time.sleep(1)
            Path(f"/output_files/run2_file{i}.txt").write_text("hi")
        """,
    ]

    async with run_mcp_session([], True) as mcp_session:
        await mcp_session.initialize()

        start = time.perf_counter()

        tasks: set[Any] = set()
        for code in code_list:
            tasks.add(mcp_session.call_tool('run_python_code', {'python_code': code}))

        # await the tasks
        results: list[types.CallToolResult] = await asyncio.gather(*tasks)

        # check parallelism
        end = time.perf_counter()
        run_time = end - start
        assert run_time < 10
        assert run_time > 5

        # check that all outputs are fine too
        for result in results:
            assert len(result.content) == 6

            run_ids: set[str] = set()
            for content in result.content:
                match content:
                    case types.EmbeddedResource():
                        # save the run id from the text file name - to make sure its all the same
                        run_ids.add(content.resource.name.split('_')[0])  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue]
                        assert content.resource.blob == 'aGk='  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
                    case types.TextContent():
                        assert content.text.strip() == '<status>success</status>'
                    case _:
                        raise AssertionError('Unexpected content type')
            assert len(run_ids) == 1


async def test_run_python_code_timeout(
    run_mcp_session: Callable[[list[str], bool], AbstractAsyncContextManager[ClientSession]],
) -> None:
    """Check that the timeout of the run command works (60s)"""
    code = """
        import time
        time.sleep(90)
        """

    async with run_mcp_session([], True) as mcp_session:
        await mcp_session.initialize()

        start = time.perf_counter()

        result = await mcp_session.call_tool('run_python_code', {'python_code': code})

        # check parallelism
        end = time.perf_counter()
        run_time = end - start
        assert run_time > 60
        assert run_time < 65

        assert len(result.content) == 1
        content = result.content[0]
        assert isinstance(content, types.TextContent)
        assert (
            content.text.strip()
            == """<status>run-error</status>
<error>
Error: Timeout exceeded for python execution (60 sec)
</error>""".strip()
        )
