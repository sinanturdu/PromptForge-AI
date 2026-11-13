import { randomUUID } from 'node:crypto'
import { createServer } from 'node:http'
import { once } from 'node:events'
import type { Page } from '@playwright/test'
import { test, expect } from './local-spa-fixture'

const RAW = '  Explain Python 3.12. Keep Merhaba.\n```python\ndef f():\n    return 1\n```\n' + 'LongUnbrokenInput'.repeat(200) + '\n'
const CONTEXT = '  Kayıtlı örnek bağlam\n    ikinci satır\n'
const FOLLOWUP_ANSWER = 'Özel örnek yanıt 🌊\n    <img src=x onerror=alert(1)>'
const PREPARE = 'Yanıtlarımla Yeni İstek Taslağı Hazırla'
async function begin(page: Page, target: 'generic' | 'openai' = 'generic', withPreferences = false) {
  const posts: string[] = []
  page.on('request', request => { if (request.method() === 'POST') posts.push(new URL(request.url()).pathname) })
  await page.goto('/')
  await page.getByLabel('Ham Prompt İsteği', { exact: false }).fill(RAW)
  if (withPreferences) {
    await page.getByLabel(/Proje Bağlamı/).fill(CONTEXT)
    await page.getByLabel(/Çıktı Dili/).selectOption('en')
    await page.getByLabel(/Görev Profili/).fill('code_review')
    for (const [group, name] of [
      [/Prompt Detay Seviyesi/, 'Yüksek'], [/Yanıt Detay Tercihi/, 'Minimal'], [/Akıl Yürütme Tercihi/, 'Düşük'],
    ] as const) {
      const option = page.getByRole('group', { name: group }).getByRole('radio', { name, exact: true })
      await option.focus()
      await option.press('Space')
      await expect(option).toBeChecked()
    }
  }
  if (target === 'openai') {
    const radio = page.getByRole('radio', { name: 'ChatGPT / GPT', exact: true })
    await radio.focus()
    await radio.press('Space')
    await expect(radio).toBeChecked()
  }
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
    const openai = name === 'mobile'
    const { results, posts } = await begin(page, openai ? 'openai' : 'generic')
    const renderer = openai ? 'OpenAIPromptRenderer' : 'GenericPromptRenderer'
    const selected = results.getByRole('region', { name: 'Seçilen prompt', exact: true })
    const identity = selected.getByLabel('Renderer kimliği')
    await expect(identity).toContainText(openai ? 'ChatGPT / GPT (openai)' : 'Universal (generic)')
    await expect(identity.getByText(renderer, { exact: true })).toBeVisible()
    await expect(identity.getByText('1.0.0', { exact: true })).toBeVisible()
    const history = results.getByRole('region', { name: 'Değerlendirme metadata geçmişi' })
    await expect(history.getByLabel('Renderer kimliği').getByText(renderer, { exact: true })).toBeVisible()
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
    const comparison = results.getByRole('region', { name: 'Prompt Karşılaştırması', exact: true })
    await expect(comparison.getByLabel('Sol prompt', { exact: true })).toHaveValue('original')
    await expect(comparison.getByLabel('Renderer kimliği')).toHaveCount(1)
    await expect(comparison.getByLabel('Renderer kimliği').getByText(renderer, { exact: true })).toBeVisible()
    expect(await results.getByLabel('Sol prompt metni').textContent()).toBe(RAW)
    expect(await results.getByLabel('Sağ prompt metni').textContent()).toBe(RAW)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.reload()
    await expect(results.getByLabel('Seçilen prompt metni')).toBeVisible()
    await expect(identity.getByText(renderer, { exact: true })).toBeVisible()
    expect(await results.getByLabel('Seçilen prompt metni').textContent()).toBe(RAW)
    await page.screenshot({ path: testInfo.outputPath(`${name}-workflow-result.png`), fullPage: true })
    expect(posts.filter(path => path.endsWith('/execution'))).toHaveLength(1)
    expect(posts).toHaveLength(3)
    expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).not.toContain('Keep Merhaba')
  })
}

