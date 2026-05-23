const { expect } = require("../fixtures/enterprise-test");

async function openApp(page) {
  await page.goto("/index.html");
  await expect(page.locator(".app-shell")).toBeVisible();
  await expect(page.locator("#pageTitle")).toBeVisible();
}

async function switchView(page, view, titlePattern) {
  await page.locator(`[data-view="${view}"]`).click();
  await expect(page.locator(`#${view}View, #${view === "matches" ? "matchesView" : view + "View"}`)).toBeVisible();
  if (titlePattern) {
    await expect(page.locator("#pageTitle")).toContainText(titlePattern);
  }
}

async function waitForSettledUi(page) {
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(250);
}

module.exports = {
  openApp,
  switchView,
  waitForSettledUi
};
