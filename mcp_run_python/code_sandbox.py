import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias, TypedDict

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

    async def eval(
        self,
        code: str,
        globals: dict[str, Any] | None = None,
    ) -> RunSuccess | RunError:
        """Run code in the sandbox.

        Args:
            code: Python code to run.
            globals: Dictionary of global variables in context when the code is executed
        """
        args: dict[str, Any] = {'python_code': code}
        if globals is not None:
            args['global_variables'] = globals
        result = await self._session.call_tool('run_python_code', args)
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
    allow_networking: bool = True,
    enable_file_outputs: bool = False,
    pyodide_max_workers: int = 10,
    pyodide_worker_wait_timeout_sec: int = 60,
    pyodide_code_run_timeout_sec: int = 60,
) -> AsyncIterator['CodeSandbox']:
    """Create a secure sandbox.

    Args:
        dependencies: A list of dependencies to be installed.
        log_handler: A callback function to handle print statements when code is running.
        deps_log_handler: A callback function to run on log statements during initial install of dependencies.
        allow_networking: Whether to allow networking or not while executing python code.
        enable_file_outputs: Whether to enable output files
        pyodide_max_workers: How many pyodide workers to max use at the same time
        pyodide_worker_wait_timeout_sec: How long to wait for a free pyodide worker in seconds.
        pyodide_code_run_timeout_sec: How long to wait for pyodide code to run in seconds.
    """
    async with async_prepare_deno_env(
        'stdio',
        dependencies=dependencies,
        deps_log_handler=log_handler,
        return_mode='json',
        allow_networking=allow_networking,
        enable_file_outputs=enable_file_outputs,
        pyodide_max_workers=pyodide_max_workers,
        pyodide_code_run_timeout_sec=pyodide_code_run_timeout_sec,
        pyodide_worker_wait_timeout_sec=pyodide_worker_wait_timeout_sec,
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
                    await session.set_logging_level('debug')
                yield CodeSandbox(session)
