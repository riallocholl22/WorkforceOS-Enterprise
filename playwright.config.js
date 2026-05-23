const { defineConfig, devices } = require("@playwright/test");

const FRONTEND_URL = process.env.FRONTEND_URL || "http://127.0.0.1:5500";
const API_BASE = process.env.API_BASE || "http://127.0.0.1:8000";
const isCI = Boolean(process.env.CI);

module.exports = defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  expect: {
    timeout: 10_000,
    toHaveScreenshot: {
      maxDiffPixelRatio: Number(process.env.VISUAL_MAX_DIFF_RATIO || 0.015),
      animations: "disabled"
    }
  },
  fullyParallel: true,
  forbidOnly: isCI,
  retries: isCI ? 2 : 0,
  workers: isCI ? 2 : undefined,
  reporter: [
    ["list"],
    ["html", { outputFolder: "test-results/playwright-report", open: "never" }],
    ["junit", { outputFile: "test-results/frontend-junit.xml" }]
  ],
  outputDir: "test-results/artifacts",
  globalSetup: require.resolve("./tests/setup/global-setup.js"),
  use: {
    baseURL: FRONTEND_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    storageState: "tests/.auth/recruiter.json"
  },
  webServer: [
    {
      command: "python -m uvicorn main:app --host 127.0.0.1 --port 8000",
      url: `${API_BASE}/health`,
      timeout: 120_000,
      reuseExistingServer: !isCI,
      stdout: "pipe",
      stderr: "pipe"
    },
    {
      command: "python -m http.server 5500 -d frontend",
      url: FRONTEND_URL,
      timeout: 60_000,
      reuseExistingServer: !isCI,
      stdout: "pipe",
      stderr: "pipe"
    }
  ],
  projects: [
    {
      name: "setup",
      testMatch: /.*\.setup\.js/,
      use: { storageState: undefined }
    },
    {
      name: "chromium",
      dependencies: ["setup"],
      use: { ...devices["Desktop Chrome"] }
    },
    {
      name: "firefox",
      dependencies: ["setup"],
      use: { ...devices["Desktop Firefox"] }
    },
    {
      name: "webkit",
      dependencies: ["setup"],
      use: { ...devices["Desktop Safari"] }
    },
    {
      name: "mobile-chrome",
      dependencies: ["setup"],
      use: { ...devices["Pixel 7"] }
    }
  ]
});
