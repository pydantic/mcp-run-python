# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "logfire",
#     "mcp-run-python",
#     "pydantic-ai-slim[mcp,anthropic]",
# ]
#
# [tool.uv.sources]
# mcp-run-python = { path = "." }
# ///
import logfire
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio

from mcp_run_python import deno_args

logfire.configure()
logfire.instrument_mcp()
logfire.instrument_pydantic_ai()

server = MCPServerStdio('deno', args=deno_args('stdio'))
agent_with_python = Agent('claude-3-5-haiku-latest', toolsets=[server])


async def main():
    async with agent_with_python:
        result = await agent_with_python.run('How many days between 2000-01-01 and 2025-03-18?')
    print(result.output)
    # > There are 9,208 days between January 1, 2000, and March 18, 2025.w


if __name__ == '__main__':
    import asyncio

    asyncio.run(main())
