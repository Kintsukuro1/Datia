import { expect, test, type Page } from '@playwright/test';

/**
 * E2E de Proyecciones.
 *
 * Contrato de lectura del escenario: la UI separa el supuesto ("si crece 10%
 * anual") del pronóstico estadístico ("pronóstico base"), y no expone jerga de
 * backtest en las tarjetas visibles.
 */

async function loginComoAdmin(page: Page) {
  await page.goto('/#/login');
  await page.locator('#login-username-input').fill('admin');
  await page.locator('#login-password-input').fill('admin123');
  await page.getByRole('button', { name: 'Ingresar' }).click();
  await expect(page).toHaveURL(/#\/chat/, { timeout: 20_000 });
}

async function abrirProyecciones(page: Page) {
  await page.getByRole('button', { name: 'Proyecciones' }).click();
  await expect(page).toHaveURL(/#\/projections/, { timeout: 10_000 });

  // La página resuelve la fuente activa después de montar y su efecto sobre
  // `activeConnectionId` aborta una corrida lanzada antes (ProjectionsPage.tsx:94).
  // Sin esta espera el test pelea con una carrera de la UI, no con el contrato.
  await expect(page.locator('#projections-connection-select')).toHaveValue(/\d+/, {
    timeout: 20_000,
  });
}

async function proyectar(page: Page, pregunta: string) {
  await page.getByLabel('Pregunta de proyección').fill(pregunta);
  const proyectar = page.getByRole('button', { name: 'Proyectar' });
  await expect(proyectar).toBeEnabled();
  await proyectar.click();
}

/**
 * La pregunta manda: "un año" tiene que publicar 12 meses, sin selectores de
 * horizonte, granularidad ni ajustes avanzados.
 */
test('proyecciones: la pregunta fija el horizonte', async ({ page }) => {  test.setTimeout(300_000);

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
    timeout: 200_000,
  });
  await page.getByRole('button', { name: 'Ver detalle técnico' }).click();

  // El backend publica 12 periodos mensuales y declara el origen.
  await expect(page.getByText('Horizonte publicado: 12 meses')).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText('según tu pregunta')).toBeVisible();
});

/**
 * Los ejemplos de la pantalla son botones: al hacer clic cargan la pregunta en
 * el campo, sin lanzarla. El usuario la revisa y decide cuándo proyectar.
 */
test('proyecciones: un ejemplo se carga en el campo y no corre solo', async ({ page }) => {
  test.setTimeout(60_000);

  await loginComoAdmin(page);
  await abrirProyecciones(page);

  const campo = page.getByLabel('Pregunta de proyección');
  const ejemplo = 'Proyecta un crecimiento del 10% anual a 5 años';
  await page.getByRole('button', { name: ejemplo }).click();

  await expect(campo).toHaveValue(ejemplo);
  await expect(campo).toBeFocused();
  await expect(page.getByText('Todavía no hay ninguna proyección')).toBeVisible();
});

/**
 * El escenario es un supuesto, no un pronóstico: la pantalla lo publica como
 * comparación entre la base y el escenario, con la diferencia a la vista.
 */
test('proyecciones: el escenario publica la lectura de supuesto acordada', async ({ page }) => {
  test.setTimeout(300_000);

  await loginComoAdmin(page);
  await abrirProyecciones(page);
  await proyectar(page, 'Proyectame una ganancia con el 10% de las bases en 10 años');

  // La corrida resuelve la pregunta y calcula el escenario: los rótulos llegan
  // con el resultado, no con el clic. El LLM local puede tardar.
  const rotulos = [
    'Diagnóstico de la serie',
    'Comparación de escenarios',
    'Pronóstico base',
    'Tu supuesto (10%)',
    'Diferencia vs. base',
    /El escenario es un supuesto, no un pronóstico/,
  ];
  for (const rotulo of rotulos) {
    await expect(page.getByText(rotulo).first()).toBeVisible({ timeout: 200_000 });
  }
});

/**
 * La lectura del escenario no muestra jerga de evaluación estadística: si el
 * usuario tiene que entender "MAPE" o "backtest" para leer un supuesto, la
 * pantalla no cumplió. Se mira el texto renderizado (innerText), no el DOM:
 * un detalle técnico cerrado no cuenta como visible.
 */
test('proyecciones: el escenario visible no expone jerga de backtest', async ({ page }) => {
  test.setTimeout(300_000);

  await loginComoAdmin(page);
  await abrirProyecciones(page);
  await proyectar(page, 'Proyectame una ganancia con el 10% de las bases en 10 años');

  await expect(
    page.getByText(/El escenario es un supuesto, no un pronóstico/).first()
  ).toBeVisible({ timeout: 200_000 });

  const textoVisible = await page.evaluate(() => document.body.innerText);
  expect(textoVisible).not.toContain('MAPE');
  expect(textoVisible).not.toMatch(/backtest/i);
  expect(textoVisible).not.toContain('naive');
});

/**
 * "¿Cuál es el producto con más ventas proyectadas?" tiene que desglosar por la
 * dimensión de producto. Si los datos demo no tienen una dimensión con historia
 * suficiente, el backend no publica desglose y el test se salta con el motivo;
 * un desglose publicado y no mostrado sí es un defecto.
 */
test('proyecciones: la pregunta por el producto desglosa el ranking', async ({ page }) => {
  test.setTimeout(420_000);

  await loginComoAdmin(page);
  await abrirProyecciones(page);

  // Se intercepta la respuesta para distinguir "no hay dimensión" de "la UI no
  // muestra el ranking que el backend sí publicó".
  const respuestaRun = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      /\/ml\/projections\/run$/.test(response.url()),
    { timeout: 300_000 }
  );
  await proyectar(page, '¿cuál es el producto con más ventas proyectadas?');

  const respuesta = await respuestaRun;
  expect(respuesta.ok(), `La corrida falló con HTTP ${respuesta.status()}`).toBe(true);
  const resultado = await respuesta.json();

  const desglose = resultado.breakdown;
  if (!desglose?.available || desglose.groups.length === 0) {
    test.skip(
      true,
      `Los datos demo no producen desglose: ${
        desglose?.reason ?? 'el backend no publicó grupos'
      }. El test corre cuando la conexión activa tenga una dimensión con historia.`
    );
  }

  await expect(
    page.getByText(/Producto con mayor proyección|Ranking proyectado/).first()
  ).toBeVisible({ timeout: 60_000 });
});
