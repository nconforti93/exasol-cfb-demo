import { expect, test } from '@playwright/test'

test('the built app loads and sets its theme before the first paint', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'CFB playoff odds' })).toBeVisible()
  const theme = await page.evaluate(() => document.documentElement.getAttribute('data-theme'))
  expect(['light', 'dark']).toContain(theme)
})
