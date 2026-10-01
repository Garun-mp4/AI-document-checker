import assert from 'node:assert/strict'
import { chromium } from '@playwright/test'

const baseUrl = process.env.AI_CHECKER_BASE_URL || 'http://localhost:5173'
const browser = await chromium.launch({ headless: true })
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const [htmlResponse, manifestResponse, versionResponse] = await Promise.all([
    fetch(baseUrl),
    fetch(`${baseUrl}/build-info.json`, { cache: 'no-store' }),
    fetch(`${baseUrl}/api/v1/version`, { cache: 'no-store' }),
  ])
  assert.equal(htmlResponse.status, 200)
  assert.match(htmlResponse.headers.get('cache-control') || '', /no-store/)
  assert.equal(manifestResponse.status, 200)
  assert.match(manifestResponse.headers.get('cache-control') || '', /no-store/)
  assert.equal(versionResponse.status, 200)
  assert.match(versionResponse.headers.get('cache-control') || '', /no-store/)
  const manifest = await manifestResponse.json()
  const apiVersion = await versionResponse.json()
  for (const key of ['build_id', 'commit', 'built_at']) assert.equal(manifest[key], apiVersion[key])
  const html = await htmlResponse.text()
  const scriptPath = html.match(/<script[^>]+src="([^"]+\.js)"/)?.[1]
  assert.ok(scriptPath, 'Production HTML references a JavaScript bundle')
  const scriptResponse = await fetch(new URL(scriptPath, baseUrl))
  assert.equal(scriptResponse.status, 200)
  assert.match(scriptResponse.headers.get('cache-control') || '', /immutable/)
  const workerPath = manifest.assets.find(asset => asset.includes('pdf.worker') && asset.endsWith('.mjs'))
  assert.ok(workerPath, 'Build manifest lists the PDF.js worker')
  const workerResponse = await fetch(new URL(workerPath, baseUrl))
  assert.equal(workerResponse.status, 200)
  assert.match(workerResponse.headers.get('content-type') || '', /application\/javascript/)
  assert.match(workerResponse.headers.get('cache-control') || '', /immutable/)

  const response = await page.goto(baseUrl, { waitUntil: 'networkidle' })
  assert.equal(response?.status(), 200, 'Web root returns HTTP 200')
  await page.getByRole('button', { name: 'Свернуть библиотеку', exact: true }).click()
  assert.equal(await page.locator('#chat-library').evaluate(element => Math.round(element.getBoundingClientRect().width)), 72)
  await page.getByRole('button', { name: 'Развернуть библиотеку', exact: true }).click()
  assert.equal(await page.locator('#chat-library').evaluate(element => Math.round(element.getBoundingClientRect().width)), 240)

  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: 'Открыть документы', exact: true }).click()
  assert.equal(await page.locator('#chat-library').evaluate(element => Math.round(element.getBoundingClientRect().width)), 300)
  await page.getByRole('button', { name: 'Свернуть библиотеку', exact: true }).click()
  assert.equal(await page.locator('#chat-library').evaluate(element => Math.round(element.getBoundingClientRect().width)), 72)
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'Mobile UI has no horizontal overflow')

  await page.setViewportSize({ width: 1280, height: 720 })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'Desktop UI has no horizontal overflow')
  assert.deepEqual(errors, [], 'No uncaught browser errors')
  console.log(`Deployment smoke passed at ${baseUrl}: root, sidebar interaction, mobile layout, no browser errors`)
} finally {
  await browser.close()
}
