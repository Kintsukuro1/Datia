import { expect, test } from '@playwright/test';

/**
 * Reproducción del punto 10 del feedback: "los botones no funcionan" en
 * Firefox y Edge. Cubre los controles de la barra lateral y del Header, que
 * son los que aparecen en la captura del documento.
 */

test('login: el selector de perfiles de prueba rellena el formulario', async ({ page }) => {
  await page.goto('/#/login');
  await page.selectOption('#login-demo-perfil', 'director');
  await expect(page.locator('#login-username-input')).toHaveValue('director');
  await expect(page.locator('#login-password-input')).toHaveValue('director123');
  await page.getByRole('button', { name: 'Ingresar' }).click();
  await expect(page).toHaveURL(/#\/chat/, { timeout: 20_000 });
});

test('admin: los controles del chat responden', async ({ page }) => {
  const pageErrors: string[] = [];
  page.on('pageerror', (e) => pageErrors.push(e.message));

  await page.goto('/#/login');
  await page.locator('#login-username-input').fill('admin');
  await page.locator('#login-password-input').fill('admin123');
  await page.getByRole('button', { name: 'Ingresar' }).click();
  await expect(page).toHaveURL(/#\/chat/, { timeout: 20_000 });

  // El selector de fuente activa vuelve a estar en el subheader del chat: sin
  // él, cambiar de base obligaba a salir de la pantalla.
  await expect(page.locator('#chat-active-db-select')).toBeVisible({ timeout: 20_000 });

  // "Nueva consulta" limpia el campo y devuelve el foco.
  const prompt = page.getByLabel('Pregunta analítica sobre datos corporativos');
  await prompt.fill('texto que debe limpiarse');
  await page.getByRole('button', { name: /Nueva consulta/ }).click();
  await expect(prompt).toHaveValue('');

  // Buscador: acepta texto y ofrece limpiar.
  const search = page.getByLabel('Buscar en historial de consultas');
  await search.fill('zzz');
  await expect(search).toHaveValue('zzz');
  await expect(page.getByRole('button', { name: 'Limpiar búsqueda' })).toBeVisible({
    timeout: 10_000,
  });
  await page.getByRole('button', { name: 'Limpiar búsqueda' }).click();
  await expect(page.getByLabel('Buscar en historial de consultas')).toHaveValue('');

  // "Armar consulta" abre el constructor guiado.
  await page.getByRole('button', { name: 'Armar consulta' }).click();
  await expect(page.locator('#chat-smart-builder')).toBeVisible();
  await page.getByRole('button', { name: 'Cerrar el asistente de consulta' }).click();
  await expect(page.locator('#chat-smart-builder')).toHaveCount(0);

  // Ayuda: abre, se lee y se cierra.
  await page.getByRole('button', { name: 'Ayuda', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Ayuda de Dat.ia' })).toBeVisible();
  await expect(page.getByText('Pregunta con tus palabras')).toBeVisible();
  await page.getByRole('button', { name: 'Entendido' }).click();
  await expect(page.getByRole('dialog', { name: 'Ayuda de Dat.ia' })).toHaveCount(0);

  // Navegación del Header.
  await page.getByRole('button', { name: 'Proyecciones' }).click();
  await expect(page).toHaveURL(/#\/projections/, { timeout: 10_000 });
  await page.getByRole('button', { name: 'Consultar' }).click();
  await expect(page).toHaveURL(/#\/chat/, { timeout: 10_000 });
  await page.getByRole('button', { name: 'Opciones' }).click();
  await expect(page).toHaveURL(/#\/settings/, { timeout: 10_000 });

  expect(pageErrors).toEqual([]);
});

test('director: Opciones y Admin no se abren y /settings redirige', async ({ page }) => {
  await page.goto('/#/login');
  await page.locator('#login-username-input').fill('director');
  await page.locator('#login-password-input').fill('director123');
  await page.getByRole('button', { name: 'Ingresar' }).click();
  await expect(page).toHaveURL(/#\/chat/, { timeout: 20_000 });

  await expect(page.getByRole('button', { name: 'Opciones' })).toHaveCount(0);

  await page.goto('/#/settings');
  await expect(page).toHaveURL(/#\/chat/, { timeout: 10_000 });

  await page.goto('/#/admin');
  await expect(page).toHaveURL(/#\/chat/, { timeout: 10_000 });
});

test('director: Mi cuenta abre el cambio de contraseña voluntario', async ({ page }) => {
  await page.goto('/#/login');
  await page.locator('#login-username-input').fill('director');
  await page.locator('#login-password-input').fill('director123');
  await page.getByRole('button', { name: 'Ingresar' }).click();
  await expect(page).toHaveURL(/#\/chat/, { timeout: 20_000 });

  await page.getByRole('button', { name: 'Mi cuenta' }).click();
  await page.getByRole('menuitem', { name: 'Cambiar contraseña' }).click();

  await expect(page.getByRole('dialog', { name: 'Cambio de contraseña' })).toBeVisible();
  await page.getByRole('button', { name: 'Cancelar' }).click();
  await expect(page.getByRole('dialog', { name: 'Cambio de contraseña' })).toHaveCount(0);
});

test('admin: la página de administración tiene un solo alta de fuente', async ({ page }) => {
  await page.goto('/#/login');
  await page.locator('#login-username-input').fill('admin');
  await page.locator('#login-password-input').fill('admin123');
  await page.getByRole('button', { name: 'Ingresar' }).click();
  await expect(page).toHaveURL(/#\/chat/, { timeout: 20_000 });

  await page.goto('/#/admin');
  await expect(page.getByText('Fuentes de Datos Corporativas', { exact: true })).toBeVisible({ timeout: 20_000 });

  await expect(page.getByRole('button', { name: 'Agregar fuente de datos' })).toHaveCount(1);
  await expect(page.getByRole('button', { name: /Importar Archivo/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Conectar BD Remota/ })).toHaveCount(0);
});
