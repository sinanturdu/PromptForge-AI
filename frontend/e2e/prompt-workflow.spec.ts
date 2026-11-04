import { test, expect } from './fixtures'
import {
  createInBrowser,
  expectSavedDetails,
  expectNoHorizontalOverflow,
  historyCard,
  promptPayload,
} from './helpers'

test('creates a prompt with context and every preference through the browser', async ({ page }) => {
  const record = await createInBrowser(page)
  await expectSavedDetails(page, record)
  await expect(page.getByRole('status')).toContainText('Prompt isteğiniz başarıyla kaydedildi.')
})

test('reload and a fresh browser context fetch the same persisted prompt', async ({ page, browser }) => {
  const record = await createInBrowser(page)
  const savedUrl = page.url()
  const fetched = page.waitForResponse((response) =>
    response.request().method() === 'GET' && new URL(response.url()).pathname === `/api/v1/prompt-requests/${record.id}`,
  )
  await page.reload()
  expect((await fetched).status()).toBe(200)
  await expect(page).toHaveURL(savedUrl)
  await expectSavedDetails(page, record)
  // A new context has neither the app's Query cache nor router navigation state.
  const fresh = await browser.newContext()
  try {
    const direct = await fresh.newPage()
    await direct.goto(savedUrl)
    await expectSavedDetails(direct, record)
  } finally {
    await fresh.close()
  }
})

for (const scenario of [
  { name: 'empty', value: '', message: 'Ham prompt alanı zorunludur.' },
  { name: 'whitespace-only', value: '   \n  ', message: 'Ham prompt yalnızca boşluk veya satır sonu karakterlerinden oluşamaz.' },
]) {
  test(`rejects ${scenario.name} prompt without creating a record`, async ({ page, request }) => {
    await page.goto('/')
    const input = page.getByRole('textbox', { name: /Ham Prompt İsteği/ })
    await input.fill(scenario.value)
    const posts: string[] = []
    page.on('request', (sent) => {
      if (sent.method() === 'POST' && new URL(sent.url()).pathname === '/api/v1/prompt-requests') posts.push(sent.url())
    })
    await page.getByRole('button', { name: 'Prompt İsteğini Kaydet' }).click()
    await expect(page.getByText(scenario.message, { exact: true })).toBeVisible()
    await expect(input).toHaveAttribute('aria-invalid', 'true')
    await expect(input).toHaveValue(scenario.value)
    await expect(page).toHaveURL('/')
    const history = await request.get('/api/v1/prompt-requests')
    expect(history.status()).toBe(200)
    expect(await history.json()).toMatchObject({ total: 0, items: [] })
    expect(posts).toEqual([])
  })
}

test('new prompt appears in history and opens the correct saved record', async ({ page }) => {
  const record = await createInBrowser(page, promptPayload({ target_mode: 'anthropic' }))
  await page.getByRole('navigation', { name: 'Ana gezinme' }).getByRole('link', { name: 'Prompt Geçmişi', exact: true }).click()
  const card = historyCard(page, record.raw_prompt)
  await expect(card).toBeVisible()
  await expect(card.getByText('Claude', { exact: true })).toBeVisible()
  await expect(card.getByText('(anthropic)', { exact: true })).toBeVisible()
  await card.getByRole('link', { name: 'Detayları Görüntüle' }).click()
  await expect(page).toHaveURL(`/prompt-requests/${record.id}`)
  await expectSavedDetails(page, record)
  await page.getByRole('link', { name: '← Prompt Geçmişine Dön' }).click()
  await expect(page).toHaveURL('/history')
  await expect(card).toBeVisible()
})

test('mobile user can save, search, filter, and reopen prompt details', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  const record = await createInBrowser(page, promptPayload({ target_mode: 'google' }))
  await expectSavedDetails(page, record)
  await expectNoHorizontalOverflow(page)
  const navigation = page.getByRole('navigation', { name: 'Ana gezinme' })
  await expect(navigation.getByRole('link', { name: 'Prompt Geçmişi', exact: true })).toBeInViewport()
  await navigation.getByRole('link', { name: 'Prompt Geçmişi', exact: true }).click()
  const search = page.getByRole('searchbox', { name: 'Anahtar Kelime ile Ara' })
  await search.fill(record.raw_prompt.split(' ')[0]!)
  await page.getByRole('group', { name: 'Hedef Yapay Zekâ Ailesi' }).getByRole('button', { name: 'Gemini', exact: true }).click()
  const card = historyCard(page, record.raw_prompt)
  await expect(card).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await card.getByRole('link', { name: 'Detayları Görüntüle' }).click()
  await expectSavedDetails(page, record)
  await expectNoHorizontalOverflow(page)
})
