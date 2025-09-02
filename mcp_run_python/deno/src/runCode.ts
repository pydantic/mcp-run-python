// deno-lint-ignore-file no-explicit-any
import { loadPyodide, type PyodideInterface } from 'pyodide'
import { preparePythonCode } from './prepareEnvCode.ts'
import type { LoggingLevel } from '@modelcontextprotocol/sdk/types.js'

export interface CodeFile {
  name: string
  content: string
}

interface PrepResult {
  pyodide: PyodideInterface
  preparePyEnv: PreparePyEnv
  sys: any
  prepareStatus: PrepareSuccess | PrepareError
}

export class RunCode {
  private pyodide?: PyodideInterface
  private preparePyEnv?: PreparePyEnv
  private prepPromise?: Promise<PrepResult>

  async run(
    dependencies: string[],
    file: CodeFile | undefined,
    log: (level: LoggingLevel, data: string) => void,
  ): Promise<RunSuccess | RunError> {
    // remove once we can upgrade to pyodide 0.27.7 and console.log is no longer used.
    const realConsoleLog = console.log
    console.log = (...args: any[]) => log('debug', args.join(' '))

    const output: string[] = []
    let pyodide: PyodideInterface
    let sys: any
    let prepareStatus: PrepareSuccess | PrepareError | undefined
    let preparePyEnv: PreparePyEnv
    if (this.pyodide && this.preparePyEnv) {
      pyodide = this.pyodide
      preparePyEnv = this.preparePyEnv
      sys = pyodide.pyimport('sys')
    } else {
      if (!this.prepPromise) {
        this.prepPromise = prepEnv(dependencies, log, output)
      }
      // TODO is this safe if the promise has already been accessed? it seems to work fine
      const prep = await this.prepPromise
      pyodide = prep.pyodide
      preparePyEnv = prep.preparePyEnv
      sys = prep.sys
      prepareStatus = prep.prepareStatus
    }

    let runResult: RunSuccess | RunError
    if (prepareStatus && prepareStatus.kind == 'error') {
      sys.stdout.flush()
      sys.stderr.flush()
      runResult = {
        status: 'install-error',
        output,
        error: prepareStatus.message,
      }
    } else if (file) {
      try {
        const rawValue = await pyodide.runPythonAsync(file.content, {
          globals: pyodide.toPy({ __name__: '__main__' }),
          filename: file.name,
        })
        sys.stdout.flush()
        sys.stderr.flush()
        runResult = {
          status: 'success',
          output,
          returnValueJson: preparePyEnv.dump_json(rawValue),
        }
      } catch (err) {
        sys.stdout.flush()
        sys.stderr.flush()
        runResult = {
          status: 'run-error',
          output,
          error: formatError(err),
        }
      }
    } else {
      sys.stdout.flush()
      sys.stderr.flush()
      runResult = {
        status: 'success',
        output,
        returnValueJson: null,
      }
    }
    console.log = realConsoleLog
    return runResult
  }
}

async function prepEnv(
  dependencies: string[],
  log: (level: LoggingLevel, data: string) => void,
  output: string[],
): Promise<PrepResult> {
  const pyodide = await loadPyodide({
    stdout: (msg) => {
      log('info', msg)
      output.push(msg)
    },
    stderr: (msg) => {
      log('warning', msg)
      output.push(msg)
    },
  })

  // see https://github.com/pyodide/pyodide/discussions/5512
  const origLoadPackage = pyodide.loadPackage
  pyodide.loadPackage = (pkgs, options) =>
    origLoadPackage(pkgs, {
      // stop pyodide printing to stdout/stderr
      messageCallback: (msg: string) => log('debug', `loadPackage: ${msg}`),
      errorCallback: (msg: string) => {
        log('error', `loadPackage: ${msg}`)
        output.push(`install error: ${msg}`)
      },
      ...options,
    })

  await pyodide.loadPackage(['micropip', 'pydantic'])
  const sys = pyodide.pyimport('sys')

  const dirPath = '/tmp/mcp_run_python'
  sys.path.append(dirPath)
  const pathlib = pyodide.pyimport('pathlib')
  pathlib.Path(dirPath).mkdir()
  const moduleName = '_prepare_env'

  pathlib.Path(`${dirPath}/${moduleName}.py`).write_text(preparePythonCode)

  const preparePyEnv: PreparePyEnv = pyodide.pyimport(moduleName)

  const prepareStatus = await preparePyEnv.prepare_env(pyodide.toPy(dependencies))
  return {
    pyodide,
    preparePyEnv,
    sys,
    prepareStatus,
  }
}

