<div align="center">
  <h1>MCP Run Python</h1>
</div>
<div align="center">
  <a href="https://github.com/pydantic/mcp-run-python/actions/workflows/ci.yml?query=branch%3Amain"><img src="https://github.com/pydantic/mcp-run-python/actions/workflows/ci.yml/badge.svg?event=push" alt="CI"></a>
  <a href="https://pypi.python.org/pypi/mcp-run-python"><img src="https://img.shields.io/pypi/v/mcp-run-python.svg" alt="PyPI"></a>
  <a href="https://github.com/pydantic/mcp-run-python"><img src="https://img.shields.io/pypi/pyversions/mcp-run-python.svg" alt="versions"></a>
  <a href="https://github.com/pydantic/mcp-run-python/blob/main/LICENSE"><img src="https://img.shields.io/github/license/pydantic/mcp-run-python.svg" alt="license"></a>
  <a href="https://logfire.pydantic.dev/docs/join-slack/"><img src="https://img.shields.io/badge/Slack-Join%20Slack-4A154B?logo=slack" alt="Join Slack" /></a>
</div>
<br/>
<div align="center">
  MCP server to run Python code in a sandbox.
</div>
<br/>

Code is executed using [Pyodide](https://pyodide.org) in [Deno](https://deno.com/) and is therefore isolated from
the rest of the operating system.

## Features

- **Secure Execution**: Run Python code in a sandboxed WebAssembly environment
- **Package Management**: Automatically detects and installs required dependencies
- **Complete Results**: Captures standard output, standard error, and return values
- **Asynchronous Support**: Runs async code properly
- **Error Handling**: Provides detailed error reports for debugging

## Usage

To use this server, you must have both Python and [Deno](https://deno.com/) installed.

The server can be run with `deno` installed using `uvx`:

```bash
uvx mcp-run-python [-h] [--version] [--port PORT] [--deps DEPS] {stdio,streamable-http,example}
```

where:

- `stdio` runs the server with the
  [Stdio MCP transport](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports#stdio) — suitable for
  running the process as a subprocess locally
- `streamable-http` runs the server with the
  [Streamable HTTP MCP transport](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports#streamable-http) -
  suitable for running the server as an HTTP server to connect locally or remotely. This supports stateful requests, but
  does not require the client to hold a stateful connection like SSE
- `example` will run a minimal Python script using `numpy`, useful for checking that the package is working, for the code
  to run successfully, you'll need to install `numpy` using `uvx mcp-run-python --deps numpy example`

## Usage in codes

```bash
pip install mcp-run-python
# or
uv add mcp-run-python
```

Then you can use `mcp-run-python` with Pydantic AI:

```python
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio
from mcp_run_python import deno_args_prepare

import logfire

logfire.configure()
logfire.instrument_mcp()
logfire.instrument_pydantic_ai()

server = MCPServerStdio('deno', args=deno_args_prepare('stdio'))
agent = Agent('claude-3-5-haiku-latest', toolsets=[server])


async def main():
    async with agent:
        result = await agent.run('How many days between 2000-01-01 and 2025-03-18?')
    print(result.output)
    #> There are 9,208 days between January 1, 2000, and March 18, 2025.w

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
```

**Note**: `deno_args_prepare` can take `deps` as a keyword argument to install dependencies.
As well as returning the args needed to run `mcp_run_python`, `deno_args_prepare` installs the dependencies
so they can be used by the server.

## Logging

MCP Run Python supports emitting stdout and stderr from the python execution as [MCP logging messages](https://github.com/modelcontextprotocol/specification/blob/eb4abdf2bb91e0d5afd94510741eadd416982350/docs/specification/draft/server/utilities/logging.md?plain=1).

For logs to be emitted you must set the logging level when connecting to the server. By default, the log level is set to the highest level, `emergency`.

Currently, it's not possible to demonstrate this due to a bug in the Python MCP Client, see [modelcontextprotocol/python-sdk#201](https://github.com/modelcontextprotocol/python-sdk/issues/201#issuecomment-2727663121).

## Dependencies

`mcp_run_python` uses a two step process to install dependencies while avoiding any risk that sandboxed code can
edit the filesystem.

* `deno` is first run with write permissions to the `node_modules` directory and dependencies are installed, causing wheels to be written to ``
* `deno` is then run with read-only permissions to the `node_modules` directory to run untrusted code.

Dependencies must be provided when initializing the server so they can be installed in the first step.

Here's an example of manually running code with `mcp-run-python`:

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_run_python import deno_args_prepare

code = """
import numpy
a = numpy.array([1, 2, 3])
print(a)
a
"""
server_params = StdioServerParameters(
    command='deno',
    args=deno_args_prepare('stdio', deps=['numpy'])
)


async def main():
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(len(tools.tools))
            #> 1
            print(repr(tools.tools[0].name))
            #> 'run_python_code'
            print(repr(tools.tools[0].inputSchema))
            """
            {'type': 'object', 'properties': {'python_code': {'type': 'string', 'description': 'Python code to run'}}, 'required': ['python_code'], 'additionalProperties': False, '$schema': 'http://json-schema.org/draft-07/schema#'}
            """
            result = await session.call_tool('run_python_code', {'python_code': code})
            print(result.content[0].text)
            """
            <status>success</status>
            <dependencies>["numpy"]</dependencies>
            <output>
            [1 2 3]
            </output>
            <return_value>
            [
              1,
              2,
              3
            ]
            </return_value>
            """
```
