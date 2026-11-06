import { randomUUID } from 'node:crypto'
import type { Page } from '@playwright/test'
import { test, expect } from './local-spa-fixture'

const RAW = '  Explain Python 3.12. Keep Merhaba.\n```python\ndef f():\n    return 1\n```\n' + 'LongUnbrokenInput'.repeat(200) + '\n'
async function begin(page: Page) {
  const posts: string[] = []
  page.on('request', request => { if (request.method() === 'POST') posts.push(new URL(request.url()).pathname) })
  await page.goto('/')
  await page.getByLabel('Ham Prompt İsteği', { exact: false }).fill(RAW)
  await page.getByRole('button', { name: 'Prompt İsteğini Kaydet' }).click()
  await page.getByRole('button', { name: 'Optimizasyonu Yapılandır' }).click()
  const creation = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/runs'))
  await page.getByRole('button', { name: 'Workflow Oluştur', exact: true }).click()
  const workflow = (await (await creation).json()).workflow as { id: string; prompt_request_id: string }
  await page.getByRole('button', { name: 'Optimizasyonu Başlat', exact: true }).click()
  const monitor = page.getByRole('region', { name: 'Workflow İlerlemesi', exact: true })
  await monitor.getByRole('button', { name: 'Sonuç ve Geçmişi Görüntüle' }).click()
  const results = page.getByRole('region', { name: 'Sonuç ve Değerlendirme Geçmişi', exact: true })
  return { workflow, monitor, results, posts }
}

for (const [name, viewport] of [['desktop', { width: 1280, height: 900 }], ['mobile', { width: 375, height: 812 }]] as const) {
  test(`${name}: qualified result, exact clipboard, original comparison and refresh-safe read-only viewing`, async ({ page, context, localSpa }, testInfo) => {
    await page.setViewportSize(viewport)
    await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: localSpa.origin })
    const { results, posts } = await begin(page)
    await expect(results.getByText('Kalite sonucu: Kalite koşulları sağlandı.', { exact: true })).toBeVisible()
    await expect(results.getByLabel('Seçilen prompt metni')).toHaveText(RAW, { useInnerText: false })
    expect(await results.getByLabel('Seçilen prompt metni').textContent()).toBe(RAW)
    await results.getByRole('button', { name: "Prompt'u Kopyala" }).click()
    await expect(results.getByText('Prompt kopyalandı.', { exact: true })).toBeVisible()
    // The Windows clipboard transport returns native CRLF. The application sends
    // the unchanged LF string to writeText (also asserted by the component test).
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(process.platform === 'win32' ? RAW.replace(/\n/g, '\r\n') : RAW)
    await results.getByRole('region', { name: 'Seçilen prompt', exact: true }).screenshot({ path: testInfo.outputPath(`${name}-selected-prompt.png`) })
    await results.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' }).click()
    await expect(results.getByText('Seçilen metinler birebir aynı.', { exact: true })).toBeVisible()
    expect(await results.getByLabel('Sol prompt metni').textContent()).toBe(RAW)
    expect(await results.getByLabel('Sağ prompt metni').textContent()).toBe(RAW)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.reload()
    await expect(results.getByLabel('Seçilen prompt metni')).toBeVisible()
    expect(await results.getByLabel('Seçilen prompt metni').textContent()).toBe(RAW)
    await page.screenshot({ path: testInfo.outputPath(`${name}-workflow-result.png`), fullPage: true })
    expect(posts.filter(path => path.endsWith('/execution'))).toHaveLength(1)
    expect(posts).toHaveLength(3)
    expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).not.toContain('Keep Merhaba')
  })
}

