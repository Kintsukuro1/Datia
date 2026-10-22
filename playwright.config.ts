import { defineConfig, devices } from '@playwright/test';

// Sin webServer a proposito: la skill webapp-testing navega contra el `npm run dev`
// que ya este levantado. `reuseExistingServer` solo aplica a un webServer propio.
export default defineConfig({
  // Solo specs de Playwright: los `*.test.ts` de src/__tests__ son de Vitest.
  testMatch: '**/*.spec.ts',
  use: {
    baseURL: 'http://127.0.0.1:5173',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
