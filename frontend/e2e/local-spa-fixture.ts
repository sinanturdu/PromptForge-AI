import { execFile, spawn } from 'node:child_process'
import { promisify } from 'node:util'
import { once } from 'node:events'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { randomUUID } from 'node:crypto'
import { test as base, expect } from '@playwright/test'

interface LocalSpa { readonly origin: string; readonly operationCount: () => number; readonly release: () => void
  readonly invalidateSelection: (workflowId: string) => Promise<void>
  readonly restoreSelection: (workflowId: string, versionId: string) => Promise<void> }
export const test = base.extend<{ localSpa: LocalSpa; spaScenario: 'early' | 'three-round' | 'failed' | 'clarify' | 'not-qualified' | 'unassessable'; spaControlled: boolean }>({
  spaScenario: ['early', { option: true }],
  spaControlled: [false, { option: true }],
  baseURL: async ({ localSpa }, provide) => { await provide(localSpa.origin) },
  localSpa: [async ({ playwright, spaScenario, spaControlled }, provide) => {
    const frontend = fileURLToPath(new URL('../', import.meta.url))
    const backend = path.resolve(frontend, '../backend')
    const python = process.env.PROMPTFORGE_E2E_PYTHON || path.join(backend, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
    const temporary = await mkdtemp(path.join(tmpdir(), 'promptforge-spa-e2e-'))
    const service = `spa-e2e-${randomUUID()}`
    const api = spawn(python, [path.join(frontend, 'e2e/serve_local_spa.py'), path.join(temporary, 'e2e.sqlite3'), service, spaScenario, spaControlled ? 'controlled' : 'immediate'], {
      cwd: backend, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' },
    })
    let log = ''
    let origin: string | undefined
    let spawnError: Error | undefined
    let operations = 0
    api.on('error', error => { spawnError = error })
    api.stdout.on('data', (chunk: Buffer) => {
      log = (log + chunk.toString()).slice(-8000)
      const port = log.match(/^PROMPTFORGE_SPA_E2E_PORT=(\d+)\r?$/m)?.[1]
      if (port && Number(port) > 0 && Number(port) <= 65535) origin = `http://127.0.0.1:${port}`
      for (const event of log.matchAll(/^PROMPTFORGE_SPA_E2E_CALL=(\d+)\r?$/gm)) operations = Math.max(operations, Number(event[1]))
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
      async function setSelection(workflowId: string, versionId: string | null) {
        // Test-only corruption/restoration in this fixture's owned disposable DB.
        // This is operator fixture setup, never an application repair endpoint.
        await promisify(execFile)(python, ['-c', [
          'import sqlite3, sys', 'from uuid import UUID',
          'version = None if sys.argv[3] == "none" else UUID(sys.argv[3]).hex',
          'with sqlite3.connect(sys.argv[1]) as connection:',
          '    changed = connection.execute("UPDATE workflow_runs SET final_version_id = ? WHERE id = ? AND status = ?", (version, UUID(sys.argv[2]).hex, "COMPLETED"))',
          '    assert changed.rowcount == 1, "Expected one completed disposable workflow."',
        ].join('\n'), path.join(temporary, 'e2e.sqlite3'), workflowId, versionId ?? 'none'], { windowsHide: true })
      }
      await provide({ origin: origin!, operationCount: () => operations, release: () => { api.stdin.write('release\n') },
        invalidateSelection: workflowId => setSelection(workflowId, null), restoreSelection: setSelection })
    } finally {
      await probe.dispose()
      api.stdin.end()
      if (api.exitCode === null && api.signalCode === null) api.kill()
      await closed
      await rm(temporary, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 })
    }
  }, { auto: true }],
})

export { expect }
