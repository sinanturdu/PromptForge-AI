import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { randomUUID } from 'node:crypto'
import { test as base, expect } from '@playwright/test'

export const test = base.extend<{ localSpa: string }>({
  baseURL: async ({ localSpa }, provide) => { await provide(localSpa) },
  localSpa: [async ({ playwright }, provide) => {
    const frontend = fileURLToPath(new URL('../', import.meta.url))
    const backend = path.resolve(frontend, '../backend')
    const python = process.env.PROMPTFORGE_E2E_PYTHON || path.join(backend, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
    const temporary = await mkdtemp(path.join(tmpdir(), 'promptforge-spa-e2e-'))
    const service = `spa-e2e-${randomUUID()}`
    const api = spawn(python, [path.join(frontend, 'e2e/serve_local_spa.py'), path.join(temporary, 'e2e.sqlite3'), service], {
      cwd: backend, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' },
    })
    let log = ''
    let origin: string | undefined
    let spawnError: Error | undefined
    api.on('error', error => { spawnError = error })
    api.stdout.on('data', (chunk: Buffer) => {
      log = (log + chunk.toString()).slice(-8000)
      const port = log.match(/^PROMPTFORGE_SPA_E2E_PORT=(\d+)\r?$/m)?.[1]
      if (port && Number(port) > 0 && Number(port) <= 65535) origin = `http://127.0.0.1:${port}`
    })
    api.stderr.on('data', (chunk: Buffer) => { log = (log + chunk.toString()).slice(-8000) })
    const closed = once(api, 'close')
    void closed.catch(() => {})
    const probe = await playwright.request.newContext()
    try {
      await expect.poll(async () => {
        if (spawnError || api.exitCode !== null) throw new Error(`SPA E2E server stopped: ${spawnError?.message ?? log}`)
        if (!origin) return null
        try { return (await (await probe.get(`${origin}/api/v1/health`, { timeout: 1000 })).json()).service }
        catch { return null }
      }, { timeout: 30_000 }).toBe(service)
      await provide(origin!)
    } finally {
      await probe.dispose()
      if (api.exitCode === null && api.signalCode === null) api.kill()
      await closed
      await rm(temporary, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 })
    }
  }, { auto: true }],
})

export { expect }
