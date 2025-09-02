from mcp_run_python import code_sandbox


def log_handler(level: str, message: str):
    print(f'{level}: {message}')


code = """
import numpy
a = numpy.array([1, 2, 3])
print(a)
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


if __name__ == '__main__':
    import asyncio

    asyncio.run(main())
