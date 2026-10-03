import { expect, provider, test, upload, originalVisible } from './helpers.mjs'

test.beforeEach(async ({ request }) => provider(request))

async function getBuildManifest(request) {
  const response = await request.get('/document-checker-manifest.json')
  expect(response.ok()).toBeTruthy()
  return response.json()
}

function assetPattern(path) {
  const escaped = path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return new RegExp(`${escaped}(?:\\?.*)?$`)
}

function pathname(url) {
  return new URL(url).pathname.replace(/^\//, '')
}

test('startup keeps PDF.js out of the network at desktop, tablet, and mobile widths', async ({ page, request }, testInfo) => {
  const manifest = await getBuildManifest(request)
  const pdfModule = manifest['src/components/PdfOriginalViewer.tsx'].file
  const pdfWorker = manifest['node_modules/pdfjs-dist/build/pdf.worker.min.mjs'].file
  await page.addInitScript(() => localStorage.setItem('document-checker-new-chat', 'true'))
  const viewports = [
    { width: 1440, height: 900 },
    { width: 1024, height: 768 },
    { width: 390, height: 844 },
  ]
  const measurements = []

  for (const viewport of viewports) {
    await page.setViewportSize(viewport)
    const requests = []
    const onRequest = (entry) => requests.push(entry)
    page.on('request', onRequest)
    await page.goto('/')
    await expect(page.getByRole('heading', { name: /Загрузите документ/ })).toBeVisible()
    const metrics = await page.evaluate(() => {
      const navigation = performance.getEntriesByType('navigation')[0]
      const paint = performance.getEntriesByName('first-contentful-paint')[0]
      const scripts = performance.getEntriesByType('resource').filter((entry) => /\.(?:m?js)(?:\?|$)/i.test(entry.name))
      return {
        firstContentfulPaintMs: paint?.startTime ?? null,
        domContentLoadedMs: navigation?.domContentLoadedEventEnd ?? null,
        jsTransferBytes: scripts.reduce((total, entry) => total + entry.transferSize, 0),
        jsEncodedBytes: scripts.reduce((total, entry) => total + entry.encodedBodySize, 0),
      }
    })
    expect(requests.some((entry) => pathname(entry.url()) === pdfModule || pathname(entry.url()) === pdfWorker)).toBeFalsy()
    measurements.push({ ...viewport, ...metrics })
    page.off('request', onRequest)
  }

  await testInfo.attach('M20 startup metrics', { body: JSON.stringify(measurements, null, 2), contentType: 'application/json' })
})

test('a non-PDF document does not load the PDF viewer chunk or worker', async ({ page, request }) => {
  const manifest = await getBuildManifest(request)
  const pdfModule = manifest['src/components/PdfOriginalViewer.tsx'].file
  const pdfWorker = manifest['node_modules/pdfjs-dist/build/pdf.worker.min.mjs'].file
  const requests = []
  page.on('request', (entry) => requests.push(entry))

  await upload(page, 'sample.docx')
  await originalVisible(page, 'docx')

  expect(requests.some((entry) => pathname(entry.url()) === pdfModule || pathname(entry.url()) === pdfWorker)).toBeFalsy()
})

test('opening a PDF loads its viewer and worker on demand and preserves citation highlighting', async ({ page, request }, testInfo) => {
  const manifest = await getBuildManifest(request)
  const pdfModule = manifest['src/components/PdfOriginalViewer.tsx'].file
  const pdfWorker = manifest['node_modules/pdfjs-dist/build/pdf.worker.min.mjs'].file
  const requests = []
  let pdfModuleRequestedAt = null
  const onRequest = (entry) => {
    requests.push(entry)
    if (pathname(entry.url()) === pdfModule && pdfModuleRequestedAt === null) pdfModuleRequestedAt = Date.now()
  }
  page.on('request', onRequest)

  const document = await upload(page, 'sample.pdf')
  const uploadIndex = requests.findIndex((entry) => pathname(entry.url()) === 'api/v1/documents' && entry.method() === 'POST')
  expect(uploadIndex).toBeGreaterThanOrEqual(0)
  expect(requests.slice(0, uploadIndex).some((entry) => pathname(entry.url()) === pdfModule || pathname(entry.url()) === pdfWorker)).toBeFalsy()
  await originalVisible(page, 'pdf')
  expect(requests.filter((entry) => pathname(entry.url()) === pdfModule)).toHaveLength(1)
  const workerRequests = requests.filter((entry) => pathname(entry.url()) === pdfWorker)
  const workerRequestHeaders = await Promise.all(workerRequests.map((entry) => entry.allHeaders()))
  const workerBootstraps = workerRequestHeaders.filter((headers) => headers['sec-fetch-dest'] === 'worker')
  expect(workerBootstraps).toHaveLength(1)
  expect(pdfModuleRequestedAt).not.toBeNull()

  const citation = page.locator('.citation-chip').first()
  await expect(citation).toBeVisible()
  await citation.click()
  await expect(page.locator('#document-original-viewer .pdf-text-match, #document-original-viewer .pdf-ocr-highlight-box').first()).toBeVisible()
  const citationWorkerRequests = requests.filter((entry) => pathname(entry.url()) === pdfWorker)
  const citationWorkerHeaders = await Promise.all(citationWorkerRequests.map((entry) => entry.allHeaders()))
  expect(citationWorkerHeaders.filter((headers) => headers['sec-fetch-dest'] === 'worker')).toHaveLength(1)

  await testInfo.attach('M20 PDF render timing', {
    body: JSON.stringify({
      documentId: document.id,
      moduleToVisibleCanvasMs: Date.now() - pdfModuleRequestedAt,
      pdfModuleRequests: requests.filter((entry) => pathname(entry.url()) === pdfModule).length,
      workerAssetRequests: workerRequests.length,
      workerBootstraps: workerBootstraps.length,
    }, null, 2),
    contentType: 'application/json',
  })
  page.off('request', onRequest)
})

test('failed PDF code chunk can be retried without losing the selected citation', async ({ page, request }) => {
  const manifest = await getBuildManifest(request)
  const pdfModule = manifest['src/components/PdfOriginalViewer.tsx'].file
  const pdfModulePattern = assetPattern(pdfModule)
  let failedRequests = 0
  await page.route(pdfModulePattern, async (route) => {
    failedRequests += 1
    await route.abort('failed')
  })
  await upload(page, 'sample.pdf')
  await expect(page.locator('.preview-render-error')).toContainText('Не удалось загрузить модуль просмотра PDF')

  const citation = page.locator('.citation-chip').first()
  await expect(citation).toBeVisible()
  const sourceId = await citation.getAttribute('data-source-id')
  await citation.click()
  await expect(page.locator('.original-viewer-body')).toHaveAttribute('data-selected-source', sourceId)

  await page.unroute(pdfModulePattern)
  await page.locator('.preview-render-error').getByRole('button', { name: 'Повторить' }).click()
  await originalVisible(page, 'pdf')
  await expect(page.locator('.original-viewer-body')).toHaveAttribute('data-selected-source', sourceId)
  await expect(page.locator('#document-original-viewer .pdf-text-match, #document-original-viewer .pdf-ocr-highlight-box').first()).toBeVisible()
  expect(failedRequests).toBe(1)
})

test('failed PDF worker keeps browser fallback available and offers a retry', async ({ page, request }) => {
  const manifest = await getBuildManifest(request)
  const pdfWorker = manifest['node_modules/pdfjs-dist/build/pdf.worker.min.mjs'].file
  const pdfWorkerPattern = assetPattern(pdfWorker)
  let failedRequests = 0
  await page.route(pdfWorkerPattern, async (route) => {
    failedRequests += 1
    await route.abort('failed')
  })
  await upload(page, 'sample.pdf')
  await expect(page.locator('.pdf-native-fallback')).toBeVisible()
  await expect(page.locator('.pdf-native-fallback-note')).toContainText('Встроенный просмотр PDF.js недоступен')
  const citation = page.locator('.citation-chip').first()
  await expect(citation).toBeVisible()
  const sourceId = await citation.getAttribute('data-source-id')
  await citation.click()
  await expect(page.locator('.original-viewer-body')).toHaveAttribute('data-selected-source', sourceId)

  await page.unroute(pdfWorkerPattern)
  await page.locator('.pdf-native-fallback-note').getByRole('button', { name: 'Повторить встроенный просмотр' }).click()
  await originalVisible(page, 'pdf')
  await expect(page.locator('.original-viewer-body')).toHaveAttribute('data-selected-source', sourceId)
  await expect(page.locator('#document-original-viewer .pdf-text-match, #document-original-viewer .pdf-ocr-highlight-box').first()).toBeVisible()
  expect(failedRequests).toBeGreaterThan(0)
})
