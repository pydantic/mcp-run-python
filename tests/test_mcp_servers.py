from __future__ import annotations as _annotations

import asyncio
import re
import subprocess
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from enum import Enum
from typing import TYPE_CHECKING, Literal, Protocol

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


class McpTools(str, Enum):
    RUN_PYTHON_CODE = 'run_python_code'
    UPLOAD_FILE = 'upload_file'
    UPLOAD_FILE_FROM_URI = 'upload_file_from_uri'
    RETRIEVE_FILE = 'retrieve_file'
    DELETE_FILE = 'delete_file'


class SessionManagerFactory(Protocol):
    def __call__(
        self, deps: list[str], file_persistence: bool = False
    ) -> AbstractAsyncContextManager[ClientSession]: ...


@pytest.fixture(name='run_mcp_session', params=['stdio', 'streamable_http'])
def fixture_run_mcp_session(
    request: pytest.FixtureRequest,
) -> SessionManagerFactory:
    @asynccontextmanager
    async def run_mcp(deps: list[str], file_persistence: bool = False) -> AsyncIterator[ClientSession]:
        if request.param == 'stdio':
            async with async_prepare_deno_env('stdio', dependencies=deps, file_persistence=file_persistence) as env:
                server_params = StdioServerParameters(command='deno', args=env.args, cwd=env.cwd)
                async with stdio_client(server_params) as (read, write):
                    async with ClientSession(read, write) as session:
                        yield session
        else:
            assert request.param == 'streamable_http', request.param
            port = 3101
            async with async_prepare_deno_env(
                'streamable_http', http_port=port, dependencies=deps, file_persistence=file_persistence
            ) as env:
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


async def test_list_tools(run_mcp_session: SessionManagerFactory) -> None:
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
    ...<9 lines>...
    .run_async(globals, locals)
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
    run_mcp_session: SessionManagerFactory,
    deps: list[str],
    code: list[str],
    expected_output: str,
) -> None:
    async with run_mcp_session(deps) as mcp_session:
        await mcp_session.initialize()
        result = await mcp_session.call_tool(McpTools.RUN_PYTHON_CODE, {'python_code': '\n'.join(code)})
        assert len(result.content) == 1
        content = result.content[0]
        assert isinstance(content, types.TextContent)
        assert content.text == expected_output


CSV_DATA = """Name,Age,Department,Salary
Alice,25,Engineering,60000
Bob,32,Marketing,52000
Charlie,29,Engineering,70000
Diana,45,HR,65000
Ethan,35,Marketing,58000
Fiona,28,Engineering,72000
George,40,HR,64000
Hannah,31,Engineering,68000
Ian,38,Marketing,61000
Julia,27,HR,59000
"""

BASE_64_IMAGE = 'iVBORw0KGgoAAAANSUhEUgAAAQAAAAEACAIAAADTED8xAAAEX0lEQVR4nOzdO8vX9R/HcS/56f8PWotGQkPBBUWESCQYNJR0GjIn6UBTgUMZTiGE4ZgRVKNkuDSEFtgBQqIiKunkEFdkWLmEBQUWiNUQYd2KNwTPx+MGvD7Tk/f2/S7O7tmyatKnJx8b3f/p6EOj+5euu2Z0/+Sxt0f3N++9fHR/+57/j+7vuPuT0f3Vo+vwHycA0gRAmgBIEwBpAiBNAKQJgDQBkCYA0gRAmgBIEwBpAiBNAKQJgDQBkCYA0gRAmgBIEwBpAiBNAKQJgDQBkCYA0gRAmgBIEwBpAiBNAKQtDr561+gDpzf9PLp/4eNzo/uXzv41uv/BM0+O7h9/bsPo/vqPdo3u7965GN13AUgTAGkCIE0ApAmANAGQJgDSBECaAEgTAGkCIE0ApAmANAGQJgDSBECaAEgTAGkCIE0ApAmANAGQJgDSBECaAEgTAGkCIE0ApAmANAGQJgDSlh5ce+XoA9+eODK6v3r7naP7b31zaHT/4p+3jO4f2/Tb6P7K41tH9zff+8LovgtAmgBIEwBpAiBNAKQJgDQBkCYA0gRAmgBIEwBpAiBNAKQJgDQBkCYA0gRAmgBIEwBpAiBNAKQJgDQBkCYA0gRAmgBIEwBpAiBNAKQJgDQBkLb09ZmLow8sb1ke3d92YXR+1dO7PhzdX7f2xtH9Q5fN/t/g2j9eHt3/cc350X0XgDQBkCYA0gRAmgBIEwBpAiBNAKQJgDQBkCYA0gRAmgBIEwBpAiBNAKQJgDQBkCYA0gRAmgBIEwBpAiBNAKQJgDQBkCYA0gRAmgBIEwBpAiBtcf3eW0cfePTE7Pf1D9yxMrq/4YrR+VWvnN84uv/lvs2j+2v3nx3dv3rT/0b3XQDSBECaAEgTAGkCIE0ApAmANAGQJgDSBECaAEgTAGkCIE0ApAmANAGQJgDSBECaAEgTAGkCIE0ApAmANAGQJgDSBECaAEgTAGkCIE0ApAmAtKWrzq0ffeD312f339h5ZnT/npsPj+7//cPDo/un739idP/Xg5+P7j/y/G2j+y4AaQIgTQCkCYA0AZAmANIEQJoASBMAaQIgTQCkCYA0AZAmANIEQJoASBMAaQIgTQCkCYA0AZAmANIEQJoASBMAaQIgTQCkCYA0AZAmANIEQNpi/5FfRh94753XRvcP7F0zuv/V7e+O7t906v3R/WdP/zO6f9/ixdH9G3Z/NrrvApAmANIEQJoASBMAaQIgTQCkCYA0AZAmANIEQJoASBMAaQIgTQCkCYA0AZAmANIEQJoASBMAaQIgTQCkCYA0AZAmANIEQJoASBMAaQIgTQCkLb25vDL6wLoHjo7ur7z03ej++u+fGt0/vm/2+/dfHF4e3d9xauPo/taN20b3XQDSBECaAEgTAGkCIE0ApAmANAGQJgDSBECaAEgTAGkCIE0ApAmANAGQJgDSBECaAEgTAGkCIE0ApAmANAGQJgDSBECaAEgTAGkCIE0ApAmAtH8DAAD//9drYGg9ROu9AAAAAElFTkSuQmCC'


