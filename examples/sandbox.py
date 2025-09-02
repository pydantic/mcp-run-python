from mcp_run_python import code_sandbox


async def main():
    async with code_sandbox(dependencies=['numpy']) as sandbox:
        for i in range(10):
            result = await sandbox.run(f'import numpy as np\na = np.array([1, 2, {i}])\nprint(a)\na')
            print(result)


if __name__ == '__main__':
    import asyncio

    asyncio.run(main())
