import asyncio
import time

from mcp_run_python import code_sandbox


def log_handler(level: str, message: str):
    print(f'{level}: {message}')


code = """
import numpy, asyncio
a = numpy.array([1, 2, 3])
print(a)
await asyncio.sleep(1)
a
"""


async def main():
    async with code_sandbox(dependencies=['numpy'], log_handler=log_handler, logging_level='debug') as sandbox:
        print('running code')
        result = await sandbox.eval(code)
        print(f'{result["status"].title()}:')
        if result['status'] == 'success':
            print(result['return_value'])
        else:
            print(result['error'])

        tic = time.time()
        result = await asyncio.gather(*[sandbox.eval(code) for _ in range(10)])
        toc = time.time()
        print(f'Execution time: {toc - tic:.3f} seconds')


if __name__ == '__main__':
    asyncio.run(main())