test.describe('completed without qualification', () => {
  test.use({ spaScenario: 'not-qualified' })
  test('keeps backend selection independent of the last round and compares two stored versions', async ({ page, request }) => {
    const { workflow, results, posts } = await begin(page)
    await expect(results.getByText('Kalite sonucu: Kalite koşulları sağlanmadı.', { exact: true })).toBeVisible()
    await expect(results.getByText('Yürütme tamamlandı; sonuç bütünlüğü doğrulandı.', { exact: true })).toBeVisible()
    const result = await (await request.get(`/api/v1/runs/${workflow.id}/result`)).json()
    expect(result.workflow.selected.evaluation_round).toBe(1)
    expect(await results.getByLabel('Seçilen prompt metni').textContent()).toBe(result.candidate.rendered_prompt)
    await expect(results.getByRole('button', { name: "Prompt'u Kopyala" })).toBeEnabled()
    await results.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' }).click()
    const content = await (await request.get(`/api/v1/runs/${workflow.id}/history/content`)).json()
    // Identical actionable feedback stops further revision attempts early. Only
    // the two versions actually committed by that workflow can be compared.
    expect(content.versions).toHaveLength(2)
    expect(result.workflow.completed_rounds).toBe(2)
    await results.getByLabel('Sol prompt', { exact: true }).selectOption(content.versions[0].version.id)
    await results.getByLabel('Sağ prompt', { exact: true }).selectOption(content.versions[1].version.id)
    expect(await results.getByLabel('Sol prompt metni').textContent()).toBe(content.versions[0].rendered_prompt)
    expect(await results.getByLabel('Sağ prompt metni').textContent()).toBe(content.versions[1].rendered_prompt)
    await expect(results.getByText(/Seçilen metinler farklı/)).toBeVisible()
    expect(posts.filter(path => path.endsWith('/execution'))).toHaveLength(1)
  })
})

test.describe('unassessable evaluation', () => {
  test.use({ spaScenario: 'unassessable' })
  test('shows null scores as unassessable and does not invent extra rounds', async ({ page }) => {
    const { results } = await begin(page)
    await expect(results.getByText('Kalite sonucu: Değerlendirilemedi.', { exact: true })).toBeVisible()
    await expect(results.getByLabel('Açıklık puanı')).toHaveText('Değerlendirilemedi')
    await expect(results.getByText('Toplam puan: Değerlendirilemedi', { exact: true })).toBeVisible()
    await expect(results.getByText(/üst sınıra ulaşmadan sonlandı/)).toBeVisible()
    await expect(results.getByText(/Tur 2 · Kayıtlı değerlendirme/)).toHaveCount(0)
  })
})

test('selecting another workflow clears old content and reload never dispatches it', async ({ page, request, localSpa }) => {
  const { workflow, monitor, results, posts } = await begin(page)
  await expect(results.getByLabel('Seçilen prompt metni')).toBeVisible()
  const second = await request.post(`/api/v1/prompt-requests/${workflow.prompt_request_id}/runs`, { data: { client_idempotency_key: `test-${randomUUID()}` } })
  expect(second.status()).toBe(201)
  const otherId = (await second.json()).workflow.id as string
  await monitor.getByRole('button', { name: 'Kayıtlı Workflow’ları Görüntüle' }).click()
  await monitor.getByLabel('Bu promptun workflow’ları').selectOption(otherId)
  await expect(results.getByText(/Nihai sonuç henüz hazır değil/)).toBeVisible()
  await expect(results.getByLabel('Seçilen prompt metni')).toHaveCount(0)
  await expect(results.getByRole('button', { name: "Prompt'u Kopyala" })).toHaveCount(0)
  await page.reload()
  await expect(results.getByText(/Nihai sonuç henüz hazır değil/)).toBeVisible()
  expect(new URL(page.url()).searchParams.get('workflow')).toBe(otherId)
  expect(posts).toHaveLength(3)
  expect(localSpa.operationCount()).toBe(2)
})

