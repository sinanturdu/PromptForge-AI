import { test, expect } from '@playwright/test'

test('application startup shows the primary prompt controls', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: 'Yeni bir prompt oluştur' })).toBeVisible()
  await expect(page.getByRole('textbox', { name: /Ham Prompt İsteği/ })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Prompt İsteğini Kaydet' })).toBeVisible()
})

test('navigation connects New Prompt and Prompt History', async ({ page }) => {
  await page.goto('/')
  const navigation = page.getByRole('navigation', { name: 'Ana gezinme' })
  await navigation.getByRole('link', { name: 'Prompt Geçmişi', exact: true }).click()
  await expect(page).toHaveURL('/history')
  await expect(page.getByRole('heading', { level: 1, name: 'Prompt Geçmişi' })).toBeVisible()
  await expect(navigation.getByRole('link', { name: 'Prompt Geçmişi', exact: true })).toHaveAttribute('aria-current', 'page')
  await navigation.getByRole('link', { name: 'Yeni Prompt', exact: true }).click()
  await expect(page).toHaveURL('/')
  await expect(page.getByRole('textbox', { name: /Ham Prompt İsteği/ })).toBeVisible()
})

test('mobile primary controls are reachable without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: 'Yeni bir prompt oluştur' })).toBeVisible()
  const input = page.getByRole('textbox', { name: /Ham Prompt İsteği/ })
  await input.fill('Mobil görünümde yazılan bir prompt isteği.')
  await expect(input).toHaveValue('Mobil görünümde yazılan bir prompt isteği.')
  const save = page.getByRole('button', { name: 'Prompt İsteğini Kaydet' })
  await save.scrollIntoViewIfNeeded()
  await expect(save).toBeInViewport()
  await expect(save).toBeEnabled()
  await expect.poll(() => page.evaluate(() =>
    document.documentElement.scrollWidth <= document.documentElement.clientWidth,
  )).toBe(true)
  const navigation = page.getByRole('navigation', { name: 'Ana gezinme' })
  await expect(navigation.getByRole('link', { name: 'Prompt Geçmişi', exact: true })).toBeInViewport()
})

test('isolated empty database displays the empty history state', async ({ page }) => {
  await page.goto('/history')
  await expect(page.getByRole('heading', { name: 'Henüz kayıtlı prompt isteğiniz bulunmuyor.' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'İlk Promptunu Oluştur' })).toBeVisible()
  await expect(page.getByLabel('Toplam kayıt sayısı')).toHaveText('Toplam 0 kayıt')
})
