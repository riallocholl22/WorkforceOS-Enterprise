const { test, expect } = require("../fixtures/enterprise-test");
const { openApp, switchView, waitForSettledUi } = require("../utils/pages");

test.describe("enterprise recruiter workflows", () => {
  test("dashboard renders core operating metrics", async ({ enterprisePage }) => {
    await openApp(enterprisePage);
    await expect(enterprisePage.locator("#dashboardView")).toBeVisible();
    await expect(enterprisePage.locator("#metricCandidates")).toBeVisible();
    await expect(enterprisePage.locator("#metricJobs")).toBeVisible();
    await expect(enterprisePage.locator("#dashboardRanking")).toBeAttached();
    await expect.poll(() => enterprisePage.locator("#dashboardRanking").innerHTML()).not.toBe("");
  });

  test("recruiter onboarding creates a job and prepares matching", async ({ enterprisePage }) => {
    await openApp(enterprisePage);
    await switchView(enterprisePage, "jobs", /jobs/i);
    await enterprisePage.locator("#jobTitle").fill("Senior Playwright QA Architect");
    await enterprisePage.locator("#jobDesc").fill("Own browser automation, release confidence, AI workflow QA, and production reliability.");
    await enterprisePage.getByRole("button", { name: /^Create Job$/ }).click();
    await expect(enterprisePage.locator("#jobStatus")).toBeVisible();
  });

  test("resume upload surface enforces enterprise upload affordances", async ({ enterprisePage }) => {
    await openApp(enterprisePage);
    await switchView(enterprisePage, "upload", /upload/i);
    await expect(enterprisePage.locator("#resumeFileInput")).toHaveAttribute("accept", /\.pdf/);
    await expect(enterprisePage.locator("#candidateIdInput")).toBeVisible();
    await expect(enterprisePage.getByRole("button", { name: /upload and process/i })).toBeEnabled();
  });

  test("shortlist workflow exposes automation and review actions", async ({ enterprisePage }) => {
    await openApp(enterprisePage);
    await switchView(enterprisePage, "shortlist", /shortlist/i);
    await expect(enterprisePage.locator("#shortlistThreshold")).toHaveValue("75");
    await expect(enterprisePage.getByRole("button", { name: /auto-shortlist/i })).toBeVisible();
    await expect(enterprisePage.getByRole("button", { name: /^export$/i })).toBeVisible();
  });

  test("interview workflow starts from a stable candidate session surface", async ({ enterprisePage }) => {
    await openApp(enterprisePage);
    await switchView(enterprisePage, "interview", /interview/i);
    await enterprisePage.locator("#interviewCandidateId").fill("cand-qa-1");
    await expect(enterprisePage.locator("#startInterviewBtn")).toBeEnabled();
    await expect(enterprisePage.locator("#interviewVideo")).toBeVisible();
    await expect(enterprisePage.locator("#interviewQuestions")).toContainText(/start interview/i);
  });

  test("analytics, billing, and security operations render without blocking the workspace", async ({ enterprisePage }) => {
    await openApp(enterprisePage);
    await switchView(enterprisePage, "enterprise", /enterprise/i);
    await waitForSettledUi(enterprisePage);
    await expect(enterprisePage.locator("#teamInviteEmail")).toBeVisible();
    await expect(enterprisePage.locator("#billingPlanSelect")).toBeVisible();

    await switchView(enterprisePage, "security", /security/i);
    await waitForSettledUi(enterprisePage);
    await expect(enterprisePage.locator("#securitySummary")).toBeVisible();
    await expect(enterprisePage.locator("#securityControls")).toBeVisible();
  });
});
