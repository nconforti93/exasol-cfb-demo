import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

import { fixtureApi } from './preview/fixtureApi.ts'

export const DEV_HOST = '127.0.0.1'
export const DEV_PORT = 5173
export const BACKEND_ORIGIN = 'http://127.0.0.1:8000'

// `make app-preview`: CFB_PREVIEW=1 serves /api/* from the test fixtures instead of proxying to the backend.
const PREVIEW = process.env['CFB_PREVIEW'] === '1'

export default defineConfig({
  plugins: [
    react(),
    ...(PREVIEW
      ? [{ name: 'cfb-fixture-api', configureServer(server: { middlewares: { use: (fn: ReturnType<typeof fixtureApi>) => unknown } }) {
            server.middlewares.use(fixtureApi())
          },
        }]
      : []),
  ],
  server: {
    host: DEV_HOST,
    port: DEV_PORT,
    strictPort: true,
    proxy: PREVIEW ? {} : { '/api': { target: BACKEND_ORIGIN, changeOrigin: false } },
  },
  preview: {
    host: DEV_HOST,
    port: 4173,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    include: ['src/**/*.test.{ts,tsx}', 'preview/**/*.test.ts'],
    css: false,
    restoreMocks: true,
  },
})