test('mobile renderer metadata wraps long stored values without changing the prompt', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  const { workflow, results, posts } = await begin(page, 'openai')
  await expect(results.getByLabel('Seçilen prompt metni')).toBeVisible()
  // Only extend metadata in the read responses to exercise the API string widths.
  const renderer = 'LongRendererIdentity'.repeat(4).slice(0, 64)
  const version = '1.0.0+'.padEnd(32, 'v')
  await page.route(url => [`/api/v1/runs/${workflow.id}/result`, `/api/v1/runs/${workflow.id}/history`].includes(url.pathname), async route => {
    const response = await route.fetch()
    if (!response.ok()) { await route.fulfill({ response }); return }
    const body = await response.json()
    const versions = body.candidate ? [body.candidate.version] : body.versions
    for (const stored of versions) {
      stored.renderer_name = renderer
      stored.renderer_version = version
    }
    await route.fulfill({ response, json: body })
  })
  await page.reload()
  const selected = results.getByRole('region', { name: 'Seçilen prompt', exact: true })
  await expect(selected.getByLabel('Renderer kimliği').getByText(renderer, { exact: true })).toBeVisible()
  await expect(selected.getByLabel('Renderer kimliği').getByText(version, { exact: true })).toBeVisible()
  expect(await selected.getByLabel('Seçilen prompt metni').textContent()).toBe(RAW)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(posts).toHaveLength(3)
})

