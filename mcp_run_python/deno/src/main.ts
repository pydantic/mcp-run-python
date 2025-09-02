/// <reference types="npm:@types/node@22.12.0" />

import './polyfill.ts'
import http from 'node:http'
import { randomUUID } from 'node:crypto'
import { parseArgs } from '@std/cli/parse-args'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js'
import { isInitializeRequest } from '@modelcontextprotocol/sdk/types.js'
import { type LoggingLevel, SetLevelRequestSchema } from '@modelcontextprotocol/sdk/types.js'
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'
import { z } from 'zod'

import { asJson, asXml, RunCode } from './runCode.ts'
import { Buffer } from 'node:buffer'

const VERSION = '0.0.13'

export async function main() {
  const { args } = Deno
  const flags = parseArgs(Deno.args, {
    string: ['deps', 'return-mode', 'port'],
    default: { port: '3001', 'return-mode': 'xml' },
  })
  const deps = flags.deps?.split(',') ?? []
  if (args.length >= 1) {
    if (args[0] === 'stdio') {
      await runStdio(deps, flags['return-mode'])
      return
    } else if (args[0] === 'streamable_http') {
      const port = parseInt(flags.port)
      runStreamableHttp(port, deps, flags['return-mode'])
      return
    } else if (args[0] === 'example') {
      await example(deps)
      return
    } else if (args[0] === 'noop') {
      await installDeps(deps)
      return
    }
  }
  console.error(
    `\
Invalid arguments: ${args.join(' ')}

Usage: deno ... deno/main.ts [stdio|streamable_http|install_deps|noop]

options:
--port <port>             Port to run the HTTP server on (default: 3001)
--deps <deps>             Comma separated list of dependencies to install
--return-mode <xml/json>  Return mode for output data (default: xml)`,
  )
  Deno.exit(1)
}

/*
 * Create an MCP server with the `run_python_code` tool registered.
 */
function createServer(deps: string[], returnMode: string): McpServer {
  const runCode = new RunCode()
  const server = new McpServer(
    {
      name: 'MCP Run Python',
      version: VERSION,
    },
    {
      instructions: 'Call the "run_python_code" tool with the Python code to run.',
      capabilities: {
        logging: {},
      },
    },
  )

  const toolDescription = `Tool to execute Python code and return stdout, stderr, and return value.

The code may be async, and the value on the last line will be returned as the return value.

The code will be executed with Python 3.12.
`

  let setLogLevel: LoggingLevel = 'emergency'

  server.server.setRequestHandler(SetLevelRequestSchema, (request) => {
    setLogLevel = request.params.level
    return {}
  })

  server.tool(
    'run_python_code',
    toolDescription,
    { python_code: z.string().describe('Python code to run') },
    async ({ python_code }: { python_code: string }) => {
      const logPromises: Promise<void>[] = []
      const result = await runCode.run(
        deps,
        { name: 'main.py', content: python_code },
        (level, data) => {
          if (LogLevels.indexOf(level) >= LogLevels.indexOf(setLogLevel)) {
            logPromises.push(server.server.sendLoggingMessage({ level, data }))
          }
        },
      )
      await Promise.all(logPromises)
      return {
        content: [{ type: 'text', text: returnMode === 'xml' ? asXml(result) : asJson(result) }],
      }
    },
  )
  return server
}

/*
 * Define some QOL functions for both the Streamable HTTP server implementation
 */
function httpGetUrl(req: http.IncomingMessage): URL {
  return new URL(
    req.url ?? '',
    `http://${req.headers.host ?? 'unknown'}`,
  )
}

function httpGetBody(req: http.IncomingMessage): Promise<JSON> {
  // https://nodejs.org/en/learn/modules/anatomy-of-an-http-transaction#request-body
  return new Promise((resolve) => {
    // deno-lint-ignore no-explicit-any
    const bodyParts: any[] = []
    let body
    req.on('data', (chunk) => {
      bodyParts.push(chunk)
    }).on('end', () => {
      body = Buffer.concat(bodyParts).toString()
      resolve(JSON.parse(body))
    })
  })
}

function httpSetTextResponse(res: http.ServerResponse, status: number, text: string) {
  res.setHeader('Content-Type', 'text/plain')
  res.statusCode = status
  res.end(`${text}\n`)
}

