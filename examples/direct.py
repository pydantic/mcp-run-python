from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_run_python import async_prepare_deno_env

code = """
import numpy
a = numpy.array([1, 2, 3])
print(a)
a
"""


async def main():
    async with async_prepare_deno_env('stdio', dependencies=['numpy']) as deno_env:
        server_params = StdioServerParameters(command='deno', args=deno_env.args, cwd=deno_env.cwd)
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                print(len(tools.tools))
                print(repr(tools.tools[0].name))
                print(repr(tools.tools[0].inputSchema))
                result = await session.call_tool('run_python_code', {'python_code': code})
                content_block = result.content[0]
                assert content_block.type == 'text'
                print(content_block.text)


if __name__ == '__main__':
    import asyncio

    asyncio.run(main())
