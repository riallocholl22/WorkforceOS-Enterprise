const API_BASE = process.env.API_BASE || "http://127.0.0.1:8000";
const mockApi = process.env.E2E_MOCK_API === "1" || process.env.E2E_MOCK_API === "true";

const json = (body) => ({
  status: 200,
  contentType: "application/json",
  body: JSON.stringify({ success: true, data: body, error: null })
});

async function installEnterpriseApiStabilizers(page) {
  await page.addInitScript((apiBase) => {
    window.API_BASE = apiBase;
    window.__QA_BROWSER_AUDIT__ = true;
  }, API_BASE);

  if (!mockApi) return;

  await page.route(`${API_BASE}/enterprise/workspace`, (route) => route.fulfill(json({
    organization: { name: "Playwright Enterprise QA", plan: "enterprise" },
    membership: { role: "super_admin" }
  })));
  await page.route(`${API_BASE}/enterprise/notifications`, (route) => route.fulfill(json({ unread_count: 2, items: [] })));
  await page.route(`${API_BASE}/enterprise/analytics/overview`, (route) => route.fulfill(json({
    funnel: { applied: 24, screened: 18, shortlisted: 9, hired: 2 },
    skill_demand: [{ skill: "Python", demand: 18 }, { skill: "AI", demand: 14 }]
  })));
  await page.route(`${API_BASE}/enterprise/billing**`, (route) => route.fulfill(json({
    plan: "enterprise",
    status: "active",
    seats: 25,
    invoices: [{ id: "INV-QA-1", amount: 1200, status: "paid" }]
  })));
  await page.route(`${API_BASE}/enterprise/security/**`, (route) => route.fulfill(json({
    score: 96,
    posture: "strong",
    incidents: [],
    controls: [{ name: "CSP", status: "enabled" }]
  })));
  await page.route(`${API_BASE}/enterprise/ops/**`, (route) => route.fulfill(json({
    recommendations: [{ title: "Review shortlist", severity: "medium" }],
    feed: []
  })));
  await page.route(`${API_BASE}/candidates**`, (route) => route.fulfill(json([
    { id: "cand-qa-1", name: "Avery QA", skills: ["Python", "Recruiting"], score: 91 }
  ])));
  await page.route(`${API_BASE}/jobs**`, (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill(json({ id: "job-qa-1", title: "QA Automation Architect" }));
    }
    return route.fulfill(json([{ id: "job-qa-1", title: "QA Automation Architect", description: "Playwright and AI systems" }]));
  });
  await page.route(`${API_BASE}/ranking**`, (route) => route.fulfill(json([
    { candidate_id: "cand-qa-1", name: "Avery QA", match_score: 91, skills: ["Python", "Playwright"] }
  ])));
  await page.route(`${API_BASE}/shortlist**`, (route) => route.fulfill(json({
    entries: [{ candidate_id: "cand-qa-1", status: "pending", score: 91 }],
    total: 1
  })));
  await page.route(`${API_BASE}/ai/chat**`, (route) => route.fulfill(json({
    response: "I found one strong candidate and no blocking compliance issues.",
    session_id: "qa-chat-session"
  })));
}

module.exports = {
  installEnterpriseApiStabilizers
};
