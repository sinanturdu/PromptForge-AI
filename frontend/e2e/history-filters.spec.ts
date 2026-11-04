import { test, expect } from './fixtures'
import {
  createRecord,
  expectHistoryQuery,
  expectHistoryRecords,
  historyCard,
  promptPayload,
  uniqueToken,
} from './helpers'

test('keyword search matches prompt and context, excludes unrelated records, and handles no results', async ({ page, request }) => {
  const keyword = uniqueToken()
  const rawMatch = await createRecord(request, promptPayload({ raw_prompt: `${keyword} için bütçe planı hazırla.` }))
  const contextMatch = await createRecord(request, promptPayload({ raw_prompt: 'Aylık giderleri sınıflandır.', user_context: `Proje kodu: ${keyword}` }))
  const unrelated = await createRecord(request, promptPayload({ raw_prompt: 'Bir seyahat rotası öner.' }))
  await page.goto('/history')
  await expectHistoryRecords(page, [rawMatch, contextMatch, unrelated])
  const search = page.getByRole('searchbox', { name: 'Anahtar Kelime ile Ara' })
  await search.fill(keyword)
  await expectHistoryQuery(page, { q: keyword })
  await expectHistoryRecords(page, [rawMatch, contextMatch])
  await expect(historyCard(page, unrelated.raw_prompt)).toHaveCount(0)
  const absent = uniqueToken()
  await search.fill(absent)
  await expectHistoryQuery(page, { q: absent })
  await expect(page.getByRole('heading', { name: 'Aramanızla eşleşen prompt bulunamadı.' })).toBeVisible()
  await expect(page.getByRole('article')).toHaveCount(0)
  await page.getByRole('button', { name: 'Filtreleri Temizle', exact: true }).click()
  await expectHistoryQuery(page, {})
  await expectHistoryRecords(page, [rawMatch, contextMatch, unrelated])
})

for (const target of [
  { mode: 'openai', label: 'ChatGPT / GPT' },
  { mode: 'anthropic', label: 'Claude' },
  { mode: 'google', label: 'Gemini' },
] as const) {
  test(`target filter returns only ${target.label} records`, async ({ page, request }) => {
    const records = []
    for (const mode of ['generic', 'openai', 'anthropic', 'google'] as const) {
      records.push(await createRecord(request, promptPayload({ target_mode: mode })))
    }
    await page.goto('/history')
    await expectHistoryRecords(page, records)
    const filter = page.getByRole('group', { name: 'Hedef Yapay Zekâ Ailesi' }).getByRole('button', { name: target.label, exact: true })
    await filter.click()
    await expect(filter).toHaveAttribute('aria-pressed', 'true')
    await expectHistoryQuery(page, { target_mode: target.mode })
    await expectHistoryRecords(page, records.filter((record) => record.target_mode === target.mode))
    for (const record of records.filter((record) => record.target_mode !== target.mode)) {
      await expect(historyCard(page, record.raw_prompt)).toHaveCount(0)
    }
    await page.getByRole('group', { name: 'Hedef Yapay Zekâ Ailesi' }).getByRole('button', { name: 'Tümü', exact: true }).click()
    await expectHistoryQuery(page, {})
    await expectHistoryRecords(page, records)
  })
}

