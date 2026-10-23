import { defineConfig } from '@playwright/test'

const PREVIEW_URL = 'http://127.0.0.1:4173'

export default defineConfig({
  testDir: 'e2e',
  reporter: 'list',
  use: { baseURL: PREVIEW_URL },
  webServer: {
    command: 'npm run preview',
    url: PREVIEW_URL,
    reuseExistingServer: true,
    timeout: 60_000,
  },
})
