import path from 'node:path'
import { readFile } from 'node:fs/promises'
import { test, expect, fixtures, provider } from './helpers.mjs'

test.beforeEach(async ({ request }) => provider(request))

for (const name of ['traversal.docx', 'traversal.xlsx', 'traversal.pptx', 'traversal.epub',
  'remote.docx', 'unsafe-link.docx', 'zip-bomb.docx', 'many-parts.epub', 'entity.xlsx', 'binary.txt']) {
  test(`Security: ${name} rejected before persistence`, async ({ page, request }) => {
    const before = await (await request.get('/api/v1/documents')).json()
    const response = await request.post('/api/v1/documents', { multipart: {
      file: { name, mimeType: 'application/octet-stream', buffer: await readFile(path.join(fixtures, name)) },
    } })
    expect(response.status(), await response.text()).toBe(422)
    expect((await response.json()).detail).toBeTruthy()
    const after = await (await request.get('/api/v1/documents')).json()
    expect(after.map(document => document.id)).toEqual(before.map(document => document.id))
    await page.goto('/')
    await expect(page.getByRole('button', { name: 'Новый чат', exact: true })).toBeEnabled()
  })
}

test('Security: declared MIME mismatch is rejected; unknown MIME remains supported', async ({ request }) => {
  const buffer = await readFile(path.join(fixtures, 'sample.pdf'))
  const invalid = await request.post('/api/v1/documents', { multipart: { file: { name: 'sample.pdf', mimeType: 'text/html', buffer } } })
  expect(invalid.status()).toBe(415)
  const valid = await request.post('/api/v1/documents', { multipart: { file: { name: 'sample.pdf', mimeType: 'application/octet-stream', buffer } } })
  expect(valid.status(), await valid.text()).toBe(202)
  const document = await valid.json()
  try {
    await expect.poll(async () => (await (await request.get(`/api/v1/documents/${document.id}`)).json()).status,
      { timeout: 150_000 }).toBe('ready')
    const original = await request.get(`/api/v1/documents/${document.id}/file`, { headers: { Range: 'bytes=0-4' } })
    expect(original.status()).toBe(206)
    expect(await original.text()).toBe('%PDF-')
    expect(original.headers()['x-content-type-options']).toBe('nosniff')
  } finally {
    expect((await request.delete(`/api/v1/documents/${document.id}`)).status()).toBe(204)
  }
})
