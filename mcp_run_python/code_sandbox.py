import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal, TypeAlias, TypedDict

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .main import deno_args_prepare

JsonData: TypeAlias = 'str| bool | int | float | None | list[JsonData] | dict[str, JsonData]'


class RunSuccess(TypedDict):
    status: Literal['success']
    output: list[str]
    returnValueJson: JsonData


class RunError(TypedDict):
    status: Literal['install-error', 'run-error']
    output: list[str]
    error: str


@dataclass
class CodeSandbox:
    _session: ClientSession

    async def run(self, code: str) -> RunSuccess | RunError:
        result = await self._session.call_tool('run_python_code', {'python_code': code})
        content_block = result.content[0]
        if content_block.type == 'text':
            return json.loads(content_block.text)
        else:
            raise ValueError(f'Unexpected content type: {content_block.type}')


@asynccontextmanager
async def code_sandbox(
    *,
    dependencies: list[str] | None = None,
    install_log_handler: Callable[[str], None] | None = None,
) -> AsyncIterator['CodeSandbox']:
    args = deno_args_prepare('stdio', deps=dependencies, install_log_handler=install_log_handler, return_mode='json')
    server_params = StdioServerParameters(command='deno', args=args)
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            yield CodeSandbox(session)
