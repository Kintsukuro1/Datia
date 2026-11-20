import { expect, test } from '@playwright/test';

/** Elige la conexión demo en un <select> de BI, buscando la opción por texto.
 *
 * El valor no se hardcodea: el id de la demo depende de cuántas bases se han
 * dado de alta antes en la instalación, y "Demo" en el nombre es lo único
 * estable entre un bootstrap fresco y una máquina con datos propios. */
async function selectDemoBase(select: import('@playwright/test').Locator) {
  const demo = select.locator('option', { hasText: 'Demo' }).first();
  // 20s: la página BI es un chunk lazy y el primer test de la corrida paga la
  // compilación de Vite antes de que la lista de bases llegue a existir.
  await expect(demo).toBeAttached({ timeout: 20_000 });
  await select.selectOption((await demo.getAttribute('value')) ?? '');
}

async function loginAsAdmin(page: import('@playwright/test').Page) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/#/login');
  await page.locator('#login-username-input').fill('admin');
  await page.locator('#login-password-input').fill('admin123');
  await page.getByRole('button', { name: 'Ingresar' }).click();
  await expect(page).toHaveURL(/#\/chat/, { timeout: 20_000 });
}

test('informe BI: pestañas, filtros, cross-filtering y datos demo', async ({ page }) => {
  await loginAsAdmin(page);

  await page.getByRole('button', { name: 'Power BI' }).first().click();
  await expect(page).toHaveURL(/#\/powerbi/, { timeout: 20_000 });

  // El informe abre por defecto sobre la fuente activa, que solo es la demo en
  // un bootstrap fresco (una máquina con datos propios abre sobre ellos). El
  // cross-filtering exige una tabla cuyo grupo Y categoría se resuelvan: en la
  // demo, `fact_ventas` no tiene categoría (`estado` se siembra con un único
  // valor) pero `fact_incidentes_ti` sí (`tipo_falla` como grupo,
  // `nivel_prioridad` como categoría). Se fija la demo para que el contrato
  // corra igual en CI que en una máquina con datos propios.
  await selectDemoBase(page.getByLabel('Base de datos'));
  await page.getByLabel('Tabla').selectOption('fact_incidentes_ti');

  await expect(page.getByRole('tab', { name: 'Resumen general' })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText('Registros del período')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('tab', { name: 'Detalle diario' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Distribución' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Datos' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Guía de lectura' })).toBeVisible();

  // Cross-filtering: clic en el rótulo de una fila del cruce filtra el informe
  // completo (queda en la URL); clic de nuevo lo quita. Es el mismo estado que
  // usa el selector del encabezado, así que también funciona por teclado.
  const crossFilterButton = page
    .getByRole('button', { name: /\(clic para filtrar\)/ })
    .first();
  await expect(crossFilterButton).toBeVisible({ timeout: 30_000 });
  await crossFilterButton.click();
  await expect(page).toHaveURL(/gv=/, { timeout: 10_000 });

  // Al filtrar, el cruce colapsa a la fila activa y su rótulo pasa a ofrecer
  // "quitar": es el botón que hay que clickear para limpiar.
  const activeFilterButton = page
    .getByRole('button', { name: /\(filtro activo; clic para quitar\)/ })
    .first();
  await expect(activeFilterButton).toBeVisible({ timeout: 10_000 });
  await activeFilterButton.click();
  await expect(page).not.toHaveURL(/gv=/, { timeout: 10_000 });

  await page.getByRole('tab', { name: 'Detalle diario' }).click();
  await expect(page.getByText(/diarios por/)).toBeVisible();

  await page.getByRole('tab', { name: 'Distribución' }).click();
  await expect(page.getByText(/Distribución por/)).toBeVisible();

  await page.getByRole('tab', { name: 'Datos' }).click();
  await expect(page.getByText(/Primeras \d+ filas/)).toBeVisible();

  await page.getByRole('tab', { name: 'Guía de lectura' }).click();
  await expect(page.getByText('Conceptos clave')).toBeVisible();
});

test('paneles BI: crear, editar, reordenar y borrar visuales', async ({ page }) => {
  await loginAsAdmin(page);

  await page.getByRole('button', { name: 'Paneles' }).first().click();
  await expect(page).toHaveURL(/#\/panels/, { timeout: 20_000 });

  const pickDemoBase = async () => {
    await selectDemoBase(page.locator('#bi-conn'));
    await expect(page.locator('#bi-table option[value="fact_ventas"]')).toHaveCount(1, { timeout: 30_000 });
    await page.locator('#bi-table').selectOption('fact_ventas');
  };

  // Primer visual: ancho (⅔) y descarga CSV de lo que se ve.
  await page.getByRole('button', { name: 'Nuevo visual' }).click();
  await pickDemoBase();
  await page.locator('#bi-dim').selectOption('metodo_pago');
  await page.locator('#bi-measure').selectOption('monto_total');
  await page.getByRole('button', { name: '⅔ Ancho' }).click();
  await page.locator('#bi-title').fill('Ventas por método de pago');
  await page.getByRole('button', { name: 'Guardar' }).click();
  await expect(page.getByRole('heading', { name: 'Ventas por método de pago' })).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('[role="listitem"]').first()).toHaveClass(/md:col-span-2/);

  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Descargar CSV' }).first().click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.csv$/);

  // Segundo visual.
  await page.getByRole('button', { name: 'Nuevo visual' }).click();
  await page.locator('#bi-dim').selectOption('estado');
  await page.locator('#bi-title').fill('Ventas por estado');
  await page.getByRole('button', { name: 'Guardar' }).click();
  await expect(page.getByRole('heading', { name: 'Ventas por estado' })).toBeVisible({ timeout: 30_000 });

  const headings = page.locator('h3');
  await expect
    .poll(async () => headings.allInnerTexts(), { timeout: 10_000 })
    .toEqual(['Ventas por método de pago', 'Ventas por estado']);

  // Reordenar: el primero baja con el botón accesible (el arrastre es HTML5).
  await page.getByRole('button', { name: 'Mover abajo en el panel' }).first().click();
  await expect
    .poll(async () => headings.allInnerTexts(), { timeout: 10_000 })
    .toEqual(['Ventas por estado', 'Ventas por método de pago']);

  // Editar el que quedó primero: mismo constructor, otro título.
  await page.getByRole('button', { name: 'Editar visual' }).first().click();
  await expect(page.getByText(/Editar visual · Ventas por estado/)).toBeVisible();
  await page.locator('#bi-title').fill('Ventas editadas');
  await page.getByRole('button', { name: 'Guardar cambios' }).click();
  await expect(page.getByRole('heading', { name: 'Ventas editadas' })).toBeVisible({ timeout: 30_000 });

  // Limpieza: quitar los dos visuales creados.
  page.on('dialog', (dialog) => void dialog.accept());
  await page.getByRole('button', { name: 'Quitar del panel' }).first().click();
  await expect(page.getByRole('heading', { name: 'Ventas editadas' })).toBeHidden({ timeout: 20_000 });
  await page.getByRole('button', { name: 'Quitar del panel' }).first().click();
  await expect(page.getByRole('heading', { name: 'Ventas por método de pago' })).toBeHidden({ timeout: 20_000 });
});