test.describe('completed without qualification', () => {
  test.use({ spaScenario: 'not-qualified' })
  test('keeps backend selection independent of the last round and compares two stored versions', async ({ page, request }) => {
    const { workflow, results, posts } = await begin(page, 'openai')
    await expect(results.getByText('Kalite sonucu: Kalite koşulları sağlanmadı.', { exact: true })).toBeVisible()
    await expect(results.getByText('Yürütme tamamlandı; sonuç bütünlüğü doğrulandı.', { exact: true })).toBeVisible()
    const result = await (await request.get(`/api/v1/runs/${workflow.id}/result`)).json()
    expect(result.workflow.selected.evaluation_round).toBe(1)
    expect(result.candidate.version.renderer_name).toBe('OpenAIPromptRenderer')
    expect(await results.getByLabel('Seçilen prompt metni').textContent()).toBe(result.candidate.rendered_prompt)
    await expect(results.getByRole('button', { name: "Prompt'u Kopyala" })).toBeEnabled()
    await results.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' }).click()
    const content = await (await request.get(`/api/v1/runs/${workflow.id}/history/content`)).json()
    // Identical actionable feedback stops further revision attempts early. Only
    // the two versions actually committed by that workflow can be compared.
    expect(content.versions).toHaveLength(2)
    expect(result.workflow.completed_rounds).toBe(2)
    await expect(results.getByLabel('Sağ prompt', { exact: true })).toHaveValue(result.candidate.version.id)
    const history = results.getByRole('region', { name: 'Değerlendirme metadata geçmişi' })
    for (const round of [1, 2]) {
      const row = history.getByRole('listitem').filter({ hasText: `Tur ${round} · Kayıtlı değerlendirme` })
      await expect(row.getByLabel('Renderer kimliği').getByText('OpenAIPromptRenderer', { exact: true })).toBeVisible()
    }
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
  const { workflow, monitor, results, posts } = await begin(page, 'openai')
  await expect(results.getByLabel('Seçilen prompt metni')).toBeVisible()
  await expect(results.getByRole('region', { name: 'Seçilen prompt', exact: true }).getByLabel('Renderer kimliği')).toContainText('OpenAIPromptRenderer')
  const second = await request.post(`/api/v1/prompt-requests/${workflow.prompt_request_id}/runs`, { data: { client_idempotency_key: `test-${randomUUID()}` } })
  expect(second.status()).toBe(201)
  const otherId = (await second.json()).workflow.id as string
  await monitor.getByRole('button', { name: 'Kayıtlı Workflow’ları Görüntüle' }).click()
  await monitor.getByLabel('Bu promptun workflow’ları').selectOption(otherId)
  await expect(results.getByText(/Nihai sonuç henüz hazır değil/)).toBeVisible()
  await expect(results.getByLabel('Seçilen prompt metni')).toHaveCount(0)
  await expect(results.getByRole('button', { name: "Prompt'u Kopyala" })).toHaveCount(0)
  await expect(results.getByLabel('Renderer kimliği')).toHaveCount(0)
  await page.reload()
  await expect(results.getByText(/Nihai sonuç henüz hazır değil/)).toBeVisible()
  await expect(results.getByLabel('Renderer kimliği')).toHaveCount(0)
  expect(new URL(page.url()).searchParams.get('workflow')).toBe(otherId)
  expect(posts).toHaveLength(3)
  expect(localSpa.operationCount()).toBe(2)
})

for (const scenario of ['failed', 'clarify'] as const) {
  test.describe(`${scenario} result`, () => {
    test.use({ spaScenario: scenario })
    test('keeps history available without a successful or copyable final prompt', async ({ page, request }) => {
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

test.describe('clarification follow-up', () => {
  test.use({ spaScenario: 'clarify' })

  async function reviewDraft(results: ReturnType<Page['getByRole']>) {
    for (const input of await results.getByRole('textbox', { name: /^Yanıt [1-3]$/ }).all()) {
      await input.fill(FOLLOWUP_ANSWER)
    }
    await results.getByRole('button', { name: PREPARE }).click()
    return results.getByRole('button', { name: 'Prompt İsteğini Kaydet' })
  }

  test('withholds stale follow-up after newer detailed history is closed and reconciles on demand', async ({ page }) => {
    const { workflow, results, posts } = await begin(page)
    await expect(results.getByRole('button', { name: PREPARE })).toBeEnabled()
    const save = await reviewDraft(results)
    await expect(save).toBeEnabled()
    let current = false
    await page.route(url => url.pathname.startsWith(`/api/v1/runs/${workflow.id}`), async route => {
      const response = await route.fetch()
      if (!response.ok()) return route.fulfill({ response })
      const body = await response.json()
      const detailed = new URL(route.request().url()).pathname.endsWith('/history/content')
      if (detailed || current) (body.metadata?.workflow ?? body.workflow ?? body).state_revision += 1
      return route.fulfill({ response, json: body })
    })
    await results.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' }).click()
    await expect(results.getByText('İçeriği sağlanan tamamlanmış değerlendirme yok.')).toBeVisible()
    await expect(save).toHaveCount(0)
    await results.getByRole('button', { name: 'Geçmiş İçeriğini Kapat' }).click()
    await page.getByRole('button', { name: 'Sonuç ve Geçmişi Kapat' }).click()
    await page.getByRole('button', { name: 'Sonuç ve Geçmişi Görüntüle' }).click()
    await expect(results.getByText('Ek açıklama gerekiyor; nihai prompt sunulmuyor.', { exact: true })).toBeVisible()
    await expect(results.getByRole('button', { name: PREPARE })).toHaveCount(0)
    expect(posts).toHaveLength(3)
    current = true
    await results.getByRole('button', { name: 'Açıklama Kayıtlarını Yenile' }).click()
    await expect(results.getByRole('button', { name: PREPARE })).toBeEnabled()
    expect(posts).toHaveLength(3)
  })

  test('rejects a same-turn submit from the previous workflow', async ({ page, request }) => {
    const { workflow, monitor, results, posts } = await begin(page)
    await expect(results.getByRole('button', { name: PREPARE })).toBeEnabled()
    const other = await request.post(`/api/v1/prompt-requests/${workflow.prompt_request_id}/runs`, {
      data: { client_idempotency_key: `test-${randomUUID()}` },
    })
    expect(other.status()).toBe(201)
    const otherId: string = (await other.json()).workflow.id
    await expect(await reviewDraft(results)).toBeEnabled()
    await monitor.getByRole('button', { name: 'Kayıtlı Workflow’ları Görüntüle' }).click()
    const select = monitor.getByLabel('Bu promptun workflow’ları')
    await expect(select.getByRole('option', { name: new RegExp(otherId.slice(0, 8)) })).toHaveCount(1)
    const changed = await select.evaluate((node: HTMLSelectElement, id) => {
      const form = document.querySelector<HTMLFormElement>('form[aria-label="Prompt gönderim formu"]')!
      node.value = id
      node.dispatchEvent(new Event('change', { bubbles: true }))
      form.requestSubmit()
      return new URL(location.href).searchParams.get('workflow') === id
    }, otherId)
    expect(changed).toBe(true)
    await expect(results.getByText(/Nihai sonuç henüz hazır değil/)).toBeVisible()
    expect(posts).toHaveLength(3)
    await page.goBack()
    await expect(results.getByRole('textbox', { name: 'Yanıt 1', exact: true })).toHaveValue('')
    await page.goForward()
    await expect(results.getByText(/Nihai sonuç henüz hazır değil/)).toBeVisible()
    expect(posts).toHaveLength(3)
  })

  test('preserves mixed source and question line endings in the submitted body after browser edits', async ({ page }) => {
    const context = '  İlk\r\nTekrar\nTekrar\r\nSon 🌊\n'
    const question = 'Hangi çıktı?\r\n  Hangi biçim?'
    await page.route(url => url.pathname === '/api/v1/prompt-requests', route => {
      if (route.request().method() !== 'POST') return route.continue()
      return route.continue({ postData: JSON.stringify({ ...route.request().postDataJSON(), user_context: context }) })
    }, { times: 1 })
    await page.route(url => url.pathname.startsWith('/api/v1/runs/') && url.pathname.endsWith('/result'), async route => {
      const response = await route.fetch()
      if (!response.ok()) return route.fulfill({ response })
      const body = await response.json()
      body.clarification_questions[0] = question
      return route.fulfill({ response, json: body })
    })
    const { results } = await begin(page)
    const first = results.getByRole('textbox', { name: 'Yanıt 1', exact: true })
    await expect(first).toBeVisible()
    await results.getByRole('button', { name: PREPARE }).waitFor()
    const save = await reviewDraft(results)
    const input = results.getByLabel(/Proje Bağlamı/)
    await input.press('Control+Home')
    await page.keyboard.insertText('Başlangıç\n')
    const text = await input.inputValue()
    const second = text.indexOf('Tekrar\nTekrar') + 'Tekrar\n'.length
    await input.evaluate((node: HTMLTextAreaElement, start) => node.setSelectionRange(start, start + 'Tekrar'.length), second)
    await page.keyboard.insertText('Değişti')
    await input.press('Control+End')
    await page.keyboard.insertText('\n  Ek 🌊')
    const response = page.waitForResponse(value => value.request().method() === 'POST' && value.url().endsWith('/prompt-requests'))
    await save.click()
    const created = await response
    expect(created.status()).toBe(201)
    const payload = created.request().postDataJSON()
    expect(payload.raw_prompt).toBe(RAW)
    expect(payload.user_context).toContain('Başlangıç\n  İlk\r\nTekrar\nDeğişti\r\nSon 🌊\n\n\nEk Açıklama Yanıtları')
    expect(payload.user_context).toContain(`Soru 1: ${question}\nYanıt 1: ${FOLLOWUP_ANSWER}`)
    expect(payload.user_context.endsWith('\n  Ek 🌊')).toBe(true)
  })

  test('rejects redirect responses before a second origin receives anything', async ({ page }) => {
    let forwarded = 0
    const receiver = createServer((request, response) => {
      forwarded++
      request.resume()
      response.writeHead(request.method === 'OPTIONS' ? 204 : 201, {
        'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'Content-Type',
        'Access-Control-Allow-Methods': 'POST, GET, OPTIONS', 'Content-Type': 'application/json',
      })
      response.end(request.method === 'OPTIONS' ? undefined : JSON.stringify({ id: randomUUID() }))
    }).listen(0, '127.0.0.1')
    try {
      await once(receiver, 'listening')
      const address = receiver.address()
      if (!address || typeof address === 'string') throw new Error('Missing test receiver port')
      const { results, posts } = await begin(page)
      await expect(results.getByRole('button', { name: PREPARE })).toBeEnabled()
      const save = await reviewDraft(results)
      const messages: string[] = []
      page.on('console', message => messages.push(message.text()))
      let status = 307
      const attempted: number[] = []
      await page.route(url => url.pathname === '/api/v1/prompt-requests', route => {
        if (route.request().method() !== 'POST') return route.continue()
        attempted.push(status)
        return route.fulfill({ status, headers: { Location: `http://127.0.0.1:${address.port}/receiver` } })
      })
      for (const expected of [307, 308, 301, 302, 303]) {
        status = expected
        await save.click()
        await expect.poll(() => attempted.at(-1)).toBe(expected)
        await expect(results.getByRole('alert').filter({ hasText: 'Prompt Geçmişini kontrol edin' })).toBeVisible()
        await expect(save).toBeEnabled()
        expect(forwarded).toBe(0)
      }
      expect(attempted).toEqual([307, 308, 301, 302, 303])
      expect(posts).toHaveLength(8)
      expect(messages.join('\n')).not.toContain('Özel örnek yanıt')
    } finally {
      await new Promise<void>((resolve, reject) => receiver.close(error => error ? reject(error) : resolve()))
    }
  })

  test('saves a separate request after review and leaves the historical workflow unchanged', async ({ page, request, localSpa }) => {
    const { workflow, results, posts } = await begin(page, 'openai', true)
    const sourcePath = `/api/v1/prompt-requests/${workflow.prompt_request_id}`
    const runPath = `/api/v1/runs/${workflow.id}`
    async function read(path: string) {
      const response = await request.get(path)
      expect(response.ok()).toBe(true)
      return response.json()
    }
    await expect(results.getByRole('heading', { name: 'Ek Bilgi Gerekiyor' })).toBeVisible()
    await expect(results.getByRole('button', { name: "Prompt'u Kopyala" })).toHaveCount(0)
    const historyPaths = [sourcePath, `${sourcePath}/runs`, runPath, `${runPath}/result`, `${runPath}/history`, `${runPath}/history/content`]
    const before = await Promise.all(historyPaths.map(read))
    expect(before[3].workflow.status).toBe('CLARIFICATION_REQUIRED')
    await results.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' }).click()
    await expect(results.getByText('İçeriği sağlanan tamamlanmış değerlendirme yok.')).toBeVisible()
    const questions = before[3].clarification_questions as string[]
    const answers = questions.map((_, index) => `${FOLLOWUP_ANSWER}\n    ${index + 1}`)
    for (const [index, question] of questions.entries()) {
      const input = results.getByRole('textbox', { name: `Yanıt ${index + 1}`, exact: true })
      await expect(input).toHaveAccessibleDescription(question)
      await input.fill(answers[index]!)
    }
    await results.getByRole('button', { name: PREPARE }).click()
    const review = results.getByRole('region', { name: 'Yeni Prompt İsteği Taslağı' })
    await expect(review.getByRole('heading', { name: 'Yeni Prompt İsteği Taslağı' })).toBeFocused()
    await expect(review.getByLabel(/Ham Prompt İsteği/)).toHaveValue(RAW)
    const context = [CONTEXT, 'Ek Açıklama Yanıtları', ...questions.map((question, index) =>
      `Soru ${index + 1}: ${question}\nYanıt ${index + 1}: ${answers[index]}`)].join('\n\n')
    await expect(review.getByLabel(/Proje Bağlamı/)).toHaveValue(context)
    await expect(review.getByLabel(/Çıktı Dili/)).toHaveValue('en')
    await expect(review.getByLabel(/Görev Profili/)).toHaveValue('code_review')
    await expect(review.getByRole('radio', { name: 'ChatGPT / GPT', exact: true })).toBeChecked()
    await expect(review.getByRole('group', { name: /Prompt Detay Seviyesi/ }).getByRole('radio', { name: 'Yüksek', exact: true })).toBeChecked()
    await expect(review.getByRole('group', { name: /Yanıt Detay Tercihi/ }).getByRole('radio', { name: 'Minimal', exact: true })).toBeChecked()
    await expect(review.getByRole('group', { name: /Akıl Yürütme Tercihi/ }).getByRole('radio', { name: 'Düşük', exact: true })).toBeChecked()
    expect(posts).toHaveLength(3)
    expect(localSpa.operationCount()).toBe(1)

    const creation = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/prompt-requests'))
    await review.getByRole('button', { name: 'Prompt İsteğini Kaydet' }).click()
    const response = await creation
    expect(response.status()).toBe(201)
    const created = await response.json()
    expect(created.id).not.toBe(workflow.prompt_request_id)
    expect(response.request().postDataJSON()).toEqual({
      raw_prompt: RAW, user_context: context, output_language: 'en', target_mode: 'openai',
      task_profile: 'code_review', prompt_detail_level: 'high', response_detail_preference: 'minimal', reasoning_preference: 'low',
    })
    await expect(page).toHaveURL(new RegExp(`/prompt-requests/${created.id}$`))
    const newPath = `/api/v1/prompt-requests/${created.id}`
    expect(await read(newPath)).toEqual(created)
    expect(await Promise.all(historyPaths.map(read))).toEqual(before)
    expect((await read(`${newPath}/runs`)).items).toEqual([])
    await page.getByRole('button', { name: 'Optimizasyonu Yapılandır' }).click()
    await expect(page.getByRole('button', { name: 'Workflow Oluştur', exact: true })).toBeEnabled()
    expect(posts).toHaveLength(4)
    expect(posts.filter(path => path.endsWith('/runs'))).toHaveLength(1)
    expect(posts.filter(path => path.endsWith('/execution'))).toHaveLength(1)
    expect(localSpa.operationCount()).toBe(1)
  })

  test('supports mobile keyboard review and drops private unsaved content on reload', async ({ page, localSpa }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    const consoleLines: string[] = []
    page.on('console', message => consoleLines.push(message.text()))
    const { results, posts } = await begin(page)
    const answers = results.getByRole('textbox', { name: /^Yanıt [1-3]$/ })
    const first = answers.first()
    await expect(first).toBeVisible()
    for (const answer of await answers.all()) {
      await answer.focus()
      await answer.press('End')
      await page.keyboard.insertText(FOLLOWUP_ANSWER)
    }
    await page.keyboard.press('Tab')
    await expect(results.getByRole('button', { name: PREPARE })).toBeFocused()
    await page.keyboard.press('Enter')
    const heading = results.getByRole('heading', { name: 'Yeni Prompt İsteği Taslağı' })
    await expect(heading).toBeFocused()
    await expect(results.getByRole('button', { name: 'Prompt İsteğini Kaydet' })).toBeEnabled()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    const browserState = () => page.evaluate(() => JSON.stringify({
      url: location.href, history: history.state, local: { ...localStorage }, session: { ...sessionStorage },
    }))
    expect(await browserState()).not.toMatch(/Özel örnek yanıt|Keep Merhaba|Ek Açıklama Yanıtları/)
    expect(consoleLines.join('\n')).not.toContain('Özel örnek yanıt')
    await page.reload()
    await expect(first).toHaveValue('')
    await expect(heading).toHaveCount(0)
    expect(await browserState()).not.toContain('Özel örnek yanıt')
    expect(posts).toHaveLength(3)
    expect(localSpa.operationCount()).toBe(1)
  })
})

test('integrity inconsistency revokes copying while keeping real evaluation metadata visible', async ({ page, request, localSpa }) => {
  const { workflow, monitor, results, posts } = await begin(page)
  await expect(results.getByRole('button', { name: "Prompt'u Kopyala" })).toBeEnabled()
  await localSpa.invalidateSelection(workflow.id)
  await monitor.getByRole('button', { name: 'Şimdi Yenile', exact: true }).click()
  await expect(results.getByLabel('Seçilen prompt metni')).toHaveCount(0)
  await expect(results.getByRole('button', { name: "Prompt'u Kopyala" })).toHaveCount(0)
  await results.getByRole('button', { name: 'Geçmiş Özetini Yenile' }).click()
  await expect(results.getByText('Tur 1 · Kayıtlı değerlendirme (doğrulanmadı)', { exact: true })).toBeVisible()
  await expect(results.getByLabel('Renderer kimliği')).toHaveCount(0)
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
  await expect(results.getByText(/Sonuç veya geçmiş okuması bir bütünlük sorunu bildirdi/)).toBeVisible()
  await expect(results.getByText('Tur 1 · Kayıtlı değerlendirme (doğrulanmadı)', { exact: true })).toBeVisible()
  await expect(results.getByLabel('Renderer kimliği')).toHaveCount(0)
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

test('conflicting detailed renderer revokes copy across reopening and requires a fresh result', async ({ page, localSpa }) => {
  const { workflow, results, monitor, posts } = await begin(page, 'openai')
  const copy = results.getByRole('button', { name: "Prompt'u Kopyala" })
  const identity = results.getByLabel('Renderer kimliği')
  await expect(copy).toBeEnabled()
  const contentUrl = (url: URL) => url.pathname === `/api/v1/runs/${workflow.id}/history/content`
  await page.route(contentUrl, async route => {
    const response = await route.fetch()
    const content = await response.json()
    content.metadata.versions[0].renderer_name = 'GenericPromptRenderer'
    content.versions[0].version.renderer_name = 'GenericPromptRenderer'
    await route.fulfill({ response, json: content })
  })
  await results.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' }).click()
  const revoked = results.getByText(/Sonuç veya geçmiş okuması bir bütünlük sorunu bildirdi/)
  await expect(revoked).toBeVisible()
  await expect(copy).toHaveCount(0)
  await expect(identity).toHaveCount(0)
  await expect(results.getByRole('region', { name: 'Prompt Karşılaştırması' })).toHaveCount(0)
  await page.unroute(contentUrl)
  await monitor.getByRole('button', { name: 'Sonuç ve Geçmişi Kapat' }).click()
  await monitor.getByRole('button', { name: 'Sonuç ve Geçmişi Görüntüle' }).click()
  await expect(revoked).toBeVisible()
  await expect(copy).toHaveCount(0)
  const refreshed = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/runs/${workflow.id}/result`)
  await results.getByRole('button', { name: 'Sonucu Yenile' }).click()
  expect((await refreshed).status()).toBe(200)
  await expect(copy).toBeEnabled()
  expect(await results.getByLabel('Seçilen prompt metni').textContent()).toBe(RAW)
  await expect(results.getByRole('region', { name: 'Seçilen prompt', exact: true }).getByLabel('Renderer kimliği'))
    .toContainText('OpenAIPromptRenderer')
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
  await expect(results.getByText(/Sonuç veya geçmiş okuması bir bütünlük sorunu bildirdi/)).toBeVisible()
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
