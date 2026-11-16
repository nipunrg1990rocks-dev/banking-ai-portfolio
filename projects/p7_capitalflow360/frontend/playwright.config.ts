import { defineConfig, devices } from "@playwright/test";

// Browser projects: chromium always; firefox and webkit when PW_ALL_BROWSERS=1 (CI installs them).
// The API and the built UI are served together by uvicorn (CF360_STATIC_DIR) against the e2e database.
const PORT = Number(process.env.E2E_PORT || 8010);
const all = process.env.PW_ALL_BROWSERS === "1";
const mobileOnly = /@mobile/;

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: Number(process.env.PW_WORKERS || 2),
  retries: 0,
  reporter: [["list"], ["json", { outputFile: process.env.PW_JSON || "../testkit/out/playwright.json" }]],
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: "off", screenshot: "only-on-failure" },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "chromium-mobile", use: { ...devices["Pixel 7"] }, grep: mobileOnly },
    ...(all ? [
      { name: "firefox", use: { ...devices["Desktop Firefox"] } },
      { name: "webkit", use: { ...devices["Desktop Safari"] } },
      { name: "webkit-mobile", use: { ...devices["iPhone 14"] }, grep: mobileOnly },
    ] : []),
  ],
  webServer: {
    command: `python -m uvicorn cf360.main:app --port ${PORT} --app-dir ../backend`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: true,
    timeout: 60_000,
    env: {
      CF360_DATABASE_URL: process.env.CF360_E2E_DATABASE_URL || "postgresql+psycopg://cf360:cf360@localhost:5432/cf360_test",
      CF360_STATIC_DIR: "../frontend/dist",
      CF360_TEST_SUPPORT: "true",
      CF360_JWT_SECRET: process.env.CF360_JWT_SECRET || "e2e-only-secret-at-least-32-bytes-long!!",
      CF360_DEMO_PASSWORD: process.env.CF360_DEMO_PASSWORD || "Demo!2026",
      CF360_DEFECTS: process.env.CF360_DEFECTS || "",
    },
  },
});