function httpSetJsonResponse(res: http.ServerResponse, status: number, text: string, code: number) {
  res.setHeader('Content-Type', 'application/json')
  res.statusCode = status
  res.write(JSON.stringify({
    jsonrpc: '2.0',
    error: {
      code: code,
      message: text,
    },
    id: null,
  }))
  res.end()
}

/*
 * Run the MCP server using the Streamable HTTP transport
 */
function runStreamableHttp(port: number, deps: string[], returnMode: string) {
  // https://github.com/modelcontextprotocol/typescript-sdk?tab=readme-ov-file#with-session-management
  const mcpServer = createServer(deps, returnMode)
  const transports: { [sessionId: string]: StreamableHTTPServerTransport } = {}

  const server = http.createServer(async (req, res) => {
    const url = httpGetUrl(req)
    let pathMatch = false
    function match(method: string, path: string): boolean {
      if (url.pathname === path) {
        pathMatch = true
        return req.method === method
      }
      return false
    }

    // Reusable handler for GET and DELETE requests
    async function handleSessionRequest() {
      const sessionId = req.headers['mcp-session-id'] as string | undefined
      if (!sessionId || !transports[sessionId]) {
        httpSetTextResponse(res, 400, 'Invalid or missing session ID')
        return
      }

      const transport = transports[sessionId]
      await transport.handleRequest(req, res)
    }

    // Handle different request methods and paths
    if (match('POST', '/mcp')) {
      // Check for existing session ID
      const sessionId = req.headers['mcp-session-id'] as string | undefined
      let transport: StreamableHTTPServerTransport

      const body = await httpGetBody(req)

      if (sessionId && transports[sessionId]) {
        // Reuse existing transport
        transport = transports[sessionId]
      } else if (!sessionId && isInitializeRequest(body)) {
        // New initialization request
        transport = new StreamableHTTPServerTransport({
          sessionIdGenerator: () => randomUUID(),
          onsessioninitialized: (sessionId) => {
            // Store the transport by session ID
            transports[sessionId] = transport
          },
        })

        // Clean up transport when closed
        transport.onclose = () => {
          if (transport.sessionId) {
            delete transports[transport.sessionId]
          }
        }

        await mcpServer.connect(transport)
      } else {
        httpSetJsonResponse(res, 400, 'Bad Request: No valid session ID provided', -32000)
        return
      }

      // Handle the request
      await transport.handleRequest(req, res, body)
    } else if (match('GET', '/mcp')) {
      // Handle server-to-client notifications
      await handleSessionRequest()
    } else if (match('DELETE', '/mcp')) {
      // Handle requests for session termination
      await handleSessionRequest()
    } else if (pathMatch) {
      httpSetTextResponse(res, 405, 'Method not allowed')
    } else {
      httpSetTextResponse(res, 404, 'Page not found')
    }
  })

  server.listen(port, () => {
    console.log(`Listening on port ${port}`)
  })
}

/*
 * Run the MCP server using the Stdio transport.
 */
async function runStdio(deps: string[], returnMode: string) {
  const mcpServer = createServer(deps, returnMode)
  const transport = new StdioServerTransport()
  await mcpServer.connect(transport)
}

/*
 * Run pyodide to download and install dependencies.
 */
async function installDeps(deps: string[]) {
  const runCode = new RunCode()
  const result = await runCode.run(
    deps,
    undefined,
    (level, data) => console.error(`${level}|${data}`),
  )
  if (result.status !== 'success') {
    console.error('error|Failed to install dependencies')
    Deno.exit(1)
  }
}

/*
 * Run a short example script that requires numpy.
 */
async function example(deps: string[]) {
  console.error(
    `Running example script for MCP Run Python version ${VERSION}...`,
  )
  const code = `
import numpy
a = numpy.array([1, 2, 3])
print('numpy array:', a)
a
`
  const runCode = new RunCode()
  const result = await runCode.run(
    deps,
    { name: 'example.py', content: code },
    // use warn to avoid recursion since console.log is patched in runCode
    (level, data) => console.warn(`${level}: ${data}`),
  )
  console.log('Tool return value:')
  console.log(asXml(result))
}

// list of log levels to use for level comparison
const LogLevels: LoggingLevel[] = [
  'debug',
  'info',
  'notice',
  'warning',
  'error',
  'critical',
  'alert',
  'emergency',
]

await main()
