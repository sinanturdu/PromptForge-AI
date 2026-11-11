import { test, expect } from './local-spa-fixture'

test('built SPA and workflow API share the verified local origin', async ({ page, request }) => {
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
})
