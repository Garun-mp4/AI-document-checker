import { test as base, expect } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { readFile } from 'node:fs/promises'

export const fixtures = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures')
export const test = base.extend({
  page: async ({ page }, use, testInfo) => {
    const errors = []
    const consoleMessages = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => consoleMessages.push(`${message.type()}: ${message.text()}`))
    page.on('requestfailed', request => consoleMessages.push(`requestfailed: ${request.url()} ${request.failure()?.errorText}`))
    await use(page)
    await testInfo.attach('browser-log', { body: JSON.stringify({ errors, consoleMessages }, null, 2), contentType: 'application/json' })
    expect(errors, 'No uncaught browser errors / white-screen failures').toEqual([])
  },
})
export { expect }

export async function provider(request, options = {}) {
  const response = await request.post('/api/v1/__e2e/provider', { data: options })
  expect(response.ok()).toBeTruthy()
}

export async function upload(page, name, expected = 'ready') {
  await page.goto('/')
  const uploaded = page.waitForResponse(response => response.url().endsWith('/api/v1/documents') && response.request().method() === 'POST')
  await page.getByLabel('Выберите документ', { exact: true }).setInputFiles(path.join(fixtures, name))
  const response = await uploaded
  expect(response.status(), await response.text()).toBe(202)
  const document = await response.json()
  await expect.poll(async () => {
    const result = await page.request.get(`/api/v1/documents/${document.id}`)
    return (await result.json()).status
  }, { timeout: 150_000, message: `Process ${name} with real parsers, OCR and embeddings` }).toBe(expected)
  if (expected === 'ready') {
    const original = await page.request.get(`/api/v1/documents/${document.id}/file`)
    expect(await original.body(), 'Original bytes are immutable after conversion').toEqual(await readFile(path.join(fixtures, name)))
    await expect(page.locator('.insight-card')).toHaveCount(7)
    await expect(page.getByRole('heading', { name: name, exact: true })).toBeVisible()
  } else {
    await expect(page.locator('.issue-banner')).toBeVisible()
  }
  return document
}

export async function showChat(page) {
  if (await page.getByRole('button', { name: 'Открыть чат', exact: true }).count()) {
    await page.getByRole('button', { name: 'Открыть чат', exact: true }).click()
  }
  await expect(page.getByLabel('Сообщение для чата', { exact: true })).toBeVisible()
}

export async function originalVisible(page, renderer) {
  const viewer = page.locator('#document-original-viewer')
  const surfaces = {
    pdf: '.pdf-page-sheet canvas',
    docx: '.docx-render-host section.docx-preview',
    text: '.source-text-line',
    csv: '.original-csv-table tbody tr',
    mapped: '.source-map-original-viewer .preview-block',
  }
  await expect(viewer.locator(surfaces[renderer]).first()).toBeVisible()
  await expect(viewer.locator('.preview-render-error')).toHaveCount(0)
  if (renderer === 'pdf') {
    expect(await viewer.locator('canvas').evaluate(canvas => canvas.width)).toBeGreaterThan(100)
    await expect.poll(() => viewer.locator('canvas').evaluate(canvas => {
      const { data } = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height)
      return data.some((value, index) => index % 4 !== 3 && value < 180)
    }), { message: 'Canvas contains actual document pixels after rendering' }).toBeTruthy()
  }
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
  expect(overflow, 'No whole-page horizontal overflow').toBeFalsy()
}
