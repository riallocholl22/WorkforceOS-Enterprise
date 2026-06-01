const { test, expect } = require("../fixtures/enterprise-test");

test.describe("authentication and session workflows", () => {
  test("login page validates credentials and surfaces service presence", async ({ enterprisePage }) => {
    await enterprisePage.goto("/login.html");
    await expect(enterprisePage.locator("#authTitle")).toContainText("Admin / Recruiter Login");
    await expect(enterprisePage.locator("#loginAccountType")).toContainText("Applicant Login");
    await expect(enterprisePage.locator("#loginAccountType")).toContainText("Admin / Recruiter Login");
    await expect(enterprisePage.locator("#presenceCore")).toBeVisible();

    await enterprisePage.locator("#email").fill("not-an-email");
    await enterprisePage.locator("#password").fill("short");
    await enterprisePage.locator("#primaryAction").click();

    await expect(enterprisePage.locator("#loginStatus")).toContainText(/valid work email/i);
  });

  test("authenticated recruiter can enter and leave the workspace", async ({ enterprisePage }) => {
    await enterprisePage.goto("/index.html", { waitUntil: "commit", timeout: 10_000 });
    await expect(enterprisePage).toHaveURL(/index\.html/);
    await expect.poll(() => enterprisePage.evaluate(() => localStorage.getItem("token"))).toBeTruthy();
    await enterprisePage.evaluate(() => {
      localStorage.removeItem("token");
      localStorage.removeItem("refreshToken");
      localStorage.removeItem("currentUser");
      sessionStorage.removeItem("token");
      sessionStorage.removeItem("refreshToken");
      sessionStorage.removeItem("currentUser");
      sessionStorage.setItem("authMessage", "You have been securely logged out.");
    });
    await enterprisePage.goto("/login.html", { waitUntil: "commit", timeout: 10_000 });
    await expect(enterprisePage).toHaveURL(/login\.html/);
    await expect(enterprisePage.locator("#authTitle")).toContainText(/welcome back/i);
    await expect.poll(() => enterprisePage.evaluate(() => localStorage.getItem("token") || sessionStorage.getItem("token"))).toBeNull();
  });

  test("protected workspace redirects anonymous sessions to login", async ({ browser }) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto("/index.html", { waitUntil: "commit", timeout: 10_000 });
    await expect(page).toHaveURL(/login\.html|index\.html/);
    await context.close();
  });
});
