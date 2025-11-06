// deno-lint-ignore-file no-explicit-any
import { loadPyodide, type PyodideInterface } from 'pyodide'
import { preparePythonCode } from './prepareEnvCode.ts'
import { randomBytes } from 'node:crypto'
import mime from 'mime-types'
import { encodeBase64 } from '@std/encoding/base64'
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
  output: string[]
}

interface PyodideWorker {
  id: number

  pyodide: PyodideInterface
  sys: any
  prepareStatus: PrepareSuccess | PrepareError | undefined
  preparePyEnv: PreparePyEnv
  output: string[]

  inUse: boolean
}

class PyodideAccess {
  // function to get a pyodide instance (with timeout & max members)
  public async withPyodideInstance<T>(
    dependencies: string[],
    log: (level: LoggingLevel, data: string) => void,
    maximumInstances: number,
    waitTimeoutMs: number,
    fn: (w: PyodideWorker) => Promise<T>,
  ): Promise<T> {
    const w = await this.getPyodideInstance(dependencies, log, maximumInstances, waitTimeoutMs)
    try {
      return await fn(w)
    } finally {
      this.releasePyodideInstance(w.id)
    }
  }

  private pyodideInstances: { [workerId: number]: PyodideWorker } = {}
  private nextWorkerId = 1
  private creatingCount = 0

  private waitQueue: {
    resolve: (w: PyodideWorker) => void
    reject: (e: unknown) => void
    timer: ReturnType<typeof setTimeout>
  }[] = []

  private tryAcquireFree(): PyodideWorker | undefined {
    for (const w of Object.values(this.pyodideInstances)) {
      if (!w.inUse) {
        w.inUse = true
        return w
      }
    }
    return undefined
  }

  private async createPyodideWorker(
    id: number,
    dependencies: string[],
    log: (level: LoggingLevel, data: string) => void,
  ): Promise<PyodideWorker> {
    // if (this.pyodide && this.preparePyEnv) {
    //   pyodide = this.pyodide
    //   preparePyEnv = this.preparePyEnv
    //   sys = pyodide.pyimport('sys')
    // } else {
    //   if (!this.prepPromise) {
    //     this.prepPromise = this.prepEnv(dependencies, log)
    //   }
    //   // TODO is this safe if the promise has already been accessed? it seems to work fine
    //   const prep = await this.prepPromise
    //   pyodide = prep.pyodide
    //   preparePyEnv = prep.preparePyEnv
    //   sys = prep.sys
    //   prepareStatus = prep.prepareStatus
    // }

    const prepPromise = this.prepEnv(dependencies, log)
    const prep = await prepPromise
    return {
      id,
      pyodide: prep.pyodide,
      sys: prep.sys,
      prepareStatus: prep.prepareStatus,
      preparePyEnv: prep.preparePyEnv,
      output: prep.output,
      inUse: false,
    }
  }

  private async prepEnv(
    dependencies: string[],
    log: (level: LoggingLevel, data: string) => void,
  ): Promise<PrepResult> {
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
        messageCallback: (msg: string) => log('debug', msg),
        errorCallback: (msg: string) => {
          log('error', msg)
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
      output,
    }
  }

  private releasePyodideInstance(workerId: number): void {
    const worker = this.pyodideInstances[workerId]
    if (!worker) return

    // if sb is waiting, take the first worker from queue & keep status as "inUse"
    const waiter = this.waitQueue.shift()
    if (waiter) {
      clearTimeout(waiter.timer)
      worker.inUse = true
      waiter.resolve(worker)
    } else {
      worker.inUse = false
    }
  }

  private async getPyodideInstance(
    dependencies: string[],
    log: (level: LoggingLevel, data: string) => void,
    maximumInstances: number,
    waitTimeoutMs: number,
  ): Promise<PyodideWorker> {
    // 1) if possible, take a free - already inititalised - worker
    const free = this.tryAcquireFree()
    if (free) return free

    // 2) if none is free, check that we are not over capacity already
    const currentCount = Object.keys(this.pyodideInstances).length
    if (currentCount + this.creatingCount < maximumInstances) {
      this.creatingCount++
      try {
        const id = this.nextWorkerId++
        const worker = await this.createPyodideWorker(id, dependencies, log)

        // cool, created a new one so let's use that one
        worker.inUse = true
        this.pyodideInstances[id] = worker
        return worker
      } catch (err) {
        // Need to make sure the creation gets reduced again, so simply re-throwing
        throw err
      } finally {
        this.creatingCount--
      }
    }

    // 3) we have the maximum worker, wait until timeout until some is free
    return await new Promise<PyodideWorker>((resolve, reject) => {
      const timer = setTimeout(() => {
        const idx = this.waitQueue.findIndex((q) => q.resolve === resolve)
        if (idx >= 0) this.waitQueue.splice(idx, 1)
        reject(new Error('Timeout: no free Pyodide worker'))
      }, waitTimeoutMs)

      this.waitQueue.push({ resolve, reject, timer })
    })
  }
}

