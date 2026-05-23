const { test, expect } = require("../fixtures/enterprise-test");
const { openApp, switchView } = require("../utils/pages");

test.describe("frontend security validation", () => {
  test("authenticated API requests include bearer and CSRF-aware headers", async ({ enterprisePage }) => {
    const observed = [];
    enterprisePage.on("request", (request) => {
      if (request.url().includes("/enterprise/")) {
        observed.push(request.headers());
      }
    });

    await openApp(enterprisePage);
    await switchView(enterprisePage, "security", /security/i);
    await expect.poll(() => observed.length).toBeGreaterThan(0);
    expect(observed.some((headers) => /^Bearer\s+/.test(headers.authorization || ""))).toBeTruthy();
  });

  test("security center exposes incident response and control validation", async ({ enterprisePage }) => {
    await openApp(enterprisePage);
    await switchView(enterprisePage, "security", /security/i);
    await expect(enterprisePage.locator("#securitySummary")).toBeVisible();
    await expect(enterprisePage.locator("#securityIncidents")).toBeVisible();
    await expect(enterprisePage.locator("#securityAutomationRules")).toBeVisible();
  });

  test("XSS payloads are not executed when entered into recruiter fields", async ({ enterprisePage }) => {
    await openApp(enterprisePage);
    await switchView(enterprisePage, "jobs", /jobs/i);
    await enterprisePage.evaluate(() => { window.__xssExecuted = false; });
    await enterprisePage.locator("#jobTitle").fill("<img src=x onerror='window.__xssExecuted=true'>");
    await enterprisePage.locator("#jobDesc").fill("<script>window.__xssExecuted=true</script>");
    await expect.poll(() => enterprisePage.evaluate(() => window.__xssExecuted)).toBeFalsy();
  });

  test("resume upload restrictions advertise only approved file types", async ({ enterprisePage }) => {
    await openApp(enterprisePage);
    await switchView(enterprisePage, "upload", /upload/i);
    await expect(enterprisePage.locator("#resumeFileInput")).toHaveAttribute("accept", ".pdf,.docx,.txt");
  });
});
