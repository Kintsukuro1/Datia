import { expect, test } from '@playwright/test';

test('login demo y entrada al chat', async ({ page }) => {
  await page.goto('/#/login');
  await expect(page.locator('#login-username-input')).toBeVisible();

  await page.locator('#login-username-input').fill('admin');
  await page.locator('#login-password-input').fill('admin123');
  await page.getByRole('button', { name: 'Ingresar' }).click();

  await expect(page).toHaveURL(/#\/chat/, { timeout: 20_000 });
  await expect(page.locator('body')).not.toContainText('Algo falló en la interfaz');
});
