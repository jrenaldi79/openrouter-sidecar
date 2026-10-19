import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  // *-screenshots specs capture PNGs for manual review; they are not checks.
  testIgnore: process.env.CI ? ['**/*-screenshots.spec.js'] : [],
  fullyParallel: true,
  timeout: 10000,
  expect: { timeout: 5000 },
  use: {
    browserName: 'chromium',
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
      : {},
  },
});
