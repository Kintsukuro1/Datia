import { defineConfig, devices } from '@playwright/test';

// Config separada de `playwright.config.ts` para no cambiar el flujo manual de
// la skill webapp-testing. Esta sí levanta el stack y corre el smoke de CI.
export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.ts',
  timeout: 60_000,
  use: {
    baseURL: 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      // `run-backend.js` resuelve el Python del venv o del sistema, igual que
      // en desarrollo. Se separa de Vite para que un fallo del backend no
      // tumbe el frontend ni viceversa.
      command: 'node scripts/run-backend.js',
      url: 'http://127.0.0.1:8000/health',
      reuseExistingServer: true,
      timeout: 180_000,
      env: {
        ...process.env,
        ENVIRONMENT: 'development',
        DEMO_MODE: 'true',
      },
    },
    {
      command: 'npm run dev:frontend',
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: true,
      timeout: 120_000,
      env: { ...process.env },
    },
  ],
});
