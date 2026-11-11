import { test, expect } from './local-spa-fixture'

for (const [name, viewport] of [
  ['desktop', { width: 1280, height: 900 }], ['mobile', { width: 375, height: 812 }],
] as const) {
  test(`${name}: save, configure, create and explicitly submit without duplicate execution`, async ({ page, request }, testInfo) => {
    await page.setViewportSize(viewport)
    const posts: string[] = []
    page.on('request', event => { if (event.method() === 'POST') posts.push(new URL(event.url()).pathname) })
    await page.goto('/')
    await page.getByLabel('Ham Prompt İsteği', { exact: false }).fill('Explain Python 3.12. Keep Merhaba.')
    await page.getByRole('button', { name: 'Prompt İsteğini Kaydet' }).click()
    await expect(page.getByRole('heading', { name: 'Prompt İsteği Detayları', exact: true })).toBeVisible()
    expect(posts).toEqual(['/api/v1/prompt-requests'])
    const configure = page.getByRole('button', { name: 'Optimizasyonu Yapılandır' })
    await configure.focus()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('button', { name: 'Yapılandırmayı Gizle' })).toHaveAttribute('aria-expanded', 'true')
    const rounds = page.getByLabel('En fazla değerlendirme turu')
    await expect(rounds).toHaveValue('3')
    await rounds.selectOption('2')
    const creation = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/runs'))
    await page.getByRole('button', { name: 'Workflow Oluştur', exact: true }).click()
    const created = await creation
    expect(created.status()).toBe(201)
    const row = (await created.json()).workflow as { id: string }
    await expect(page.getByText(row.id, { exact: true })).toBeVisible()
    await expect(page.getByText('Workflow oluşturuldu. Optimizasyon henüz başlatılmadı.')).toBeVisible()
    expect(posts).toHaveLength(2)
    expect((await (await request.get(`/api/v1/runs/${row.id}/history`)).json()).operations).toEqual([])
    await page.reload()
    await expect(page.getByText(row.id, { exact: true })).toBeVisible()
    expect(posts).toHaveLength(2)
    const submission = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/execution'))
    await page.getByRole('button', { name: 'Optimizasyonu Başlat', exact: true }).click()
    expect((await submission).status()).toBe(202)
    await expect(page.getByText('Optimizasyon isteği kabul edildi.', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Optimizasyonu Başlat', exact: true })).toBeDisabled()
    await expect(page.getByText(/tamamlandığı veya kalite koşullarını sağladığı anlamına gelmez/)).toBeVisible()
    expect(posts.filter(url => url.endsWith('/execution'))).toHaveLength(1)
    await page.reload()
    await expect(page.getByText('Optimizasyon isteği kabul edildi.', { exact: true })).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath('optimization-accepted.png'), fullPage: true })
    expect(posts).toHaveLength(3)
    await expect.poll(async () => (await (await request.get(`/api/v1/runs/${row.id}`)).json()).status).toBe('COMPLETED')
    await page.getByRole('button', { name: 'Kayıtlı Durumu Kontrol Et' }).click()
    await expect(page.getByText(/Son okunan kayıtlı durum: Yürütme tamamlandı/)).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    const parent = new URL(page.url()).pathname.split('/').at(-1)!
    const lists = await (await request.get(`/api/v1/prompt-requests/${parent}/runs`)).json()
    expect(lists.items).toHaveLength(1)
    expect(posts).toHaveLength(3)
    await page.getByRole('link', { name: 'Prompt Geçmişine Git', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Prompt Geçmişi', exact: true })).toBeVisible()
  })
}