for (const scenario of ['failed', 'clarify'] as const) {
  test.describe(`${scenario} result`, () => {
    test.use({ spaScenario: scenario })
    test('offers read-only history without a successful or copyable final prompt', async ({ page, request }) => {
      const { workflow, results, posts } = await begin(page)
      await expect(results.getByText(scenario === 'failed' ? 'Yürütme başarısız; başarılı bir optimizasyon sonucu sunulmuyor.' : 'Ek açıklama gerekiyor; nihai prompt sunulmuyor.', { exact: true })).toBeVisible()
      await expect(results.getByLabel('Seçilen prompt metni')).toHaveCount(0)
      await expect(results.getByRole('button', { name: "Prompt'u Kopyala" })).toHaveCount(0)
      if (scenario === 'clarify') {
        const stored = await (await request.get(`/api/v1/runs/${workflow.id}/result`)).json()
        await expect(results.getByRole('region', { name: 'Gerekli açıklamalar' })).toContainText(stored.clarification_questions[0])
      }
      expect(posts.filter(path => path.endsWith('/execution'))).toHaveLength(1)
    })
  })
}

test('integrity inconsistency revokes copying while keeping real evaluation metadata visible', async ({ page, request, localSpa }) => {
  const { workflow, monitor, results, posts } = await begin(page)
  await expect(results.getByRole('button', { name: "Prompt'u Kopyala" })).toBeEnabled()
  await localSpa.invalidateSelection(workflow.id)
  await monitor.getByRole('button', { name: 'Şimdi Yenile', exact: true }).click()
  await expect(results.getByLabel('Seçilen prompt metni')).toHaveCount(0)
  await expect(results.getByRole('button', { name: "Prompt'u Kopyala" })).toHaveCount(0)
  await results.getByRole('button', { name: 'Geçmiş Özetini Yenile' }).click()
  await expect(results.getByText('Tur 1 · Kayıtlı değerlendirme (doğrulanmadı)', { exact: true })).toBeVisible()
  expect((await request.get(`/api/v1/runs/${workflow.id}/result`)).status()).toBe(409)
  const actual = await (await request.get(`/api/v1/runs/${workflow.id}/history`)).json()
  expect(actual.evaluations).toHaveLength(1)
  expect(actual.workflow.findings.length).toBeGreaterThan(0)
  expect(localSpa.operationCount()).toBe(2)
  expect(posts.filter(path => path.endsWith('/execution'))).toHaveLength(1)
})

test('a new content-history integrity finding overrides an older cached result without a progress refresh', async ({ page, request, localSpa }) => {
  const { workflow, results, posts } = await begin(page)
  await expect(results.getByRole('button', { name: "Prompt'u Kopyala" })).toBeEnabled()
  await localSpa.invalidateSelection(workflow.id)
  const historyRead = page.waitForResponse(response => response.url().includes('/history/content?'))
  await results.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' }).click()
  const history = await historyRead
  expect(history.status()).toBe(200)
  const stored = await history.json()
  expect(stored.metadata.evaluations).toHaveLength(1)
  expect(stored.metadata.workflow.findings.length).toBeGreaterThan(0)
  await expect(results.getByRole('button', { name: "Prompt'u Kopyala" })).toHaveCount(0)
  await expect(results.getByLabel('Seçilen prompt metni')).toHaveCount(0)
  await expect(results.getByText(/Geçmiş içeriği bir bütünlük sorunu bildirdi/)).toBeVisible()
  await expect(results.getByText('Tur 1 · Kayıtlı değerlendirme', { exact: true })).toBeVisible()
  await results.getByRole('button', { name: 'Sonucu Yenile' }).click()
  await expect(results.getByRole('button', { name: "Prompt'u Kopyala" })).toHaveCount(0)
  expect((await request.get(`/api/v1/runs/${workflow.id}/result`)).status()).toBe(409)
  expect((await (await request.get(`/api/v1/runs/${workflow.id}/history`)).json()).evaluations).toHaveLength(1)
  await page.reload()
  await expect(results.getByRole('alert').first()).toBeVisible()
  await expect(results.getByRole('button', { name: "Prompt'u Kopyala" })).toHaveCount(0)
  expect(localSpa.operationCount()).toBe(2)
  expect(posts).toHaveLength(3)
})