test('combined search and target state survive reload, direct URLs, Back/Forward, and clearing', async ({ page, request }) => {
  const keyword = uniqueToken()
  const match = await createRecord(request, promptPayload({ raw_prompt: `${keyword} finans raporu hazırla.`, target_mode: 'openai' }))
  const wrongTarget = await createRecord(request, promptPayload({ raw_prompt: `${keyword} finans tablosu oluştur.`, target_mode: 'anthropic' }))
  const wrongText = await createRecord(request, promptPayload({ raw_prompt: 'Yazılım mimarisini incele.', target_mode: 'openai' }))
  const records = [match, wrongTarget, wrongText]
  await page.goto('/history')
  await expectHistoryRecords(page, records)
  const search = page.getByRole('searchbox', { name: 'Anahtar Kelime ile Ara' })
  const filters = page.getByRole('group', { name: 'Hedef Yapay Zekâ Ailesi' })
  // Target changes push history entries; debounced search replaces the current one.
  await filters.getByRole('button', { name: 'ChatGPT / GPT', exact: true }).click()
  await expectHistoryRecords(page, [match, wrongText])
  await search.fill(keyword)
  await expectHistoryQuery(page, { q: keyword, target_mode: 'openai' })
  await expectHistoryRecords(page, [match])
  await filters.getByRole('button', { name: 'Claude', exact: true }).click()
  await expectHistoryQuery(page, { q: keyword, target_mode: 'anthropic' })
  await expectHistoryRecords(page, [wrongTarget])
  await page.goBack()
  await expectHistoryQuery(page, { q: keyword, target_mode: 'openai' })
  await expect(search).toHaveValue(keyword)
  await expect(filters.getByRole('button', { name: 'ChatGPT / GPT', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expectHistoryRecords(page, [match])
  await page.goForward()
  await expectHistoryQuery(page, { q: keyword, target_mode: 'anthropic' })
  await expect(filters.getByRole('button', { name: 'Claude', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expectHistoryRecords(page, [wrongTarget])
  await page.reload()
  await expect(search).toHaveValue(keyword)
  await expect(filters.getByRole('button', { name: 'Claude', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expectHistoryRecords(page, [wrongTarget])
  // Reverse parameter order deliberately: URL state must not depend on ordering.
  await page.goto(`/history?target_mode=openai&q=${keyword}`)
  await expectHistoryQuery(page, { q: keyword, target_mode: 'openai' })
  await expect(search).toHaveValue(keyword)
  await expect(filters.getByRole('button', { name: 'ChatGPT / GPT', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expectHistoryRecords(page, [match])
  await historyCard(page, match.raw_prompt).getByRole('link', { name: 'Detayları Görüntüle' }).click()
  await expect(page).toHaveURL(`/prompt-requests/${match.id}`)
  await page.getByRole('link', { name: '← Prompt Geçmişine Dön' }).click()
  await expectHistoryQuery(page, { q: keyword, target_mode: 'openai' })
  await expectHistoryRecords(page, [match])
  await page.getByRole('button', { name: 'Hedef model filtresini kaldır' }).click()
  await expectHistoryQuery(page, { q: keyword })
  await expectHistoryRecords(page, [match, wrongTarget])
  await page.getByRole('button', { name: 'Arama filtresini kaldır' }).click()
  await expectHistoryQuery(page, {})
  await expect(search).toHaveValue('')
  await expectHistoryRecords(page, records)
})

test('filtered pagination preserves search and target state through detail and reload', async ({ page, request }) => {
  const keyword = uniqueToken()
  for (let index = 1; index <= 11; index++) {
    await createRecord(request, promptPayload({ raw_prompt: `${keyword} plan ${index}`, target_mode: 'anthropic' }))
  }
  await createRecord(request, promptPayload({ raw_prompt: `${keyword} başka hedef`, target_mode: 'google' }))
  await page.goto(`/history?q=${keyword}&target_mode=anthropic`)
  await expect(page.getByRole('article')).toHaveCount(10)
  const pagination = page.getByRole('navigation', { name: 'Prompt geçmişi sayfalama' })
  await expect(pagination).toContainText('Sayfa 1 / 2')
  await pagination.getByRole('button', { name: 'Sonraki', exact: true }).click()
  await expectHistoryQuery(page, { q: keyword, target_mode: 'anthropic', page: '2' })
  await expect(page.getByRole('article')).toHaveCount(1)
  const detailLink = page.getByRole('article').getByRole('link', { name: 'Detayları Görüntüle' })
  const detailPath = await detailLink.getAttribute('href')
  await detailLink.click()
  await expect(page).toHaveURL(detailPath!)
  await page.getByRole('link', { name: '← Prompt Geçmişine Dön' }).click()
  await expectHistoryQuery(page, { q: keyword, target_mode: 'anthropic', page: '2' })
  await page.reload()
  await expectHistoryQuery(page, { q: keyword, target_mode: 'anthropic', page: '2' })
  await expect(page.getByRole('searchbox', { name: 'Anahtar Kelime ile Ara' })).toHaveValue(keyword)
  await expect(page.getByRole('group', { name: 'Hedef Yapay Zekâ Ailesi' }).getByRole('button', { name: 'Claude', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(pagination).toContainText('Sayfa 2 / 2')
  await expect(pagination.getByRole('button', { name: 'Sonraki', exact: true })).toBeDisabled()
})
