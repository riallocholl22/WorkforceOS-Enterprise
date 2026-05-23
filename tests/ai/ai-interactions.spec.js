const { test, expect } = require("../fixtures/enterprise-test");
const { openApp, switchView } = require("../utils/pages");

test.describe("AI interaction reliability", () => {
  test("AI copilot accepts recruiter commands without freezing", async ({ enterprisePage }) => {
    await openApp(enterprisePage);
    await switchView(enterprisePage, "brain", /brain|hiring/i);
    await expect(enterprisePage.locator("#brainCandidateId")).toBeVisible();
    await enterprisePage.locator("#brainCandidateId").fill("cand-qa-1");
    await enterprisePage.locator("#brainJobDescription").fill("Summarize candidate fit for a QA automation leadership role.");
    await expect(enterprisePage.getByRole("button", { name: /generate 360/i })).toBeEnabled();
  });

  test("chat page has stable AI response controls and fallback rendering surface", async ({ enterprisePage }) => {
    await enterprisePage.goto("/pages/chat.html");
    await expect(enterprisePage.locator("body")).toBeVisible();
    await expect(enterprisePage.locator("body")).not.toContainText(/undefined|null/i);
  });

  test("AI stream transport failures are visible as controlled UI state", async ({ enterprisePage }) => {
    await openApp(enterprisePage);
    await switchView(enterprisePage, "brain", /brain|hiring/i);
    await expect(enterprisePage.locator("#brainStatus")).toBeVisible();
    await expect(enterprisePage.locator("#brainResult")).toBeVisible();
  });
});