export async function runCode(
  dependencies: string[],
  file: CodeFile | undefined,
  log: (level: LoggingLevel, data: string) => void,
): Promise<RunSuccess | RunError> {
  // remove once we can upgrade to pyodide 0.27.7 and console.log is no longer used.
  const realConsoleLog = console.log
  console.log = (...args: any[]) => log('debug', args.join(' '))

  const output: string[] = []
  const pyodide = await loadPyodide({
    stdout: (msg) => {
      log('info', msg)
      output.push(msg)
    },
    stderr: (msg) => {
      log('warning', msg)
      output.push(msg)
    },
  })

  // see https://github.com/pyodide/pyodide/discussions/5512
  const origLoadPackage = pyodide.loadPackage
  pyodide.loadPackage = (pkgs, options) =>
    origLoadPackage(pkgs, {
      // stop pyodide printing to stdout/stderr
      messageCallback: (msg: string) => log('debug', `loadPackage: ${msg}`),
      errorCallback: (msg: string) => {
        log('error', `loadPackage: ${msg}`)
        output.push(`install error: ${msg}`)
      },
      ...options,
    })

  await pyodide.loadPackage(['micropip', 'pydantic'])
  const sys = pyodide.pyimport('sys')

  const dirPath = '/tmp/mcp_run_python'
  sys.path.append(dirPath)
  const pathlib = pyodide.pyimport('pathlib')
  pathlib.Path(dirPath).mkdir()
  const moduleName = '_prepare_env'

  pathlib.Path(`${dirPath}/${moduleName}.py`).write_text(preparePythonCode)

  const preparePyEnv: PreparePyEnv = pyodide.pyimport(moduleName)

  const prepareStatus = await preparePyEnv.prepare_env(pyodide.toPy(dependencies))
  let runResult: RunSuccess | RunError
  if (prepareStatus.kind == 'error') {
    runResult = {
      status: 'install-error',
      output,
      error: prepareStatus.message,
    }
  } else if (file) {
    try {
      const rawValue = await pyodide.runPythonAsync(file.content, {
        globals: pyodide.toPy({ __name__: '__main__' }),
        filename: file.name,
      })
      runResult = {
        status: 'success',
        output,
        returnValueJson: preparePyEnv.dump_json(rawValue),
      }
    } catch (err) {
      runResult = {
        status: 'run-error',
        output,
        error: formatError(err),
      }
    }
  } else {
    runResult = {
      status: 'success',
      output,
      returnValueJson: null,
    }
  }
  sys.stdout.flush()
  sys.stderr.flush()
  console.log = realConsoleLog
  return runResult
}

interface RunSuccess {
  status: 'success'
  // we could record stdout and stderr separately, but I suspect simplicity is more important
  output: string[]
  returnValueJson: string | null
}

interface RunError {
  status: 'install-error' | 'run-error'
  output: string[]
  error: string
}

export function asXml(runResult: RunSuccess | RunError): string {
  const xml = [`<status>${runResult.status}</status>`]
  if (runResult.output.length) {
    xml.push('<output>')
    const escapeXml = escapeClosing('output')
    xml.push(...runResult.output.map(escapeXml))
    xml.push('</output>')
  }
  if (runResult.status == 'success') {
    if (runResult.returnValueJson) {
      xml.push('<return_value>')
      xml.push(escapeClosing('return_value')(runResult.returnValueJson))
      xml.push('</return_value>')
    }
  } else {
    xml.push('<error>')
    xml.push(escapeClosing('error')(runResult.error))
    xml.push('</error>')
  }
  return xml.join('\n')
}

export function asJson(runResult: RunSuccess | RunError): string {
  const { status, output } = runResult
  const json: Record<string, any> = { status, output }
  if (runResult.status == 'success') {
    json.return_value = JSON.parse(runResult.returnValueJson || 'null')
  } else {
    json.error = runResult.error
  }
  return JSON.stringify(json)
}

function escapeClosing(closingTag: string): (str: string) => string {
  const regex = new RegExp(`</?\\s*${closingTag}(?:.*?>)?`, 'gi')
  const onMatch = (match: string) => {
    return match.replace(/</g, '&lt;').replace(/>/g, '&gt;')
  }
  return (str) => str.replace(regex, onMatch)
}

function formatError(err: any): string {
  let errStr = err.toString()
  errStr = errStr.replace(/^PythonError: +/, '')
  // remove frames from inside pyodide
  errStr = errStr.replace(
    / {2}File "\/lib\/python\d+\.zip\/_pyodide\/.*\n {4}.*\n(?: {4,}\^+\n)?/g,
    '',
  )
  return errStr
}

interface PrepareSuccess {
  kind: 'success'
  dependencies?: string[]
}
interface PrepareError {
  kind: 'error'
  message: string
}
interface PreparePyEnv {
  prepare_env: (files: CodeFile[]) => Promise<PrepareSuccess | PrepareError>
  dump_json: (value: any) => string | null
}
