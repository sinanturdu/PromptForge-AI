import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { randomUUID } from 'node:crypto'
import { test, expect } from '@playwright/test'

test.use({ baseURL: 'http://127.0.0.1:18081' })

test('built SPA and workflow API share the verified local origin', async ({ page, request }) => {
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
  api.stdout.on('data', (chunk: Buffer) => { log = (log + chunk.toString()).slice(-8000) })
  api.stderr.on('data', (chunk: Buffer) => { log = (log + chunk.toString()).slice(-8000) })
  const closed = once(api, 'close')
  void closed.catch(() => {})
  try {
    await expect.poll(async () => {
      if (api.exitCode !== null) throw new Error(`SPA E2E server stopped: ${log}`)
      try { return (await (await request.get('/api/v1/health', { timeout: 1000 })).json()).service }
      catch { return null }
    }, { timeout: 30_000 }).toBe(service)
    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'Yeni bir prompt oluştur' })).toBeVisible()
    await page.getByRole('link', { name: 'Prompt Geçmişi', exact: true }).click()
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Prompt Geçmişi', exact: true })).toBeVisible()
    const outcome = await page.evaluate(async () => {
      const post = async (url: string, body: object) => fetch(url, { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), cache: 'no-store' })
      const source = await (await post('/api/v1/prompt-requests', { raw_prompt: 'Explain Python 3.12. Keep Merhaba.' })).json() as { id: string }
      const created = await post(`/api/v1/prompt-requests/${source.id}/runs`, { client_idempotency_key: 'browser-spa-1' })
      const run = await created.json() as { workflow: { id: string } }
      const start = await post(`/api/v1/runs/${run.workflow.id}/execution`, { expected_revision: 0 })
      return { created: created.status, start: start.status, id: run.workflow.id,
        location: start.headers.get('Location'), cache: start.headers.get('Cache-Control') }
    })
    expect(outcome.created).toBe(201)
    expect(outcome.start).toBe(202)
    expect(outcome.cache).toBe('no-store')
    expect(outcome.location).toBe(`/api/v1/runs/${outcome.id}`)
    await expect.poll(async () => (await (await request.get(outcome.location!)).json()).status).toBe('COMPLETED')
    const result = await request.get(`/api/v1/runs/${outcome.id}/result`)
    expect((await result.json()).workflow.selected.quality_status).toBe('qualified')
    expect(result.headers()['cache-control']).toBe('no-store')
    expect((await request.get('/api/v1/not-a-route')).status()).toBe(404)
    expect((await request.get('/.env')).status()).toBe(404)
    const denied = await request.post(`/api/v1/runs/${outcome.id}/execution`, {
      data: { expected_revision: 0 }, headers: { Origin: 'http://evil.invalid' },
    })
    expect(denied.status()).toBe(403)
  } finally {
    if (api.exitCode === null && api.signalCode === null) api.kill()
    await closed
    await rm(temporary, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 })
  }
})