test('closing and reopening results cannot reuse revoked approval while cache collection is paused', async ({ page, request, localSpa }) => {
  await page.clock.install()
  let resultReads = 0
  let progressReads = 0
  const diagnostics: string[] = []
  page.on('console', message => diagnostics.push(message.text()))
  page.on('pageerror', error => diagnostics.push(error.message))
  page.on('request', request => {
    if (/\/runs\/[^/]+\/result\?/.test(request.url())) resultReads++
    if (/\/runs\/[^/]+\?/.test(request.url())) progressReads++
  })
  const { workflow, monitor, results, posts } = await begin(page)
  await expect(results.getByRole('button', { name: "Prompt'u Kopyala" })).toBeEnabled()
  const original = await (await request.get(`/api/v1/runs/${workflow.id}/result`)).json()
  const second = await request.post(`/api/v1/prompt-requests/${workflow.prompt_request_id}/runs`, { data: { client_idempotency_key: `test-${randomUUID()}` } })
  expect(second.status()).toBe(201)
  const otherId = (await second.json()).workflow.id as string
  await localSpa.invalidateSelection(workflow.id)
  await results.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' }).click()
  await expect(results.getByText(/Geçmiş içeriği bir bütünlük sorunu bildirdi/)).toBeVisible()
  // Hold zero-delay cache collection across the two clicks. Approval must be
  // revoked independently of whether an old successful Query is still retained.
  await page.clock.pauseAt(new Date(Date.now() + 1000))
  await monitor.getByRole('button', { name: 'Sonuç ve Geçmişi Kapat' }).click()
  await monitor.getByRole('button', { name: 'Sonuç ve Geçmişi Görüntüle' }).click()
  await expect(results.getByRole('button', { name: "Prompt'u Kopyala" })).toHaveCount(0)
  await expect(results.getByLabel('Seçilen prompt metni')).toHaveCount(0)
  expect(resultReads).toBe(1)
  await page.clock.resume()
  await monitor.getByRole('button', { name: 'Kayıtlı Workflow’ları Görüntüle' }).click()
  await monitor.getByLabel('Bu promptun workflow’ları').selectOption(otherId)
  await expect(results.getByText(/Nihai sonuç henüz hazır değil/)).toBeVisible()
  await monitor.getByLabel('Bu promptun workflow’ları').selectOption(workflow.id)
  await expect(results.getByRole('alert').first()).toBeVisible()
  await expect(results.getByLabel('Seçilen prompt metni')).toHaveCount(0)
  // Restore only the disposable fixture to model an external, correct repair.
  // UI trust still requires new GETs; restoration itself cannot clear the guard.
  await localSpa.restoreSelection(workflow.id, original.candidate.version.id)
  await monitor.getByRole('button', { name: 'Şimdi Yenile', exact: true }).click()
  await results.getByRole('button', { name: 'Geçmiş Özetini Yenile' }).click()
  await expect(results.getByRole('button', { name: 'Sonucu Yenile' })).toBeEnabled()
  await expect(results.getByLabel('Seçilen prompt metni')).toHaveCount(0)
  const verified = page.waitForResponse(response => response.url().includes(`/runs/${workflow.id}/result?`))
  await results.getByRole('button', { name: 'Sonucu Yenile' }).click()
  expect((await verified).status()).toBe(200)
  await expect(results.getByRole('button', { name: "Prompt'u Kopyala" })).toBeEnabled()
  await expect(results.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' })).toHaveAttribute('aria-expanded', 'false')
  await localSpa.invalidateSelection(workflow.id)
  await results.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' }).click()
  await expect(results.getByLabel('Seçilen prompt metni')).toHaveCount(0)
  const beforeReload = progressReads
  await page.reload()
  await expect(results.getByRole('alert').first()).toBeVisible()
  expect(progressReads).toBeGreaterThan(beforeReload)
  await expect(results.getByRole('button', { name: "Prompt'u Kopyala" })).toHaveCount(0)
  expect(diagnostics.join('\n')).not.toContain('Keep Merhaba')
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).not.toContain('Keep Merhaba')
  expect(posts).toHaveLength(3)
  expect(localSpa.operationCount()).toBe(2)
})
