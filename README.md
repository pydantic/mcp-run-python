# MCP Run Python

[Model Context Protocol](https://modelcontextprotocol.io/) server to run Python code in a sandbox.

The code is executed using [Pyodide](https://pyodide.org) in [Deno](https://deno.com/) and is therefore isolated from
the rest of the operating system.

**See <https://ai.pydantic.dev/mcp/run-python/> for complete documentation.**

To use this server, you must have both Python and [Deno](https://deno.com/) installed

The server can be run with `deno` installed using `uvx`:

```bash
uvx mcp-run-python [stdio|streamable-http|warmup]
```

where:

- `stdio` runs the server with the
  [Stdio MCP transport](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports#stdio) — suitable for
  running the process as a subprocess locally
- `streamable-http` runs the server with the
  [Streamable HTTP MCP transport](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports#streamable-http) -
  suitable for running the server as an HTTP server to connect locally or remotely. This supports stateful requests, but
  does not require the client to hold a stateful connection like SSE
- `warmup` will run a minimal Python script to download and cache the Python standard library. This is also useful to
  check the server is running correctly.

Here's an example of using `mcp-run-python` with Pydantic AI:

```python
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio
from mcp_run_python import deno_args

import logfire

logfire.configure()
logfire.instrument_mcp()
logfire.instrument_pydantic_ai()

server = MCPServerStdio('deno', args=deno_args('stdio'))
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
