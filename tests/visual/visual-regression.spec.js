const { test, expect } = require("../fixtures/enterprise-test");
const { openApp, switchView, waitForSettledUi } = require("../utils/pages");

test.describe("visual regression snapshots", () => {
  test("login page", async ({ enterprisePage }) => {
    await enterprisePage.goto("/login.html");
    await waitForSettledUi(enterprisePage);
    await expect(enterprisePage).toHaveScreenshot("login-page.png", { fullPage: true });
  });

  test("dashboard", async ({ enterprisePage }) => {
    await openApp(enterprisePage);
    await waitForSettledUi(enterprisePage);
    await expect(enterprisePage).toHaveScreenshot("dashboard.png", { fullPage: true });
  });

  test("AI copilot", async ({ enterprisePage }) => {
    await openApp(enterprisePage);
    await switchView(enterprisePage, "brain", /brain|hiring/i);
    await waitForSettledUi(enterprisePage);
    await expect(enterprisePage).toHaveScreenshot("ai-copilot.png", { fullPage: true });
  });

  test("shortlist", async ({ enterprisePage }) => {
    await openApp(enterprisePage);
    await switchView(enterprisePage, "shortlist", /shortlist/i);
    await waitForSettledUi(enterprisePage);
    await expect(enterprisePage).toHaveScreenshot("shortlist.png", { fullPage: true });
  });

  test("security center", async ({ enterprisePage }) => {
    await openApp(enterprisePage);
    await switchView(enterprisePage, "security", /security/i);
    await waitForSettledUi(enterprisePage);
    await expect(enterprisePage).toHaveScreenshot("security-center.png", { fullPage: true });
  });

  test("analytics and billing", async ({ enterprisePage }) => {
    await openApp(enterprisePage);
    await switchView(enterprisePage, "enterprise", /enterprise/i);
    await waitForSettledUi(enterprisePage);
    await expect(enterprisePage).toHaveScreenshot("analytics-billing.png", { fullPage: true });
  });

  test("mobile responsiveness", async ({ enterprisePage }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile-chrome", "Mobile snapshot runs in the mobile project.");
    await openApp(enterprisePage);
    await waitForSettledUi(enterprisePage);
    await expect(enterprisePage).toHaveScreenshot("mobile-dashboard.png", { fullPage: true });
  });
});
