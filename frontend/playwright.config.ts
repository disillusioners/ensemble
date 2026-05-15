import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false, // Sequential for data dependencies
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: 'html',
  timeout: 60000, // Extended timeout for full stack E2E tests
  use: {
    baseURL: 'http://localhost:4199',
    trace: 'on-first-retry',
    actionTimeout: 15000,
  },
  // Backend must start BEFORE frontend (proxy requires backend running first)
  webServer: [
    {
      command: 'cd .. && bash dev.sh',
      port: 8079,
      reuseExistingServer: true,
      timeout: 60000,
      stdout: 'pipe', // Capture for logging if needed
      stderr: 'pipe',
      env: {
        // Pass through required env vars
        OPENAI_API_KEY: process.env.OPENAI_API_KEY || '',
        LOG_LEVEL: 'info',
      },
    },
    {
      command: 'ng serve --port 4199',
      port: 4199,
      reuseExistingServer: true,
      timeout: 120000,
    },
  ],
  // Use webServer arrays for proper teardown
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
