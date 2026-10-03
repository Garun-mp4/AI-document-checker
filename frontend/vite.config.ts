import { defineConfig, loadEnv, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const buildInfo = {
    build_id: env.APP_BUILD_ID || 'unverified',
    commit: env.APP_BUILD_COMMIT || 'unknown',
    built_at: env.APP_BUILD_TIME || 'unknown',
  }
  const buildInfoPlugin: Plugin = {
    name: 'application-build-info',
    configureServer(server) {
      server.middlewares.use('/build-info.json', (_request, response) => {
        response.statusCode = 200
        response.setHeader('Content-Type', 'application/json; charset=utf-8')
        response.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate')
        response.end(JSON.stringify({ ...buildInfo, assets: [] }))
      })
    },
    generateBundle(_options, bundle) {
      const assets = Object.keys(bundle).filter(name => name.startsWith('assets/')).sort()
      this.emitFile({
        type: 'asset',
        fileName: 'build-info.json',
        source: JSON.stringify({ ...buildInfo, assets }),
      })
    },
  }

  return {
    define: {
      __APP_BUILD_INFO__: JSON.stringify(buildInfo),
    },
    plugins: [react(), buildInfoPlugin],
    build: {
      manifest: 'document-checker-manifest.json',
    },
    server: {
      proxy: {
        '/api': 'http://localhost:8000',
      },
    },
  }
})
