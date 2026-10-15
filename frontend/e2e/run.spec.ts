import { test, expect } from '@playwright/test'

test('starts a baseline run and shows state checks and trace', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: /start run/i }).click()
  await expect(page.getByRole('region', { name: 'Scorecard' }).locator('.status')).toHaveText('PASS', { timeout: 30000 })
  await expect(page.locator('.result-meta')).toContainText('normal')
  await expect(page.getByText('expected payment refunded once')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Trace' }).getByText('state changed')).toBeVisible()
})
