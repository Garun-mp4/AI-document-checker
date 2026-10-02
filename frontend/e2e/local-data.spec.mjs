import { test, expect } from './helpers.mjs'

test('local data dialog previews deletion, supports cancellation and fits mobile screens', async ({ page, request }) => {
  await request.post('/api/v1/__e2e/provider', { data: { mode: 'ready' } })
  const filename = `m15-maintenance-${Date.now()}.txt`
  const upload = await request.post('/api/v1/documents', {
    multipart: { file: { name: filename, mimeType: 'text/plain', buffer: Buffer.from('M15 document content stays local during this test.') } },
  })
  expect(upload.status(), await upload.text()).toBe(202)
  const document = await upload.json()
  try {
    await expect.poll(async () => (await (await request.get(`/api/v1/documents/${document.id}`)).json()).status,
      { timeout: 150_000 }).toBe('ready')

    await page.goto('/')
    await page.getByRole('button', { name: 'Управление локальными данными' }).click()
    const dialog = page.getByRole('dialog', { name: 'Локальные данные' })
    await expect(dialog).toBeVisible()
    await expect(dialog.getByText('Авторизация Codex защищена')).toBeVisible()
    await expect(dialog.getByText(filename, { exact: true })).toBeVisible()

    await page.setViewportSize({ width: 1440, height: 900 })
    const desktopBounds = await dialog.evaluate(element => element.getBoundingClientRect().toJSON())
    expect(desktopBounds.width).toBeLessThanOrEqual(900)
    expect(desktopBounds.height).toBeLessThanOrEqual(876)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()

    await page.setViewportSize({ width: 390, height: 844 })
    await expect(dialog.getByRole('heading', { name: 'Локальные данные' })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    const bounds = await dialog.evaluate(element => element.getBoundingClientRect().toJSON())
    expect(bounds.width).toBeLessThanOrEqual(390)

    await dialog.getByRole('checkbox', { name: `Выбрать ${filename}` }).check()
    await dialog.getByRole('button', { name: /Удалить выбранные/ }).click()
    await expect(dialog.getByRole('heading', { name: `Удалить: Выбранные чаты и документы` })).toBeVisible()
    await expect(dialog.getByText(filename, { exact: true })).toBeVisible()
    await expect(dialog.getByLabel('Для подтверждения введите: УДАЛИТЬ ВЫБРАННЫЕ ДАННЫЕ')).toBeVisible()
    await dialog.getByRole('button', { name: 'Отмена', exact: true }).click()
    expect((await request.get(`/api/v1/documents/${document.id}`)).status()).toBe(200)

    const downloadPromise = page.waitForEvent('download')
    await dialog.getByRole('button', { name: 'Скачать диагностику' }).click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toBe('document-checker-diagnostics.zip')
    await page.keyboard.press('Escape')
    await expect(dialog).toHaveCount(0)
  } finally {
    await request.delete(`/api/v1/documents/${document.id}`)
  }
})
