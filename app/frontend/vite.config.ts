import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export const DEV_HOST = '127.0.0.1'
export const DEV_PORT = 5173
export const BACKEND_ORIGIN = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: DEV_HOST,
    port: DEV_PORT,
    strictPort: true,
    proxy: {
      '/api': { target: BACKEND_ORIGIN, changeOrigin: false },
    },
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
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
    restoreMocks: true,
  },
})
