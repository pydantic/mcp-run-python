import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal, TypeAlias, TypedDict

from mcp import ClientSession, StdioServerParameters, types as mcp_types
from mcp.client.stdio import stdio_client

from .main import LogHandler, async_prepare_deno_env

JsonData: TypeAlias = 'str | bool | int | float | None | list[JsonData] | dict[str, JsonData]'


class RunSuccess(TypedDict):
    status: Literal['success']
    output: list[str]
    return_value: JsonData


class RunError(TypedDict):
    status: Literal['install-error', 'run-error']
    output: list[str]
    error: str


@dataclass
class CodeSandbox:
    _session: ClientSession

    async def eval(self, code: str) -> RunSuccess | RunError:
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
    log_handler: LogHandler | None = None,
    logging_level: mcp_types.LoggingLevel | None = None,
    allow_networking: bool = True,
) -> AsyncIterator['CodeSandbox']:
    """Run code in a secure sandbox.

    Args:
        dependencies: A list of dependencies to be installed.
        log_handler: A callback function to handle print statements when code is running.
        logging_level: The logging level to use for the print handler, defaults to `info` if `log_handler` is provided.
        deps_log_handler: A callback function to run on log statements during initial install of dependencies.
        allow_networking: Whether to allow networking or not while executing python code.
    """
    async with async_prepare_deno_env(
        'stdio',
        dependencies=dependencies,
        deps_log_handler=log_handler,
        return_mode='json',
        allow_networking=allow_networking,
    ) as deno_env:
        server_params = StdioServerParameters(command='deno', args=deno_env.args, cwd=deno_env.cwd)

        logging_callback: Callable[[mcp_types.LoggingMessageNotificationParams], Awaitable[None]] | None = None

        if log_handler:

            async def logging_callback_(params: mcp_types.LoggingMessageNotificationParams) -> None:
                log_handler(params.level, params.data)

            logging_callback = logging_callback_

        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write, logging_callback=logging_callback) as session:
                if log_handler:
                    await session.set_logging_level(logging_level or 'info')
                yield CodeSandbox(session)
