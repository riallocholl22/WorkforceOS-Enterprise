const { test, expect } = require("../fixtures/enterprise-test");

test.use({ storageState: "tests/.auth/recruiter.json" });

test("authenticated recruiter storage state is available", async ({ page }) => {
  try {
    await page.goto("/index.html", { waitUntil: "domcontentloaded" });
    await expect(page.locator("#pageTitle")).toBeVisible();
  } finally {
    // Close the app document explicitly so long-lived operational streams/timers
    // cannot make browser-context teardown look like an application failure.
    await page.goto("about:blank").catch(() => {});
    await page.close({ runBeforeUnload: false }).catch(() => {});
  }
});
