from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_run_python import deno_prepare_args

code = """
import numpy
a = numpy.array([1, 2, 3])
print(a)
a
"""
server_params = StdioServerParameters(command='deno', args=deno_prepare_args('stdio', deps=['numpy']))


async def main():
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(len(tools.tools))
            print(repr(tools.tools[0].name))
            print(repr(tools.tools[0].inputSchema))
            result = await session.call_tool('run_python_code', {'python_code': code})
            print(result.content[0].text)


if __name__ == '__main__':
    import asyncio

    asyncio.run(main())
