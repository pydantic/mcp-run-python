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

interface PyodideWorker extends PrepResult {
  id: number
  pyodideInterruptBuffer: Uint8Array
  inUse: boolean
}

/*
 * Class that instanciates pyodide and keeps multiple instances
 * There need to be multiple instances, as file system mounting to a standard directory (which is needed for the LLM), cannot be easily done without mixing files
 * Now, every process has their own file system & they get looped around
 */
class PyodideAccess {
  // context manager to get a pyodide instance (with timeout & max members)
  public async withPyodideInstance<T>(
    dependencies: string[],
    log: (level: LoggingLevel, data: string) => void,
    maximumInstances: number,
    pyodideWorkerWaitTimeoutSec: number,
    fn: (w: PyodideWorker) => Promise<T>,
  ): Promise<T> {
    const w = await this.getPyodideInstance(dependencies, log, maximumInstances, pyodideWorkerWaitTimeoutSec)
    try {
      return await fn(w)
    } finally {
      this.releasePyodideInstance(w.id)
    }
  }

  private pyodideInstances: { [workerId: number]: PyodideWorker } = {}
  private nextWorkerId = 1
  private creatingCount = 0

  // after code is run, this releases the worker again to the pool for other codes to run
  private releasePyodideInstance(workerId: number): void {
    const worker = this.pyodideInstances[workerId]

    // clear interrupt buffer in case it was used
    worker.pyodideInterruptBuffer[0] = 0

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

  // main logic of getting a pyodide instance. Will re-use if possible, otherwise create (up to limit)
  private async getPyodideInstance(
    dependencies: string[],
    log: (level: LoggingLevel, data: string) => void,
    maximumInstances: number,
    pyodideWorkerWaitTimeoutSec: number,
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

    // 3) we have the maximum worker, poll periodically until timeout for a free one
    return await new Promise<PyodideWorker>((resolve, reject) => {
      const start = Date.now()
      const poll = () => {
        const free = this.tryAcquireFree()
        if (free) {
          clearTimeout(timer)
          resolve(free)
          return
        }
        if (Date.now() - start >= pyodideWorkerWaitTimeoutSec * 1000) {
          reject(new Error('Timeout: no free Pyodide worker'))
          return
        }
        timer = setTimeout(poll, 1000)
      }
      let timer = setTimeout(poll, 1000)
      this.waitQueue.push({ resolve, reject, timer })
    })
  }

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

  // this creates the pyodide worker from scratch
  private async createPyodideWorker(
    id: number,
    dependencies: string[],
    log: (level: LoggingLevel, data: string) => void,
  ): Promise<PyodideWorker> {
    const prepPromise = this.prepEnv(dependencies, log)
    const prep = await prepPromise

    // setup the interrupt buffer to be able to cancel the task
    let interruptBuffer = new Uint8Array(new SharedArrayBuffer(1))
    prep.pyodide.setInterruptBuffer(interruptBuffer)

    return {
      id,
      pyodide: prep.pyodide,
      pyodideInterruptBuffer: interruptBuffer,
      sys: prep.sys,
      prepareStatus: prep.prepareStatus,
      preparePyEnv: prep.preparePyEnv,
      output: prep.output,
      inUse: false,
    }
  }

  // load pyodide and install dependencies
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
    pyodideMaxWorkers: number = 10,
    pyodideCodeRunTimeoutSec: number = 60,
    pyodideWorkerWaitTimeoutSec: number = 60,
  ): Promise<RunSuccess | RunError> {
    // get a pyodide instance for this job
    try {
      return await this.pyodideAccess.withPyodideInstance(
        dependencies,
        log,
        pyodideMaxWorkers,
        pyodideWorkerWaitTimeoutSec,
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

              // run the code with pyodide including a timeout
              let timeoutId: any
              const rawValue = await Promise.race([
                pyodideWorker.pyodide.runPythonAsync(file.content, {
                  globals: pyodideWorker.pyodide.toPy({ ...(globals || {}), __name__: '__main__' }),
                  filename: file.name,
                }),
                new Promise((_, reject) => {
                  timeoutId = setTimeout(() => {
                    // after the time passes signal SIGINT to stop execution
                    // 2 stands for SIGINT
                    pyodideWorker.pyodideInterruptBuffer[0] = 2
                    reject(new Error(`Timeout exceeded for python execution (${pyodideCodeRunTimeoutSec} sec)`))
                  }, pyodideCodeRunTimeoutSec * 1000)
                }),
              ])
              clearTimeout(timeoutId)

              if (enableFileOutputs) {
                // check files that got saved
                files = await this.readAndDeleteFiles(folderPath)
                pyodideWorker.pyodide.FS.unmount('/output_files')
              }

              return {
                status: 'success',
                output: this.takeOutput(pyodideWorker),
                returnValueJson: pyodideWorker.preparePyEnv.dump_json(rawValue, alwaysReturnJson),
                embeddedResources: files,
              }
            } catch (err) {
              try {
                pyodideWorker.pyodide.FS.unmount('/output_files')
              } catch (_) {}

              console.log(err)
              return {
                status: 'run-error',
                output: this.takeOutput(pyodideWorker),
                error: formatError(err),
              }
            }
          } else {
            return {
              status: 'success',
              output: this.takeOutput(pyodideWorker),
              returnValueJson: null,
              embeddedResources: [],
            }
          }
        },
      )
    } catch (err) {
      console.error(err)
      return {
        status: 'fatal-runtime-error',
        output: [],
        error: formatError(err),
      }
    }
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
  status: 'install-error' | 'run-error' | 'fatal-runtime-error'
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