@pytest.mark.parametrize(
    'deps,code,expected_output,data_type,expected_file',
    [
        pytest.param(
            ['pillow'],
            [
                'from PIL import Image, ImageFilter',
                'img = Image.open("storage/image.png")',
                'gray_img = img.convert("L")',
                'gray_img.save("storage/image-gray.png")',
                'print(f"Image size: {img.size}")',
            ],
            snapshot("""\
<status>success</status>
<output>
Image size: (256, 256)
</output>\
"""),
            'bytes',
            snapshot(
                'iVBORw0KGgoAAAANSUhEUgAAAQAAAAEACAAAAAB5Gfe6AAAC6ElEQVR4nO3cMaqVZxhFYY9ea7EIiJ0DEEv7gEUghag4gEAGYCNoestg4wCcgE3ggmWIpULSJUJwBEEQQZEEnIG7sHggZ612f8Vi8Zb/OYdHZ77Mn2P/cez/jv33sT8f+52x/zP2s2P/31MALaApgBbQFEALaAqgBTQF0AKaAmgBTQG0gKYAWkBTAC2gKYAW0BRAC2gKoAU0BdACmgJoAU0BtIDmcH08ODf2G2P/MPZ3Y7869vdjPx370V9AAbSApgBaQFMALaApgBbQFEALaAqgBTQF0AKaAmgBTQG0gKYAWkBTAC2gKYAW0BRAC2gKoAU0BdACmsOV8eDe2K+N/eLY/xv7+n7g6djX9wVHfwEF0AKaAmgBTQG0gKYAWkBTAC2gKYAW0BRAC2gKoAU0BdACmgJoAU0BtICmAFpAUwAtoCmAFtAUQAtoDq/Hg/tjvzX2v8b+duyfvnL/OPajv4ACaAFNAbSApgBaQFMALaApgBbQFEALaAqgBTQF0AKaAmgBTQG0gKYAWkBTAC2gKYAW0BRAC2gKoAU0h4fjwfr9/W9jvzz2F2O/OfZnY7809qO/gAJoAU0BtICmAFpAUwAtoCmAFtAUQAtoCqAFNAXQApoCaAFNAbSApgBaQFMALaApgBbQFEALaAqgBTQn34wHp2P/aezfjf3nsb8c+4OxPxn70V9AAbSApgBaQFMALaApgBbQFEALaAqgBTQF0AKaAmgBTQG0gKYAWkBTAC2gKYAW0BRAC2gKoAU0BdACmpPz48H6//+7Y7859j/G/uvYb4/9+7Ef/QUUQAtoCqAFNAXQApoCaAFNAbSApgBaQFMALaApgBbQFEALaAqgBTQF0AKaAmgBTQG0gKYAWkBTAC2gOXk1Hnw79l/G/sPYH4/9zdj/HvuFsR/9BRRAC2gKoAU0BdACmgJoAU0BtICmAFpAUwAtoCmAFtAUQAtoCqAFNAXQApoCaAFNAbSApgBaQFMALaD5DBLPJU7x++9vAAAAAElFTkSuQmCC'
            ),
            id='image-transform',
        ),
        pytest.param(
            ['pandas'],
            [
                'import pandas as pd',
                'df = pd.read_csv("storage/data.csv")',
                'df["Age_in_10_years"] = df["Age"] + 10',
                'df.to_csv("storage/data-processed.csv", index=False)',
                'print(df.describe())',
            ],
            snapshot("""\
<status>success</status>
<output>
             Age        Salary  Age_in_10_years
count  10.000000     10.000000        10.000000
mean   33.000000  62900.000000        43.000000
std     6.394442   6100.091074         6.394442
min    25.000000  52000.000000        35.000000
25%    28.250000  59250.000000        38.250000
50%    31.500000  62500.000000        41.500000
75%    37.250000  67250.000000        47.250000
max    45.000000  72000.000000        55.000000
</output>\
"""),
            'text',
            snapshot("""\
Name,Age,Department,Salary,Age_in_10_years
Alice,25,Engineering,60000,35
Bob,32,Marketing,52000,42
Charlie,29,Engineering,70000,39
Diana,45,HR,65000,55
Ethan,35,Marketing,58000,45
Fiona,28,Engineering,72000,38
George,40,HR,64000,50
Hannah,31,Engineering,68000,41
Ian,38,Marketing,61000,48
Julia,27,HR,59000,37
"""),
            id='dataframe-manipulation',
        ),
    ],
)
async def test_run_python_code_with_files(
    run_mcp_session: SessionManagerFactory,
    deps: list[str],
    code: list[str],
    expected_output: str,
    data_type: Literal['bytes', 'text'],
    expected_file: str,
) -> None:
    async with run_mcp_session(deps, file_persistence=True) as mcp_session:
        await mcp_session.initialize()

        match data_type:
            case 'text':
                filename = 'data.csv'
                new_filename = 'data-processed.csv'
                ctype = 'text/csv'
                result = await mcp_session.call_tool(
                    McpTools.UPLOAD_FILE, {'type': 'text', 'filename': filename, 'text': CSV_DATA}
                )

            case 'bytes':
                filename = 'image.png'
                new_filename = 'image-gray.png'
                ctype = 'image/png'
                result = await mcp_session.call_tool(
                    McpTools.UPLOAD_FILE, {'type': 'bytes', 'filename': filename, 'blob': BASE_64_IMAGE}
                )

        assert result.isError is False
        assert len(result.content) == 1
        content = result.content[0]
        assert isinstance(content, types.ResourceLink)
        assert str(content.uri) == f'file:///{filename}'
        assert content.name == filename
        assert content.mimeType is not None
        assert content.mimeType.startswith(ctype)

        result = await mcp_session.list_resources()
        assert len(result.resources) == 1
        content = result.resources[0]
        assert str(content.uri) == f'file:///{filename}'
        assert content.name == filename
        assert content.mimeType is not None
        assert content.mimeType.startswith(ctype)

        result = await mcp_session.call_tool('run_python_code', {'python_code': '\n'.join(code)})
        assert result.isError is False
        assert len(result.content) == 1
        content = result.content[0]
        assert isinstance(content, types.TextContent)
        assert content.text == expected_output

        result = await mcp_session.list_resources()
        assert len(result.resources) == 2
        assert {filename, new_filename} == set(resource.name for resource in result.resources)

        result = await mcp_session.call_tool(McpTools.RETRIEVE_FILE, {'filename': new_filename})
        assert result.isError is False
        assert len(result.content) == 1
        content = result.content[0]
        assert isinstance(content, types.ResourceLink)
        assert str(content.uri) == f'file:///{new_filename}'
        assert content.name == new_filename
        assert content.mimeType is not None
        assert content.mimeType.startswith(ctype)

        result = await mcp_session.read_resource(content.uri)
        assert len(result.contents) == 1
        content = result.contents[0]
        assert str(content.uri) == f'file:///{new_filename}'
        assert content.mimeType is not None
        assert content.mimeType.startswith(ctype)

        match data_type:
            case 'text':
                assert isinstance(content, types.TextResourceContents)
                assert content.text == expected_file

            case 'bytes':
                assert isinstance(content, types.BlobResourceContents)
                assert content.blob == expected_file


async def test_install_run_python_code() -> None:
    logs: list[str] = []

    def logging_callback(level: str, message: str) -> None:
        logs.append(f'{level}: {message}')

    async with async_prepare_deno_env('stdio', dependencies=['numpy'], deps_log_handler=logging_callback) as env:
        assert len(logs) >= 10
        assert re.search(
            r"loadPackage: Didn't find package numpy\S+?\.whl locally, attempting to load from", '\n'.join(logs)
        )

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
