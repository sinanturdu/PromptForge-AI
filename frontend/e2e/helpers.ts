import { randomUUID } from 'node:crypto'
import type { APIRequestContext, Page } from '@playwright/test'
import type {
  PromptRequestCreatePayload,
  PromptRequestRecord,
} from '../src/features/prompt-requests/types'
import { expect } from './fixtures'

export const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export function uniqueToken() {
  return `e2e${randomUUID().replaceAll('-', '')}`
}

export function promptPayload(overrides: Partial<PromptRequestCreatePayload> = {}): PromptRequestCreatePayload {
  return {
    raw_prompt: `${uniqueToken()} — Ekip için haftalık çalışma planı hazırla.`,
    user_context: 'Türkçe arayüz kullanan küçük bir yazılım ekibi.',
    target_mode: 'openai',
    output_language: 'en',
    task_profile: 'software_development',
    prompt_detail_level: 'high',
    response_detail_preference: 'comprehensive',
    reasoning_preference: 'low',
    ...overrides,
  }
}

// Setup only: sends real HTTP requests through the dedicated E2E Vite proxy.
export async function createRecord(request: APIRequestContext, payload: PromptRequestCreatePayload) {
  const response = await request.post('/api/v1/prompt-requests', { data: payload })
  expect(response.status()).toBe(201)
  const record = await response.json() as PromptRequestRecord
  expect(record.id).toMatch(UUID_V4)
  expect(record).toMatchObject({ ...payload })
  return record
}

export async function createInBrowser(page: Page, payload = promptPayload()) {
  await page.goto('/')
  await page.getByRole('textbox', { name: /Ham Prompt İsteği/ }).fill(payload.raw_prompt)
  await page.getByRole('textbox', { name: /Proje Bağlamı/ }).fill(payload.user_context ?? '')
  const targetLabel = { generic: 'Universal', openai: 'ChatGPT / GPT', anthropic: 'Claude', google: 'Gemini' }[payload.target_mode]
  // Click the visible label content; check() on sr-only inputs targets a clipped pixel.
  await page.getByRole('group', { name: 'Hedef Yapay Zekâ Ailesi' })
    .getByText(targetLabel, { exact: true }).click()
  await expect(page.getByRole('radio', { name: new RegExp(targetLabel) })).toBeChecked()
  await page.getByRole('combobox', { name: 'Çıktı Dili' }).selectOption(payload.output_language ?? 'tr')
  await page.getByRole('textbox', { name: /Görev Profili/ }).fill(payload.task_profile ?? '')
  const promptDetail = { low: 'Düşük', medium: 'Orta', high: 'Yüksek' }[payload.prompt_detail_level]
  const responseDetail = { minimal: 'Minimal', balanced: 'Dengeli', comprehensive: 'Kapsamlı' }[payload.response_detail_preference]
  const reasoning = { auto: 'Otomatik', low: 'Düşük', medium: 'Orta', high: 'Yüksek' }[payload.reasoning_preference]
  await page.getByRole('group', { name: /Prompt Detay Seviyesi/ }).getByText(promptDetail, { exact: true }).click()
  await page.getByRole('group', { name: /Beklenen Yanıt Detay Tercihi/ }).getByText(responseDetail, { exact: true }).click()
  await page.getByRole('group', { name: /Akıl Yürütme Tercihi/ }).getByText(reasoning, { exact: true }).click()
  const saved = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/v1/prompt-requests' && response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: 'Prompt İsteğini Kaydet' }).click()
  const response = await saved
  expect(response.status()).toBe(201)
  const record = await response.json() as PromptRequestRecord
  expect(record.id).toMatch(UUID_V4)
  expect(record).toMatchObject({ ...payload })
  await expect(page).toHaveURL(`/prompt-requests/${record.id}`)
  return record
}

export async function expectSavedDetails(page: Page, record: PromptRequestRecord) {
  await expect(page.getByRole('heading', { level: 1, name: 'Prompt İsteği Detayları' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Kayıt üst verileri' }).getByText(record.id, { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Orijinal Ham Prompt' }).getByText(record.raw_prompt, { exact: true })).toBeVisible()
  if (record.user_context) {
    await expect(page.getByRole('region', { name: 'Proje ve Kullanım Bağlamı' }).getByText(record.user_context, { exact: true })).toBeVisible()
  }
  const preferences = page.getByRole('region', { name: 'Kaydedilen Tercihler' })
  const target = { generic: 'Universal', openai: 'ChatGPT / GPT', anthropic: 'Claude', google: 'Gemini' }[record.target_mode]
  await expect(preferences.getByText(target, { exact: true })).toBeVisible()
  await expect(preferences.getByText(record.target_mode, { exact: true })).toBeVisible()
  await expect(preferences.getByText(record.output_language === 'en' ? 'İngilizce (en)' : 'Türkçe (tr)', { exact: true })).toBeVisible()
  await expect(preferences.getByText(record.task_profile!, { exact: true })).toBeVisible()
  await expect(preferences.getByText(`(${record.prompt_detail_level})`, { exact: true })).toBeVisible()
  await expect(preferences.getByText(`(${record.response_detail_preference})`, { exact: true })).toBeVisible()
  await expect(preferences.getByText(`(${record.reasoning_preference})`, { exact: true })).toBeVisible()
}

export function historyCard(page: Page, prompt: string) {
  return page.getByRole('article').filter({ hasText: prompt })
}

export async function expectHistoryRecords(page: Page, records: readonly PromptRequestRecord[]) {
  await expect(page.getByRole('article')).toHaveCount(records.length)
  for (const record of records) await expect(historyCard(page, record.raw_prompt)).toBeVisible()
}

export async function expectHistoryQuery(page: Page, query: { q?: string; target_mode?: string; page?: string }) {
  await expect(page).toHaveURL((url) => {
    if (url.pathname !== '/history') return false
    return ['q', 'target_mode', 'page'].every((key) =>
      url.searchParams.get(key) === (query[key as keyof typeof query] ?? null),
    )
  })
}

export async function expectNoHorizontalOverflow(page: Page) {
  await expect.poll(() => page.evaluate(() =>
    document.documentElement.scrollWidth <= document.documentElement.clientWidth,
  )).toBe(true)
}
