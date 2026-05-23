const { test, expect } = require("../fixtures/enterprise-test");
const { openApp, switchView } = require("../utils/pages");

test.describe("performance and UX guardrails", () => {
  test("dashboard becomes usable within enterprise response budget", async ({ enterprisePage }) => {
    const started = Date.now();
    await openApp(enterprisePage);
    await expect(enterprisePage.locator("#dashboardView")).toBeVisible();
    expect(Date.now() - started).toBeLessThan(Number(process.env.E2E_DASHBOARD_BUDGET_MS || 8000));
  });

  test("view navigation does not flood backend APIs", async ({ enterprisePage }) => {
    const apiCalls = [];
    enterprisePage.on("request", (request) => {
      if (request.url().startsWith(process.env.API_BASE || "http://127.0.0.1:8000")) {
        apiCalls.push(request.url());
      }
    });

    await openApp(enterprisePage);
    for (const view of ["candidates", "jobs", "matches", "shortlist", "enterprise", "security", "dashboard"]) {
      await switchView(enterprisePage, view);
    }

    expect(apiCalls.length).toBeLessThan(Number(process.env.E2E_API_FLOOD_LIMIT || 90));
  });

  test("layout shift remains controlled on dashboard", async ({ enterprisePage }) => {
    await enterprisePage.addInitScript(() => {
      window.__qaLayoutShift = 0;
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (!entry.hadRecentInput) window.__qaLayoutShift += entry.value;
        }
      }).observe({ type: "layout-shift", buffered: true });
    });

    await openApp(enterprisePage);
    await enterprisePage.waitForTimeout(1000);
    const cls = await enterprisePage.evaluate(() => window.__qaLayoutShift || 0);
    expect(cls).toBeLessThan(Number(process.env.E2E_MAX_CLS || 0.2));
  });

  test("repeated enterprise view changes do not leak DOM nodes aggressively", async ({ enterprisePage }) => {
    await openApp(enterprisePage);
    const before = await enterprisePage.evaluate(() => document.querySelectorAll("*").length);
    for (let i = 0; i < 3; i += 1) {
      await switchView(enterprisePage, "enterprise");
      await switchView(enterprisePage, "security");
      await switchView(enterprisePage, "dashboard");
    }
    const after = await enterprisePage.evaluate(() => document.querySelectorAll("*").length);
    expect(after).toBeLessThan(before + Number(process.env.E2E_DOM_GROWTH_LIMIT || 900));
  });
});
