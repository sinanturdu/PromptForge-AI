import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { randomUUID } from 'node:crypto'
import { expect } from '@playwright/test'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { createServer, type ViteDevServer } from 'vite'

export default async function globalSetup() {
  const frontend = fileURLToPath(new URL('../', import.meta.url))
  const backend = path.resolve(frontend, '../backend')
  const python = path.join(backend, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  const temporaryDirectory = await mkdtemp(path.join(tmpdir(), 'promptforge-e2e-'))
  const database = path.join(temporaryDirectory, 'e2e.sqlite3')
  const service = `promptforge-e2e-${randomUUID()}`
  let vite: ViteDevServer | undefined
  let backendError: Error | undefined
  let backendLog = ''
  const api = spawn(python, [path.join(frontend, 'e2e/serve_backend.py'), database, service], {
    cwd: backend,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' },
  })
  api.on('error', (error) => { backendError = error })
  api.stdout.on('data', (chunk: Buffer) => { backendLog = (backendLog + chunk.toString()).slice(-8000) })
  api.stderr.on('data', (chunk: Buffer) => { backendLog = (backendLog + chunk.toString()).slice(-8000) })
  const closed = once(api, 'close')
  // Attach immediately so startup failures cannot produce an unhandled rejection.
  void closed.catch(() => {})

  async function cleanup() {
    try {
      await vite?.close()
    } finally {
      if (api.exitCode === null && api.signalCode === null && !backendError) api.kill('SIGTERM')
      await closed.catch(() => {})
      // Only remove the exact directory created above, after the DB process has exited.
      await rm(temporaryDirectory, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
    }
  }

  try {
    await expect.poll(async () => {
      if (backendError || api.exitCode !== null || api.signalCode !== null) {
        throw new Error(`E2E backend failed: ${backendError?.message ?? backendLog}`)
      }
      try {
        const response = await fetch('http://127.0.0.1:18080/api/v1/health', {
          signal: AbortSignal.timeout(1000),
        })
        const health = await response.json() as { service?: string }
        return response.ok && health.service === service
      } catch {
        return false
      }
    }, { timeout: 30_000, message: 'Wait for this run’s dedicated E2E backend' }).toBe(true)

    vite = await createServer({
      root: frontend,
      configFile: false,
      envDir: false,
      mode: 'e2e',
      plugins: [react(), tailwindcss()],
      // Override even an inherited VITE_API_BASE_URL: browsers must use this proxy.
      define: { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify('') },
      server: {
        host: '127.0.0.1',
        port: 15173,
        strictPort: true,
        open: false,
        proxy: { '/api': { target: 'http://127.0.0.1:18080', changeOrigin: true } },
      },
    })
    await vite.listen()
    console.log(`[E2E] disposable database: ${database}`)
    return cleanup
  } catch (error) {
    await cleanup()
    throw new Error(`E2E setup failed. ${backendLog}`, { cause: error })
  }
}