export class RunCode {
  private pyodideAccess: PyodideAccess = new PyodideAccess()

  async run(
    dependencies: string[],
    log: (level: LoggingLevel, data: string) => void,
    file?: CodeFile,
    globals?: Record<string, any>,
    alwaysReturnJson: boolean = false,
    enableFileOutputs: boolean = false,
    pyodideMaximumInstances: number = 5,
    pyodideWaitTimeoutMs: number = 60_000,
  ): Promise<RunSuccess | RunError> {
    // get a pyodide instance for this job
    return await this.pyodideAccess.withPyodideInstance(
      dependencies,
      log,
      pyodideMaximumInstances,
      pyodideWaitTimeoutMs,
      async (pyodideWorker) => {
        if (pyodideWorker.prepareStatus && pyodideWorker.prepareStatus.kind == 'error') {
          return {
            status: 'install-error',
            output: this.takeOutput(pyodideWorker),
            error: pyodideWorker.prepareStatus.message,
          }
        } else if (file) {
          try {
            // defaults in case file output is not enabled
            let folderPath = ''
            let files: Resource[] = []

            if (enableFileOutputs) {
              // make the temp file system for pyodide to use
              const folderName = randomBytes(20).toString('hex').slice(0, 20)
              folderPath = `./output_files/${folderName}`
              await Deno.mkdir(folderPath, { recursive: true })
              pyodideWorker.pyodide.mountNodeFS('/output_files', folderPath)
            }

            // run the code with pyodide
            const rawValue = await pyodideWorker.pyodide.runPythonAsync(file.content, {
              globals: pyodideWorker.pyodide.toPy({ ...(globals || {}), __name__: '__main__' }),
              filename: file.name,
            })

            if (enableFileOutputs) {
              // check files that got saved
              files = await this.readAndDeleteFiles(folderPath)
              pyodideWorker.pyodide.FS.unmount('/output_files')
            }

            // label the worker as free again
            pyodideWorker.inUse = false

            return {
              status: 'success',
              output: this.takeOutput(pyodideWorker),
              returnValueJson: pyodideWorker.preparePyEnv.dump_json(rawValue, alwaysReturnJson),
              embeddedResources: files,
            }
          } catch (err) {
            pyodideWorker.pyodide.FS.unmount('/output_files')
            pyodideWorker.inUse = false
            console.log(err)
            return {
              status: 'run-error',
              output: this.takeOutput(pyodideWorker),
              error: formatError(err),
            }
          }
        } else {
          pyodideWorker.inUse = false
          return {
            status: 'success',
            output: this.takeOutput(pyodideWorker),
            returnValueJson: null,
            embeddedResources: [],
          }
        }
      },
    )
  }

  async readAndDeleteFiles(folderPath: string): Promise<Resource[]> {
    const results: Resource[] = []
    for await (const file of Deno.readDir(folderPath)) {
      // Skip directories
      if (!file.isFile) continue

      const fileName = file.name
      const filePath = `${folderPath}/${fileName}`
      const mimeType = mime.lookup(fileName)
      const fileData = await Deno.readFile(filePath)

      // Convert binary to Base64
      const base64Encoded = encodeBase64(fileData)

      results.push({
        name: fileName,
        mimeType: mimeType,
        blob: base64Encoded,
      })
    }

    // Now delete the file folder - otherwise they add up :)
    await Deno.remove(folderPath, { recursive: true })

    return results
  }

  private takeOutput(pyodideWorker: PyodideWorker): string[] {
    pyodideWorker.sys.stdout.flush()
    pyodideWorker.sys.stderr.flush()
    const output = pyodideWorker.output
    pyodideWorker.output = []
    return output
  }
}

interface Resource {
  name: string
  mimeType: string
  blob: string
}

interface RunSuccess {
  status: 'success'
  // we could record stdout and stderr separately, but I suspect simplicity is more important
  output: string[]
  returnValueJson: string | null
  embeddedResources: Resource[]
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
    / {2}File "\/lib\/python\d+\.zip\/_pyodide\/.*\n {4}.*\n(?: {4}.*\n)*/g,
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
  dump_json: (value: any, always_return_json: boolean) => string | null
}
