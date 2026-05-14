import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false, // Sequential for data dependencies
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: 'html',
  timeout: 30000,
  use: {
    baseURL: 'http://localhost:4200',
    trace: 'on-first-retry',
    actionTimeout: 10000,
  },
  webServer: [
    {
      command: 'cd ../.. && python -m daemon',
      port: 8088,
      reuseExistingServer: true,
      timeout: 30000,
    },
    {
      command: 'npm start',
      port: 4200,
      reuseExistingServer: true,
      timeout: 120000,
    },
  ],
});
