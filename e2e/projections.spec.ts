import { expect, test } from '@playwright/test';

/**
 * La pregunta manda: "un año" tiene que publicar 12 meses, sin selectores de
 * horizonte, granularidad ni ajustes avanzados.
 */
test('proyecciones: la pregunta fija el horizonte', async ({ page }) => {  test.setTimeout(150_000);

  await page.goto('/#/login');
  await page.locator('#login-username-input').fill('admin');
  await page.locator('#login-password-input').fill('admin123');
  await page.getByRole('button', { name: 'Ingresar' }).click();
  await expect(page).toHaveURL(/#\/chat/, { timeout: 20_000 });

  await page.getByRole('button', { name: 'Proyecciones' }).click();
  await expect(page).toHaveURL(/#\/projections/, { timeout: 10_000 });

  // Los controles que el feedback pidió sacar ya no existen.
  await expect(page.locator('#projections-horizon-select')).toHaveCount(0);
  await expect(page.locator('#projections-granularity-select')).toHaveCount(0);
  await expect(page.locator('#projections-advanced-toggle')).toHaveCount(0);

  await page.getByLabel('Pregunta de proyección').fill('proyecta las ventas del próximo año');
  const proyectar = page.getByRole('button', { name: 'Proyectar' });
  await expect(proyectar).toBeEnabled();
  await proyectar.click();

  // El encabezado del horizonte vive en el detalle técnico, que nace cerrado.
  await expect(page.getByRole('button', { name: 'Ver detalle técnico' })).toBeVisible({
    timeout: 90_000,
  });
  await page.getByRole('button', { name: 'Ver detalle técnico' }).click();

  // El backend publica 12 periodos mensuales y declara el origen.
  await expect(page.getByText('Horizonte publicado: 12 meses')).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText('según tu pregunta')).toBeVisible();
});

test('proyecciones: escenario de crecimiento del 10% a 10 años', async ({ page }) => {
  test.setTimeout(180_000);

  await page.goto('/#/login');
  await page.locator('#login-username-input').fill('admin');
  await page.locator('#login-password-input').fill('admin123');
  await page.getByRole('button', { name: 'Ingresar' }).click();
  await expect(page).toHaveURL(/#\/chat/, { timeout: 20_000 });

  await page.getByRole('button', { name: 'Proyecciones' }).click();
  await expect(page).toHaveURL(/#\/projections/, { timeout: 10_000 });

  await page.getByLabel('Pregunta de proyección').fill(
    'Proyectame una ganancia con el 10% de las bases en 10 años'
  );
  const proyectar = page.getByRole('button', { name: 'Proyectar' });
  await expect(proyectar).toBeEnabled();
  await proyectar.click();

  // Es un escenario, no un pronóstico: sin semáforo de calidad ni backtest.
  await expect(page.getByText('Escenario de crecimiento').first()).toBeVisible({
    timeout: 120_000,
  });
  await expect(page.getByText('10% anual')).toBeVisible();
  await expect(page.getByText('Ganancia vs. base')).toBeVisible();
  await expect(page.getByText(/durante 120 meses/)).toBeVisible();
  await expect(page.getByText('Escenario determinista').first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Ver detalle técnico' })).toHaveCount(0);
});
