import { test, expect } from './local-spa-fixture'
import type { Page } from '@playwright/test'

async function begin(page: Page) {
  const posts: string[] = []
  page.on('request', request => { if (request.method() === 'POST') posts.push(new URL(request.url()).pathname) })
  await page.goto('/')
  await page.getByLabel('Ham Prompt İsteği', { exact: false }).fill('Explain Python 3.12. Keep Merhaba.')
  await page.getByRole('button', { name: 'Prompt İsteğini Kaydet' }).click()
  await page.getByRole('button', { name: 'Optimizasyonu Yapılandır' }).click()
  const creation = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/runs'))
  await page.getByRole('button', { name: 'Workflow Oluştur', exact: true }).click()
  const id = (await (await creation).json()).workflow.id as string
  await page.getByRole('button', { name: 'Optimizasyonu Başlat', exact: true }).click()
  const monitor = page.getByRole('region', { name: 'Workflow İlerlemesi', exact: true })
  await monitor.scrollIntoViewIfNeeded()
  return { id, monitor, posts }
}

test.describe('durable three-round workflow', () => {
  test.use({ spaScenario: 'three-round', spaControlled: true })
  test('refresh during execution follows Architect/Critic and all three committed evaluations', async ({ page, localSpa }, testInfo) => {
    const { monitor, posts } = await begin(page)
    await expect.poll(localSpa.operationCount).toBe(1)
    await expect(monitor.getByText(/Son okunan kayıtlı durum: Prompt hazırlanıyor/)).toBeVisible()
    await page.reload()
    await monitor.scrollIntoViewIfNeeded()
    await expect(monitor.getByText(/Son okunan kayıtlı durum: Prompt hazırlanıyor/)).toBeVisible()
    for (const [number, label] of [[2, 'Prompt değerlendiriliyor'], [3, 'Prompt iyileştiriliyor'],
      [4, 'Prompt değerlendiriliyor'], [5, 'Prompt iyileştiriliyor'], [6, 'Prompt değerlendiriliyor']] as const) {
      localSpa.release()
      await expect.poll(localSpa.operationCount).toBe(number)
      await expect(monitor.getByText(`Son okunan kayıtlı durum: ${label}.`, { exact: true })).toBeVisible()
    }
    localSpa.release()
    await expect(monitor.getByText(/Son okunan kayıtlı durum: Yürütme tamamlandı/)).toBeVisible()
    await expect(monitor.getByLabel('Doğrulanmış tamamlanan değerlendirme sayısı')).toHaveText('3')
    expect(posts.filter(path => path.endsWith('/execution'))).toHaveLength(1)
    await monitor.getByRole('button', { name: 'İşlem Özetini Görüntüle' }).click()
    await expect(monitor.getByText(/Critic · Değerlendirme · Tamamlandı/)).toHaveCount(3)
    await page.screenshot({ path: testInfo.outputPath('three-round-progress.png'), fullPage: true })
  })
})

for (const [scenario, label, operations] of [
  ['early', 'Yürütme tamamlandı', 2], ['failed', 'İşlem başarısız', 1], ['clarify', 'Ek açıklama gerekiyor', 1],
] as const) {
  test.describe(scenario, () => {
    test.use({ spaScenario: scenario, spaControlled: true })
    test(`${scenario} terminal state is observed without restart`, async ({ page, localSpa }) => {
      const { monitor, posts } = await begin(page)
      for (let number = 1; number <= operations; number++) {
        await expect.poll(localSpa.operationCount).toBe(number)
        localSpa.release()
      }
      await expect(monitor.getByText(`Son okunan kayıtlı durum: ${label}.`, { exact: true })).toBeVisible()
      await expect(monitor.getByRole('button', { name: 'İzlemeyi Duraklat' })).toHaveCount(0)
      expect(posts.filter(path => path.endsWith('/execution'))).toHaveLength(1)
    })
  })
}

test.describe('uncertain submission and interrupted polling', () => {
  test.use({ spaControlled: true })
  test('GET recovery never repeats a disconnected accepted start', async ({ page, localSpa }) => {
    await page.route(/\/api\/v1\/runs\/[^/]+\/execution$/, async route => {
      const response = await route.fetch()
      expect(response.status()).toBe(202)
      await route.abort('failed')
    })
    const { monitor, posts, id } = await begin(page)
    await expect.poll(localSpa.operationCount).toBe(1)
    await expect(page.getByText('Başlatma isteğinin sonucu belirsiz.', { exact: true })).toBeVisible()
    await expect(monitor.getByText(/Son okunan kayıtlı durum: Prompt hazırlanıyor/)).toBeVisible()
    await page.reload()
    await monitor.scrollIntoViewIfNeeded()
    await expect(page.getByRole('button', { name: 'Optimizasyonu Başlat', exact: true })).toBeDisabled()
    const progressUrl = new RegExp(`/api/v1/runs/${id}\\?`)
    await page.route(progressUrl, route => route.abort('failed'))
    await monitor.getByRole('button', { name: 'Şimdi Yenile' }).click()
    await expect(monitor.getByRole('alert')).toContainText('İlerleme yenilenemedi')
    await expect(monitor.getByText(/Son okunan kayıtlı durum: Prompt hazırlanıyor/)).toBeVisible()
    await page.unroute(progressUrl)
    await monitor.getByRole('button', { name: 'Şimdi Yenile' }).click()
    await expect(monitor.getByRole('alert')).toHaveCount(0)
    expect(posts.filter(path => path.endsWith('/execution'))).toHaveLength(1)
    expect(localSpa.operationCount()).toBe(1)
    localSpa.release(); await expect.poll(localSpa.operationCount).toBe(2); localSpa.release()
    await expect(monitor.getByText(/Son okunan kayıtlı durum: Yürütme tamamlandı/)).toBeVisible()
    await expect(page.getByRole('button', { name: 'Optimizasyonu Başlat', exact: true })).toBeDisabled()
  })
})
