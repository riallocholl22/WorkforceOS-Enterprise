function switchView(view){
currentView = view;
const token = nextViewRequestToken();
document.querySelectorAll(".view").forEach(el => el.classList.remove("active"));
document.querySelectorAll(".nav-item").forEach(el => el.classList.remove("active"));
document.getElementById(`${view}View`)?.classList.add("active");
document.querySelector(`[data-view="${view}"]`)?.classList.add("active");

const titles = {
dashboard: "Command Center",
candidates: "Candidates",
jobs: "Jobs",
upload: "Upload Resume",
matches: "Match Results",
brain: "AI Decision Brain",
productIntelligence: "Product Intelligence",
shortlist: "Shortlist",
interview: "AI Interview",
enterprise: "Enterprise Operations Fabric",
security: "Security Center",
observability: "Observability"
};
document.getElementById("pageTitle").innerText = titles[view] || "Command Center";

refreshCurrentView(token, view).catch(err => {
hideLoader();
showAlert(err?.message || "This workspace view could not refresh. Try again in a moment.");
});
}

async function refreshCurrentView(token = nextViewRequestToken(), view = currentView){
lastLoaded[view] = 0;
if(view === "dashboard") await loadDashboard(token);
if(!isCurrentViewRequest(token, view)) return;
if(view === "candidates") await loadCandidates(token);
if(!isCurrentViewRequest(token, view)) return;
if(view === "jobs") await loadJobs(token);
if(!isCurrentViewRequest(token, view)) return;
if(view === "matches"){
// Load jobs first so Match Results can choose onboarding vs ranking reliably.
if(!jobs.length) await loadJobs().catch(() => null);
if(!isCurrentViewRequest(token, view)) return;
await runMatch(false);
}
if(!isCurrentViewRequest(token, view)) return;
if(view === "brain") await loadHiringBrain(false);
if(!isCurrentViewRequest(token, view)) return;
if(view === "productIntelligence") await loadProductIntelligence(true);
if(!isCurrentViewRequest(token, view)) return;
if(view === "shortlist") await refreshShortlist();
if(!isCurrentViewRequest(token, view)) return;
if(view === "enterprise") await loadEnterpriseOps();
if(!isCurrentViewRequest(token, view)) return;
if(view === "observability") await loadObservability();
if(!isCurrentViewRequest(token, view)) return;
if(view === "security") await loadSecurityCenter();
}

async function boot(){
try {
detectPerformanceMode();
registerPWA();
setupResumeDropZone();
setupCommandPalette();
if(!WORKFORCE_TEST_MODE){
initHorizontalRails();
initCinematicReveals();
}
initCopilotLayer();
const [me, workspaceData, notificationData] = await Promise.all([
apiGetMe(),
apiGetWorkspace().catch(() => null),
apiGetNotifications().catch(() => []),
]);
if(me?.role === "applicant"){
sessionStorage.setItem("authMessage", "This workspace is only available to recruiters and administrators.");
window.location.href = "pages/upload.html";
return;
}
workspace = workspaceData?.workspace || null;
notifications = notificationData || [];
document.getElementById("userEmail").innerText = me.email;
document.getElementById("workspaceName").innerText = workspace?.organization?.name || "Workspace";
document.getElementById("workspacePlan").innerText = (workspaceData?.subscription?.plan || "free").toUpperCase();
document.getElementById("notificationCount").innerText = `${notifications.length} alerts`;
renderFunnel(workspace);
connectOpsStream();
updateShortlistCount();
renderFeedbackPanel();
await loadDashboard();
if(!WORKFORCE_TEST_MODE){
scheduleIdleTask(() => refreshShortlist(true).catch(() => null), 2200);
}
} catch (err) {
showAlert(err.message || "Please log in again.");
renderDashboardRanking([]);
renderTopSkills([]);
renderFeedbackPanel();
} finally {
hideLoader();
}
}

function renderFunnel(workspaceData){
const funnel = workspaceData?.funnel || {};
const applied = funnel.applied ?? "--";
const screened = funnel.screened ?? "--";
const shortlisted = funnel.shortlisted ?? "--";
const hired = funnel.hired ?? "--";
const map = {
funnelApplied: applied,
funnelScreened: screened,
funnelShortlisted: shortlisted,
funnelHired: hired
};
Object.entries(map).forEach(([id, val]) => {
const node = document.getElementById(id);
if(node) node.textContent = String(val);
});
}

function renderKeyValueList(items){
return Object.entries(items || {}).map(([key, value]) => `
<div class="mini-row">
<span>${escapeHTML(key.replaceAll("_", " "))}</span>
<strong>${escapeHTML(Array.isArray(value) ? value.join(", ") : value)}</strong>
</div>
`).join("");
}

async function loadEnterpriseOps(showErrors = true){
try {
const [team, billing, schedules] = await Promise.all([
apiGetTeam().catch(() => ({members: [], invitations: []})),
apiGetBilling().catch(() => null),
apiGetSchedules().catch(() => []),
]);
enterpriseState.team = team;
enterpriseState.billing = billing;
renderTeamPanel(team);
renderBillingPanel(billing);
renderAssessmentSchedulePanel(null, schedules);
} catch (err) {
if(showErrors) showAlert(err.message || "Unable to load enterprise operations");
}
}

async function loadHiringBrain(showErrors = true){
const status = document.getElementById("brainStatus");
const result = document.getElementById("brainResult");
const candidateInput = document.getElementById("brainCandidateId");
const jobText = document.getElementById("brainJobDescription");

try {
if(!jobs.length){
try { await loadJobs(); } catch {}
}
if(!candidates.length){
try { await loadCandidates(); } catch {}
}

// Prefill from the rest of the app so Brain feels connected to the workflow.
const top = matches?.[0] || null;
const activeJob = document.getElementById("matchJobDescription")?.value.trim() || jobs?.[0]?.description || "";

if(candidateInput && !candidateInput.value.trim() && top?.candidate_id){
candidateInput.value = top.candidate_id;
}
if(jobText && !jobText.value.trim() && activeJob){
jobText.value = activeJob;
}

if(status) status.innerHTML = `<span class="status-pill status-top">AI Decision Brain is ready</span>`;
if(result){
if(!jobs.length){
result.innerHTML = renderMatchOnboardingState("Candidate intelligence activates once jobs are configured. Create a role first, then upload resumes.");
} else if(!candidates.length){
result.innerHTML = renderOnboardingCard(
"Candidate intelligence is ready for intake.",
"Upload a resume to activate Candidate 360 profiles, match intelligence, and next-step recommendations.",
`<button onclick="switchView('upload')">Upload Resume</button>`
);
} else if(!result.innerHTML.trim()){
result.innerHTML = renderOnboardingCard(
"Ready to generate Candidate 360.",
"Pick a candidate, confirm the job description, and generate a recruiter-style 360 profile with risks, readiness, and next actions."
);
}
}
loadAutomationRules().catch(() => null);
} catch (err) {
if(showErrors) showAlert(err.message || "Unable to prepare AI Decision Brain");
if(status) status.innerHTML = `<span class="status-pill status-low">Unavailable</span>`;
}
}

function _brainCard(title, bodyHtml){
return `
<section class="brain-card">
<h4>${escapeHTML(title)}</h4>
${bodyHtml || ""}
</section>
`;
}

function renderCandidate360(payload){
const container = document.getElementById("brainResult");
if(!container) return;

const status = payload?.status || "";
if(status && status !== "ok"){
container.innerHTML = `<div class="empty-state">${escapeHTML(payload?.message || "Candidate intelligence unavailable.")}</div>`;
return;
}

const candidate = payload?.candidate || {};
const match = payload?.match_intelligence || {};
const decision = payload?.decision_engine || {};
const risk = payload?.risk || {};
const readiness = payload?.readiness || {};
const next = payload?.next_actions || [];
const graph = payload?.candidate_graph || {};
const automation = payload?.automation_preview || {};

const recommendation = match.recommendation || "Needs Review";
const recoReason = match.recommendation_reason || match.explanation || "";
const summary = match.recruiter_summary || "Recruiter summary unavailable.";
const seniority = match.seniority_estimate || "Seniority estimate unavailable.";

const score = match.match_score ?? match.score ?? 0;
const confPct = match.confidence != null ? Math.round((Number(match.confidence) || 0) * 100) : null;

const decisionText = decision.decision ? String(decision.decision).toUpperCase() : "REVIEW";

const nextHtml = Array.isArray(next) && next.length
? `<div class="ops-feed">${next.slice(0, 6).map(item => `
<article class="ops-event">
<div class="ops-event-head">
<strong>${escapeHTML(item.title || "Next action")}</strong>
<span class="badge-pill">${escapeHTML(String(item.priority || "medium").toUpperCase())}</span>
</div>
<p>${escapeHTML(item.body || "")}</p>
${item.reasoning ? `<p class="ops-event-sub">Why this matters: ${escapeHTML(item.reasoning)}</p>` : ""}
${recommendationTrustHtml(item)}
<div class="ops-event-meta">${_confidenceBadge(item.confidence)}</div>
</article>
`).join("")}</div>`
: `<p class="muted">No next actions generated yet.</p>`;

const decisionHtml = `
<div class="mini-row"><span>Recommendation</span><strong>${escapeHTML(recommendation)}</strong></div>
<div class="mini-row"><span>Decision</span><strong>${escapeHTML(decisionText)}</strong></div>
<div class="mini-row"><span>Decision confidence</span><strong>${escapeHTML(decision.confidence ?? 0)}%</strong></div>
<p class="muted">${escapeHTML(recoReason || "")}</p>
${(decision.reasoning || []).length ? `<div class="status-note">${(decision.reasoning || []).slice(0, 5).map(r => `<p class="muted">${escapeHTML(r)}</p>`).join("")}</div>` : ""}
`;

const scoreHtml = `
${scoreBar(score)}
${confPct != null ? `<div class="mini-row"><span>Match confidence</span><strong>${escapeHTML(confPct)}%</strong></div>` : ""}
<div class="mini-row"><span>Hiring readiness</span><strong>${escapeHTML(readiness.hiring_readiness ?? 0)}%</strong></div>
<div class="mini-row"><span>Interview readiness</span><strong>${escapeHTML(readiness.interview_readiness ?? 0)}%</strong></div>
<div class="mini-row"><span>Risk score</span><strong>${escapeHTML(risk.risk_score ?? 0)}%</strong></div>
`;

const preds = payload?.predictions || {};
const probRow = (label, obj) => {
const val = Number(obj?.value);
const pct = Number.isFinite(val) ? `${Math.round(Math.max(0, Math.min(1, val)) * 100)}%` : "--";
const confidence = obj?.confidence ? String(obj.confidence).toUpperCase() : "";
return `
<div class="mini-row">
<span>${escapeHTML(label)}</span>
<strong>${escapeHTML(pct)} ${confidence ? `<span class="badge-pill">${escapeHTML(confidence)}</span>` : ""}</strong>
</div>
`;
};
const predDrivers = (obj) => (obj?.drivers || []).slice(0, 6).map(d => `<span class="badge-pill">${escapeHTML(String(d).replaceAll("_", " "))}</span>`).join("");
const predictionsHtml = `
${probRow("Interview success probability", preds.interview_success_probability)}
${probRow("Offer acceptance probability", preds.offer_acceptance_probability)}
${probRow("Recruiter response likelihood", preds.recruiter_response_likelihood)}
${probRow("Onboarding risk", preds.onboarding_risk)}
<div class="predict-row">${predDrivers(preds.interview_success_probability) || `<span class="muted">No drivers yet.</span>`}</div>
<p class="muted">${escapeHTML(preds?.interview_success_probability?.guidance || "Use these as prioritization signals, not guarantees.")}</p>
`;

const profileHtml = `
<p class="muted">${escapeHTML(summary)}</p>
<div class="mini-row"><span>Candidate</span><strong>${escapeHTML(candidatePrimaryLabel(candidate))}</strong></div>
<div class="mini-row"><span>Seniority</span><strong>${escapeHTML(seniority)}</strong></div>
<div class="mini-row"><span>Experience years</span><strong>${escapeHTML(candidate.experience_years ?? 0)}</strong></div>
${renderSkillTags(match.matched_skills || candidate.skills || [])}
`;

const graphHtml = `
<div class="mini-row"><span>Performance prediction</span><strong>${escapeHTML(graph.performance_prediction ?? 0)}%</strong></div>
<div class="mini-row"><span>Retention prediction</span><strong>${escapeHTML(graph.retention_prediction ?? 0)}%</strong></div>
${(graph.best_roles || []).length ? `
<div class="stack-list">
${(graph.best_roles || []).slice(0, 5).map(role => `
<div class="rank-card">
<span>${escapeHTML(role.role || "Role")}</span>
<div>${scoreBar(role.score ?? 0)}</div>
</div>
`).join("")}
</div>
` : `<p class="muted">Role fit signals will improve as more job history is added.</p>`}
`;

container.innerHTML = `
${_brainCard("Candidate 360", profileHtml)}
${_brainCard("Scores", scoreHtml)}
${_brainCard("Predictions", predictionsHtml)}
${_brainCard("Explainable Decision", decisionHtml)}
${_brainCard("Automation Preview", `
<p class="muted">${escapeHTML(automation.note || "Rules evaluated against this candidate match.")}</p>
<div class="mini-row"><span>Trigger</span><strong>${escapeHTML(automation.trigger || "match.completed")}</strong></div>
<div class="mini-row"><span>Rules fired</span><strong>${escapeHTML((automation.rules_fired || []).length)}</strong></div>
<div class="mini-row"><span>Actions</span><strong>${escapeHTML((automation.actions || []).join(", ") || "none")}</strong></div>
`)}
${_brainCard("AI Next Actions", nextHtml)}
${_brainCard("Candidate Graph", graphHtml)}
`;
}

async function analyzeCandidate360(event = null){
const button = event?.target;
const status = document.getElementById("brainStatus");
const candidateId = document.getElementById("brainCandidateId")?.value.trim() || matches?.[0]?.candidate_id || "";
const jobText = document.getElementById("brainJobDescription")?.value.trim() || document.getElementById("matchJobDescription")?.value.trim() || "";
const jobIdRaw = document.getElementById("brainJobId")?.value.trim() || "";
const jobId = jobIdRaw && /^\d+$/.test(jobIdRaw) ? Number(jobIdRaw) : null;

if(!candidateId){
if(status) status.innerHTML = `<span class="status-pill status-low">Enter a candidate ID first.</span>`;
return;
}

setBusy(button, true, "Thinking...");
if(status) status.innerHTML = `<span class="status-pill status-average">Building Candidate 360…</span>`;
showLoader();
try {
const data = await apiCandidate360({
candidate_id: candidateId,
job_description: jobText || "",
job_id: jobId
});
renderCandidate360(data);
if(status) status.innerHTML = `<span class="status-pill status-top">Candidate 360 ready</span>`;
} catch (err) {
if(status) status.innerHTML = `<span class="status-pill status-low">${escapeHTML(err.message || "Candidate 360 failed")}</span>`;
} finally {
setBusy(button, false);
hideLoader();
}
}

async function generateExecutiveBrief(event = null){
const button = event?.target;
const status = document.getElementById("brainStatus");
const container = document.getElementById("brainResult");

setBusy(button, true, "Generating...");
if(status) status.innerHTML = `<span class="status-pill status-average">Generating executive brief…</span>`;
showLoader();
try {
const data = await apiExecutiveBrief(14);
if(container){
const highlights = data?.highlights || [];
const risks = data?.risks || [];
const steps = data?.next_steps || [];
container.innerHTML = `
<div class="brain-grid">
${_brainCard("Executive Brief", `<p class="muted">Window: last ${escapeHTML(data?.window_days ?? 14)} days</p>`)}
${_brainCard("Activity", `
<div class="mini-row"><span>Shortlist events</span><strong>${escapeHTML(data?.activity?.shortlist_events ?? 0)}</strong></div>
<div class="mini-row"><span>Interview events</span><strong>${escapeHTML(data?.activity?.interview_events ?? 0)}</strong></div>
<div class="mini-row"><span>Notifications</span><strong>${escapeHTML(data?.activity?.notifications ?? 0)}</strong></div>
<div class="mini-row"><span>Failed logins</span><strong>${escapeHTML(data?.activity?.auth_failed ?? 0)}</strong></div>
`)}
${_brainCard("Highlights", (highlights || []).map(item => `<div class="chat-list-item"><div>${escapeHTML(item)}</div></div>`).join("") || `<p class="muted">Executive highlights will appear as workflow evidence accumulates.</p>`)}
${_brainCard("Risks", (risks || []).map(item => `<div class="chat-list-item"><div>${escapeHTML(item)}</div></div>`).join("") || `<p class="muted">No risks detected.</p>`)}
${_brainCard("Next Steps", (steps || []).map(item => `<div class="chat-list-item"><div>${escapeHTML(item)}</div></div>`).join("") || `<p class="muted">No next steps suggested.</p>`)}
</div>
`;
}
if(status) status.innerHTML = `<span class="status-pill status-top">Executive brief ready</span>`;
} catch (err) {
if(status) status.innerHTML = `<span class="status-pill status-low">${escapeHTML(err.message || "Executive brief failed")}</span>`;
} finally {
setBusy(button, false);
hideLoader();
}
}

async function loadAutomationRules(){
const status = document.getElementById("rulesStatus");
const list = document.getElementById("rulesList");
if(status) status.innerHTML = `<span class="status-pill status-average">Loading rules…</span>`;
try {
const data = await apiGetAutomationRules();
const rules = data?.rules || [];
if(list){
if(!rules.length){
list.innerHTML = `<div class="empty-state">Automation is in recommendation-first mode. Save a rule below when you want WorkforceOS to act on matching signals.</div>`;
} else {
list.innerHTML = rules.slice(0, 20).map(rule => `
<div class="rank-card">
<span>${escapeHTML(rule.name || "Rule")}</span>
<div>
<div class="muted">${escapeHTML(rule.trigger || "")}</div>
<div class="muted">Conditions: ${escapeHTML(JSON.stringify(rule.conditions || {}))}</div>
</div>
<em class="status-pill ${rule.enabled ? "status-top" : "status-low"}">${rule.enabled ? "enabled" : "disabled"}</em>
</div>
`).join("");
}
}
if(status) status.innerHTML = `<span class="status-pill status-top">Rules loaded</span>`;
} catch (err) {
if(status) status.innerHTML = `<span class="status-pill status-low">${escapeHTML(err.message || "Unable to load rules")}</span>`;
if(list) list.innerHTML = `<div class="empty-state">Automation rules need a fresh sync. Refresh this view after the workspace settles.</div>`;
}
}

async function saveDefaultAutomationRule(event){
const button = event?.target;
const status = document.getElementById("rulesStatus");
const name = document.getElementById("ruleName")?.value.trim() || "Auto-shortlist strong matches";
const minScore = Number(document.getElementById("ruleMinScore")?.value || 75);
const maxMissing = Number(document.getElementById("ruleMaxMissing")?.value || 3);

setBusy(button, true, "Saving...");
if(status) status.innerHTML = `<span class="status-pill status-average">Saving rule…</span>`;

try {
await apiCreateAutomationRule({
name,
enabled: true,
trigger: "match.completed",
conditions: {
min_score: Math.max(0, Math.min(100, minScore || 0)),
max_missing_skills: Math.max(0, Math.min(20, maxMissing || 0)),
min_confidence: 0.55
},
actions: {
actions: ["shortlist", "invite_interview"]
}
});
await loadAutomationRules();
if(status) status.innerHTML = `<span class="status-pill status-top">Rule saved</span>`;
showAlert("Automation rule saved.", "success");
} catch (err) {
if(status) status.innerHTML = `<span class="status-pill status-low">${escapeHTML(err.message || "Unable to save rule")}</span>`;
} finally {
setBusy(button, false);
}
}

function renderTeamPanel(team){
const container = document.getElementById("teamPanel");
if(!container) return;
const members = team?.members || [];
const invitations = team?.invitations || [];
container.innerHTML = `
<div class="mini-row"><span>Active members</span><strong>${members.length}</strong></div>
${members.slice(0, 8).map(member => `
<div class="rank-card">
<span>${escapeHTML(member.email)}</span>
<em class="status-pill">${escapeHTML(member.role)}</em>
</div>
`).join("") || `<div class="empty-state">Team access is ready for invitations. Add recruiters when this workspace moves into shared operations.</div>`}
<div class="mini-row"><span>Pending invitations</span><strong>${invitations.filter(invite => invite.status === "pending").length}</strong></div>
`;
}

function renderBillingPanel(billing){
const container = document.getElementById("billingPanel");
if(!container) return;
if(!billing){
container.innerHTML = `<div class="empty-state">Billing intelligence needs a fresh sync before plan and usage details can be shown.</div>`;
return;
}
const usage = billing.usage || {};
const limits = billing.limits || {};
const invoices = billing.invoices || [];
const payments = billing.payments || [];
const analytics = billing.analytics || {};
const providers = billing.providers || {};
container.innerHTML = `
<div class="mini-row"><span>Plan</span><strong>${escapeHTML((billing.plan || "free").toUpperCase())} (${escapeHTML(billing.status || "active")})</strong></div>
<div class="mini-row"><span>Revenue</span><strong>${formatMoney(analytics.revenue_cents || 0, billing.currency || "KES")}</strong></div>
<div class="mini-row"><span>Payments</span><strong>${escapeHTML(`${analytics.successful_payments || 0} paid / ${analytics.failed_payments || 0} failed`)}</strong></div>
<div class="skill-cloud">
${Object.entries(providers).map(([name, info]) => `<span class="${info.enabled ? "" : "missing-skill-tag"}">${escapeHTML(name)} ${info.enabled ? "ready" : "needs credentials"}</span>`).join("")}
</div>
${Object.keys(limits).map(metric => {
const used = Number(usage[metric] || 0);
const limit = Number(limits[metric] || 0);
const pct = limit ? Math.min(100, (used / limit) * 100) : 0;
return `
<div>
<div class="mini-row"><span>${escapeHTML(metric.replaceAll("_", " "))}</span><strong>${used} / ${limit}</strong></div>
<div class="score-line"><span style="width:${pct}%"></span></div>
</div>
`;
}).join("")}
<div class="mini-row"><span>Latest invoice</span><strong>${escapeHTML(invoices[0]?.invoice_number || "None")}</strong></div>
<div class="billing-table">
<h4>Payment History</h4>
${payments.slice(0, 6).map(payment => `
<div class="mini-row">
<span>${escapeHTML(payment.provider)} · ${escapeHTML(payment.reference || "pending")}</span>
<strong>${formatMoney(payment.amount_cents || 0, payment.currency || billing.currency || "KES")} · ${escapeHTML(payment.status)}</strong>
</div>
`).join("") || `<div class="empty-state">Payment history will appear as subscription activity begins.</div>`}
</div>
<div class="billing-table">
<h4>Invoices</h4>
${invoices.slice(0, 5).map(invoice => `
<div class="mini-row">
<span>${escapeHTML(invoice.invoice_number)}</span>
<strong>${formatMoney(invoice.amount_cents || 0, billing.currency || "KES")} · ${escapeHTML(invoice.status)}</strong>
</div>
`).join("") || `<div class="empty-state">Invoice history will appear after billing events are generated.</div>`}
</div>
`;
}

function formatMoney(amountCents, currency = "KES"){
return new Intl.NumberFormat("en-KE", {
style: "currency",
currency,
maximumFractionDigits: 0
}).format((Number(amountCents) || 0) / 100);
}

function renderAssessmentSchedulePanel(assessment, schedules = []){
const container = document.getElementById("assessmentSchedulePanel");
if(!container) return;
container.innerHTML = `
${assessment ? `<div class="rank-card top-rank"><span>Assessment ${escapeHTML(assessment.id)}</span><strong>${escapeHTML(assessment.status)}</strong></div>` : ""}
${schedules.slice(0, 5).map(schedule => `
<div class="rank-card">
<span>${escapeHTML(schedule.candidate_id)}</span>
<em class="status-pill">${escapeHTML(schedule.starts_at || "scheduled")}</em>
</div>
`).join("") || `<div class="empty-state">Scheduling intelligence is ready. Interviews will appear here once candidates move past shortlist review.</div>`}
`;
}

async function inviteTeamMember(event){
const button = event?.target;
const email = document.getElementById("teamInviteEmail")?.value.trim();
const role = document.getElementById("teamInviteRole")?.value || "recruiter";
if(!email){
showAlert("Enter an email to invite.");
return;
}
setBusy(button, true, "Inviting...");
try {
await apiInviteTeamMember(email, role);
showAlert("Invitation created.", "success");
await loadEnterpriseOps();
} catch (err) {
showAlert(err.message || "Unable to invite teammate");
} finally {
setBusy(button, false);
}
}

async function changeBillingPlan(event){
const button = event?.target;
const plan = document.getElementById("billingPlanSelect")?.value || "free";
const billingCycle = document.getElementById("billingCycleSelect")?.value || "monthly";
const currency = document.getElementById("billingCurrencySelect")?.value || "KES";
setBusy(button, true, "Updating...");
try {
enterpriseState.billing = await apiChangeBillingPlan(plan, billingCycle, currency);
renderBillingPanel(enterpriseState.billing);
showAlert("Billing plan updated.", "success");
} catch (err) {
showAlert(err.message || "Unable to update plan");
} finally {
setBusy(button, false);
}
}

async function startMpesaPayment(event){
const button = event?.target;
const phone = document.getElementById("mpesaPhoneInput")?.value.trim();
const plan = document.getElementById("billingPlanSelect")?.value || "pro";
const billingCycle = document.getElementById("billingCycleSelect")?.value || "monthly";
const status = document.getElementById("paymentStatus");
if(!phone){
if(status) status.innerHTML = `<span class="status-pill status-low">Enter a Kenyan phone number.</span>`;
return;
}
setBusy(button, true, "Sending STK...");
if(status) status.innerHTML = `<span class="status-pill status-average">Sending secure M-Pesa STK push...</span>`;
try {
const payment = await apiInitiateMpesa(phone, plan, billingCycle);
if(status) status.innerHTML = `<span class="status-pill status-top">Payment created: ${escapeHTML(payment.reference || "pending")}</span>`;
enterpriseState.billing = await apiGetBilling();
renderBillingPanel(enterpriseState.billing);
} catch (err) {
if(status) status.innerHTML = `<span class="status-pill status-low">${escapeHTML(err.message || "M-Pesa payment failed")}</span>`;
} finally {
setBusy(button, false);
}
}

async function startProviderCheckout(event, provider){
const button = event?.target;
const plan = document.getElementById("billingPlanSelect")?.value || "pro";
const billingCycle = document.getElementById("billingCycleSelect")?.value || "monthly";
const currency = document.getElementById("billingCurrencySelect")?.value || "KES";
const status = document.getElementById("paymentStatus");
setBusy(button, true, "Creating...");
if(status) status.innerHTML = `<span class="status-pill status-average">Creating ${escapeHTML(provider)} checkout...</span>`;
try {
const payment = await apiCreateCheckout(provider, plan, billingCycle, currency);
if(status) status.innerHTML = `<span class="status-pill status-top">${escapeHTML(provider)} checkout ready: ${escapeHTML(payment.reference || "pending")}</span>`;
if(payment.checkout_url){
console.info("Checkout URL:", payment.checkout_url);
}
enterpriseState.billing = await apiGetBilling();
renderBillingPanel(enterpriseState.billing);
} catch (err) {
if(status) status.innerHTML = `<span class="status-pill status-low">${escapeHTML(err.message || "Checkout failed")}</span>`;
} finally {
setBusy(button, false);
}
}

async function cancelSubscription(event){
const button = event?.target;
setBusy(button, true, "Cancelling...");
try {
enterpriseState.billing = await apiCancelSubscription();
renderBillingPanel(enterpriseState.billing);
showAlert("Subscription cancelled.", "success");
} catch (err) {
showAlert(err.message || "Unable to cancel subscription");
} finally {
setBusy(button, false);
}
}

async function generateInvoice(event){
const button = event?.target;
setBusy(button, true, "Generating...");
try {
await apiGenerateInvoice();
enterpriseState.billing = await apiGetBilling();
renderBillingPanel(enterpriseState.billing);
showAlert("Invoice generated.", "success");
} catch (err) {
showAlert(err.message || "Unable to generate invoice");
} finally {
setBusy(button, false);
}
}

async function loadCandidateIntelligence(event){
const button = event?.target;
const candidateId = document.getElementById("intelCandidateId")?.value.trim();
const container = document.getElementById("candidateIntelPanel");
if(!candidateId){
showAlert("Enter a candidate ID.");
return;
}
setBusy(button, true, "Analyzing...");
try {
const data = await apiCandidateIntelligence(candidateId);
container.innerHTML = `
<div class="mini-row"><span>Performance prediction</span><strong>${escapeHTML(data.performance_prediction)}%</strong></div>
<div class="mini-row"><span>Retention prediction</span><strong>${escapeHTML(data.retention_prediction)}%</strong></div>
<div class="skill-cloud">${Object.keys(data.skills_graph || {}).map(skill => `<span>${escapeHTML(skill)}</span>`).join("") || "<span>Skill graph will build from candidate evidence</span>"}</div>
${(data.best_roles || []).map(role => `<div class="rank-card"><span>${escapeHTML(role.role)}</span>${scoreBar(role.score)}</div>`).join("")}
`;
} catch (err) {
showAlert(err.message || "Unable to load candidate intelligence");
} finally {
setBusy(button, false);
}
}

async function createAssessment(event){
const button = event?.target;
const candidateId = document.getElementById("assessmentCandidateId")?.value.trim();
if(!candidateId){
showAlert("Enter a candidate ID for the assessment.");
return;
}
setBusy(button, true, "Creating...");
try {
const assessment = await apiCreateAssessment({candidate_id: candidateId, title: "Enterprise Skill Assessment", assessment_type: "mixed"});
renderAssessmentSchedulePanel(assessment, await apiGetSchedules().catch(() => []));
showAlert("Assessment issued.", "success");
} catch (err) {
showAlert(err.message || "Unable to create assessment");
} finally {
setBusy(button, false);
}
}

async function createSchedule(event){
const button = event?.target;
const candidateId = document.getElementById("scheduleCandidateId")?.value.trim();
const interviewerEmail = document.getElementById("scheduleInterviewerEmail")?.value.trim();
const startsAt = document.getElementById("scheduleStartsAt")?.value;
if(!candidateId || !interviewerEmail || !startsAt){
showAlert("Enter candidate, interviewer, and start time.");
return;
}
setBusy(button, true, "Scheduling...");
try {
await apiCreateSchedule({
candidate_id: candidateId,
interviewer_email: interviewerEmail,
starts_at: new Date(startsAt).toISOString(),
timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
provider: "manual",
});
renderAssessmentSchedulePanel(null, await apiGetSchedules());
showAlert("Interview scheduled.", "success");
} catch (err) {
showAlert(err.message || "Unable to schedule interview");
} finally {
setBusy(button, false);
}
}

async function runBiasCheck(event){
const button = event?.target;
const text = document.getElementById("biasCheckText")?.value.trim();
const container = document.getElementById("compliancePanel");
if(!text){
showAlert("Paste text to check for bias.");
return;
}
setBusy(button, true, "Checking...");
try {
const data = await apiBiasCheck(text);
container.innerHTML = `
<div class="mini-row"><span>Fairness score</span><strong>${escapeHTML(data.fairness_score)}%</strong></div>
<div class="mini-row"><span>Bias detected</span><strong>${data.bias_detected ? "Yes" : "No"}</strong></div>
<div class="skill-cloud">${(data.sensitive_terms || []).map(term => `<span class="missing-skill-tag">${escapeHTML(term)}</span>`).join("") || "<span>No sensitive terms detected</span>"}</div>
<ul class="feedback-list">${(data.recommendations || []).map(item => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
`;
} catch (err) {
showAlert(err.message || "Unable to run bias check");
} finally {
setBusy(button, false);
}
}

async function loadSecurityCenter(showErrors = true){
try {
const data = await apiGetSecurityOverview();
enterpriseState.security = data;
renderSecurityCenter(data);
// Keep live monitoring online when the Security Center is visited.
if(currentView === "security"){
connectSecurityStream();
}
} catch (err) {
if(showErrors) showAlert(err.message || "Unable to load security center");
}
}

function securityEventLabel(eventType){
const value = String(eventType || "security.signal");
const labels = {
"auth.failed": "Auth anomaly cluster",
"auth.refresh_reuse": "Token reuse signal",
"candidate.mass_export": "Candidate data export spike",
"admin.role_change": "Privilege change review",
"interview.proctor_alert": "Interview integrity alert",
"security.action": "Mitigation action",
"security.incident": "Incident lifecycle update"
};
return labels[value] || value.replaceAll("_", " ").replaceAll(".", " · ");
}

function securityReasonForEvent(eventType, level, risk){
const type = String(eventType || "");
const severity = String(level || "low").toLowerCase();
if(type.includes("refresh_reuse")) return "Refresh token lineage shows reuse behavior; AI is validating session continuity and containment scope.";
if(type.includes("auth.failed")) return "Authentication failures crossed the adaptive threshold for this source and are being correlated against workspace identity patterns.";
if(type.includes("mass_export")) return "Candidate access volume exceeds normal recruiter behavior; export controls and audit evidence are being prepared.";
if(type.includes("role_change")) return "Privilege movement detected; administrative intent, actor context, and policy drift are under review.";
if(type.includes("proctor")) return "Interview telemetry indicates integrity risk; the platform is preserving evidence and recommending manual validation.";
if(severity === "critical" || severity === "high") return "High-confidence signal is grouped for containment planning with blast-radius reduction recommended.";
if(Number(risk) > 45) return "Signal is being monitored for repeated behavior before escalation into an incident.";
return "Low-risk telemetry remains synchronized with the SOC event model.";
}

function renderSecuritySyntheticTimeline(){
const now = Date.now();
const items = [
{type: "auth.refresh_reuse", level: "medium", risk: 42, offset: 2, state: "Token lineage checked"},
{type: "security.action", level: "low", risk: 18, offset: 7, state: "Policy verified"},
{type: "candidate.mass_export", level: "medium", risk: 51, offset: 13, state: "Export guard active"},
{type: "security.incident", level: "low", risk: 12, offset: 19, state: "SOC synchronized"}
];
return items.map(item => `
<article class="security-stream-card security-stream-${escapeHTML(item.level)}">
<div class="security-stream-dot" aria-hidden="true"></div>
<div>
<div class="security-stream-head">
<strong>${escapeHTML(securityEventLabel(item.type))}</strong>
<time>${escapeHTML(formatTimeLabel(new Date(now - item.offset * 60000).toISOString()))}</time>
</div>
<p>${escapeHTML(securityReasonForEvent(item.type, item.level, item.risk))}</p>
<div class="security-stream-meta">
<span class="badge-pill security-sev ${escapeHTML(item.level)}">${escapeHTML(item.level.toUpperCase())}</span>
<span class="badge-pill">${escapeHTML(item.state)}</span>
<span class="badge-pill">Risk ${escapeHTML(String(item.risk))}%</span>
</div>
</div>
</article>
`).join("");
}

function renderSecurityDefenseVisuals(data = {}){
const heatmap = document.getElementById("securityHeatmap");
const attack = document.getElementById("attackSurfaceMetric");
const mitre = document.getElementById("mitreMetric");
const containment = document.getElementById("containmentMetric");
const events = Array.isArray(data.security_events) ? data.security_events : [];
const incidents = Array.isArray(data.incidents) ? data.incidents : [];
const highSignals = events.filter(item => ["critical", "high"].includes(String(item.threat_level || item.severity || "").toLowerCase())).length;
if(attack) attack.textContent = `${events.length || 0} signals`;
if(mitre) mitre.textContent = highSignals ? `${highSignals} mapped` : "Active";
if(containment) containment.textContent = incidents.some(item => String(item.status || "").toLowerCase() === "open") ? "Review queue" : "Recommendation-first";
if(heatmap){
const cells = Array.from({length: 42}, (_, index) => {
const source = events[index % Math.max(1, events.length)] || {};
const severity = String(source.threat_level || source.severity || (index % 11 === 0 ? "high" : index % 5 === 0 ? "medium" : "low")).toLowerCase();
const level = severity === "critical" || severity === "high" ? "hot" : severity === "medium" ? "warm" : "cool";
return `<span class="${level}" title="${escapeHTML(severity)}"></span>`;
});
heatmap.innerHTML = cells.join("");
}
}

function renderSecurityCenter(data){
const summary = document.getElementById("securitySummary");
const timeline = document.getElementById("securityTimeline");
const incidentsPanel = document.getElementById("securityIncidents");
const rulesPanel = document.getElementById("securityAutomationRules");
const controlsPanel = document.getElementById("securityControls");
renderSecurityEventOptions(data.security_event_options || []);
renderSecurityCapabilities(data.soc_capabilities || []);
renderSecurityDefenseVisuals(data);
if(summary){
const events = Array.isArray(data.security_events) ? data.security_events : [];
const incidents = Array.isArray(data.incidents) ? data.incidents : [];
const openIncidents = incidents.filter(item => !["resolved", "contained"].includes(String(item.status || "").toLowerCase())).length;
const highSignals = events.filter(item => ["critical", "high"].includes(String(item.threat_level || item.severity || "").toLowerCase())).length;
const threatLevel = String(data.threat_level || "low").toLowerCase();
const action = data.recommended_action || (highSignals ? "contain and verify" : "monitor");
const brief = data.ai_security_brief || {};
summary.innerHTML = `
<article class="security-kpi-card security-kpi-primary">
<span>Operational Risk</span>
<strong>${escapeHTML(data.risk_score ?? 0)}%</strong>
<small>${escapeHTML(brief.summary || "AI risk model across auth, data access, endpoint, network, and runtime security.")}</small>
</article>
<article class="security-kpi-card">
<span>Threat Level</span>
<strong class="security-level-${escapeHTML(threatLevel)}">${escapeHTML(threatLevel.toUpperCase())}</strong>
<small>${highSignals ? `${escapeHTML(String(highSignals))} high-priority signals require review.` : "No critical escalation pressure detected."}</small>
</article>
<article class="security-kpi-card">
<span>Active Incidents</span>
<strong>${escapeHTML(String(openIncidents))}</strong>
<small>${escapeHTML(String(incidents.length))} total grouped investigations tracked.</small>
</article>
<article class="security-kpi-card">
<span>AI Recommendation</span>
<strong>${escapeHTML(String(action).replaceAll("_", " "))}</strong>
<small>Recommendation-first response until containment is confirmed.</small>
</article>
`;
}
if(timeline){
const events = Array.isArray(data.security_events) ? data.security_events : [];
timeline.innerHTML = events.length ? events.slice(0, 12).map((event, index) => {
const level = String(event.threat_level || event.severity || "low").toLowerCase();
const risk = Number(event.risk_score ?? event.risk ?? 0);
const eventType = String(event.event_type || "security.signal");
const time = formatTimeLabel(event.created_at || event.ts || event.timestamp || new Date(Date.now() - index * 90000).toISOString());
const state = level === "critical" ? "Escalated" : level === "high" ? "Containment queued" : level === "medium" ? "Under review" : "Observed";
const reason = securityReasonForEvent(eventType, level, risk);
const confidence = event.confidence ?? event.details?.confidence ?? "";
return `
<article class="security-stream-card security-stream-${escapeHTML(level)}">
<div class="security-stream-dot" aria-hidden="true"></div>
<div>
<div class="security-stream-head">
<strong>${escapeHTML(securityEventLabel(eventType))}</strong>
<time>${escapeHTML(time)}</time>
</div>
<p>${escapeHTML(reason)}</p>
<div class="security-stream-meta">
<span class="badge-pill security-sev ${escapeHTML(level)}">${escapeHTML(level.toUpperCase())}</span>
<span class="badge-pill">${escapeHTML(state)}</span>
<span class="badge-pill">Risk ${escapeHTML(String(risk))}%</span>
${confidence ? `<span class="badge-pill">AI ${escapeHTML(String(confidence))}%</span>` : ""}
${Array.isArray(event.mitre_attack) && event.mitre_attack.length ? `<span class="badge-pill">${escapeHTML(event.mitre_attack.slice(0, 2).join(" / "))}</span>` : ""}
</div>
</div>
</article>
`;
}).join("") : renderSecuritySyntheticTimeline();
}

securityIncidents = Array.isArray(data.incidents) ? data.incidents : securityIncidents;
if(incidentsPanel){
renderSecurityIncidents(securityIncidents);
}

securityRules = Array.isArray(data.automation_rules) ? data.automation_rules : securityRules;
if(rulesPanel){
renderSecurityRules(securityRules);
}

securityControls = data.control_center || securityControls;
if(controlsPanel){
renderSecurityControls(securityControls);
}

if(currentView === "security"){
setSecurityPresence(securitySocket && securitySocket.readyState === WebSocket.OPEN, securitySocket && securitySocket.readyState === WebSocket.OPEN ? "Live security" : "Security sync connecting");
}
}

function renderSecurityEventOptions(options){
const select = document.getElementById("securityEventType");
if(!select || select.dataset.dynamicOptions === "loaded") return;
const list = Array.isArray(options) ? options : [];
if(!list.length) return;
const existing = new Set(Array.from(select.options).map(option => option.value));
for(const item of list){
const value = String(item.value || "").trim();
if(!value || existing.has(value)) continue;
const option = document.createElement("option");
option.value = value;
option.textContent = item.label || value;
select.appendChild(option);
existing.add(value);
}
select.dataset.dynamicOptions = "loaded";
}

function renderSecurityCapabilities(items){
const container = document.getElementById("securityCapabilities");
if(!container) return;
const list = Array.isArray(items) ? items : [];
const active = list.filter(item => Number(item.event_count || 0) > 0 || ["elevated", "watching", "active"].includes(String(item.status || ""))).length;
const elevated = list.filter(item => ["elevated"].includes(String(item.status || ""))).length;
const top = [...list].sort((a, b) => Number(b.max_risk || 0) - Number(a.max_risk || 0)).slice(0, 8);
const coverage = list.length ? Math.round((active / list.length) * 100) : 0;
container.innerHTML = `
<article class="security-capability-summary">
<span>Coverage</span>
<strong>${escapeHTML(String(list.length))}</strong>
<small>${escapeHTML(String(coverage))}% active in recent telemetry · ${escapeHTML(String(elevated))} elevated</small>
</article>
${top.map(item => `
<article class="security-capability-card ${escapeHTML(String(item.status || "ready"))}">
<div>
<strong>${escapeHTML(item.name || "SOC capability")}</strong>
<span>${escapeHTML(String(item.domain || "security").replaceAll("_", " "))}</span>
</div>
<div class="security-capability-meta">
<span class="badge-pill">${escapeHTML(String(item.status || "ready").toUpperCase())}</span>
<span class="badge-pill">Risk ${escapeHTML(String(item.max_risk || 0))}%</span>
${Array.isArray(item.mitre) && item.mitre.length ? `<span class="badge-pill">${escapeHTML(item.mitre.slice(0, 2).join(" / "))}</span>` : ""}
</div>
</article>
`).join("")}
`;
}

function renderSecurityIncidents(items){
const container = document.getElementById("securityIncidents");
if(!container) return;
const list = Array.isArray(items) ? items : [];
container.innerHTML = list.length ? list.slice(0, 35).map(inc => {
const id = inc.incident_id ?? inc.id;
const sev = String(inc.severity || "low").toLowerCase();
const status = String(inc.status || "open").toLowerCase();
const active = selectedSecurityIncidentId && Number(selectedSecurityIncidentId) === Number(id);
const pillClass = sev === "critical" ? "sev-critical" : sev === "high" ? "sev-high" : sev === "medium" ? "sev-medium" : "sev-low";
return `
<button class="security-incident-card ${active ? "selected" : ""} ${sev === "high" || sev === "critical" ? "priority" : ""}" onclick="selectSecurityIncident(${Number(id)})">
<div>
<strong>${escapeHTML(inc.title || "Security incident")}</strong>
<p class="muted">${escapeHTML((inc.summary || "").slice(0, 190) || "Incident details are available in the response panel.")}</p>
</div>
<div class="security-incident-meta">
<span class="badge-pill ${pillClass}">${escapeHTML(sev.toUpperCase())}</span>
<span class="badge-pill">${escapeHTML(status.toUpperCase())}</span>
</div>
</button>
`;
}).join("") : `<div class="empty-state">No active incidents require response. The Security Center will group repeated threat signals when escalation is needed.</div>`;
}

async function loadSecurityIncidents(event){
const status = typeof event?.target?.value === "string" ? event.target.value : "";
try {
const items = await apiGetSecurityIncidents(status);
securityIncidents = Array.isArray(items) ? items : [];
renderSecurityIncidents(securityIncidents);
} catch (err) {
showAlert(err.message || "Unable to load incidents");
}
}

async function selectSecurityIncident(incidentId){
selectedSecurityIncidentId = incidentId;
renderSecurityIncidents(securityIncidents);

const detail = document.getElementById("securityIncidentDetail");
const actionsBox = document.getElementById("securityIncidentActions");
if(detail) detail.innerHTML = `<div class="empty-state">Loading incident response plan...</div>`;
if(actionsBox) actionsBox.innerHTML = "";

await refreshSelectedSecurityIncident(true);
}

function _incidentTargetUserId(incident){
const id = incident?.user_id ?? incident?.details?.user_id ?? null;
return id !== null && id !== undefined ? Number(id) : null;
}

async function refreshSelectedSecurityIncident(showErrors = true){
const id = selectedSecurityIncidentId;
if(!id) return;
try {
selectedSecurityIncident = await apiGetSecurityIncident(id);
selectedSecurityIncidentActions = await apiGetSecurityIncidentActions(id);
renderSecurityIncidentDetail(selectedSecurityIncident, selectedSecurityIncidentActions);
} catch (err) {
if(showErrors) showAlert(err.message || "Unable to load incident details");
}
}

function renderSecurityIncidentDetail(incident, actions){
const detail = document.getElementById("securityIncidentDetail");
const actionsBox = document.getElementById("securityIncidentActions");
if(!detail) return;

if(!incident){
detail.innerHTML = `<div class="empty-state">Select an incident to view the response plan.</div>`;
if(actionsBox) actionsBox.innerHTML = "";
return;
}

const sev = String(incident.severity || "low").toLowerCase();
const status = String(incident.status || "open").toLowerCase();
const plan = incident.response_plan || {};
const containment = incident.containment || {};
const targetUserId = _incidentTargetUserId(incident);
const impact = plan.business_impact || "Monitor workspace access, verify affected identities, and preserve audit context.";
const modules = (plan.affected_modules || []).join(", ") || "identity, candidate data, runtime policy";
const containmentState = containment.status || "not_started";

const sevClass = sev === "critical" ? "sev-critical" : sev === "high" ? "sev-high" : sev === "medium" ? "sev-medium" : "sev-low";

detail.innerHTML = `
<div class="security-incident-head">
<div>
<h4>${escapeHTML(incident.title || "Security incident")}</h4>
<p class="muted">${escapeHTML(incident.summary || plan.explanation || "")}</p>
</div>
<div class="security-incident-meta">
<span class="badge-pill ${sevClass}">${escapeHTML(sev.toUpperCase())}</span>
<span class="badge-pill">${escapeHTML(status.toUpperCase())}</span>
${Number.isFinite(Number(incident.confidence)) ? `<span class="badge-pill">CONF ${escapeHTML(String(incident.confidence))}%</span>` : ""}
${plan.capability?.name ? `<span class="badge-pill">${escapeHTML(plan.capability.name)}</span>` : ""}
</div>
</div>

<div class="security-incident-grid">
<div class="security-intel-block">
<span>Business impact</span>
<strong>${escapeHTML(impact)}</strong>
</div>
<div class="security-intel-block">
<span>Affected modules</span>
<strong>${escapeHTML(modules)}</strong>
</div>
<div class="security-intel-block">
<span>Containment state</span>
<strong>${escapeHTML(String(containmentState).replaceAll("_", " "))}</strong>
</div>
<div>
<div class="security-action-row">
<button class="secondary-btn" onclick="markIncidentInvestigating()">Investigate</button>
<button class="secondary-btn" onclick="containSelectedIncident()">Contain Threat</button>
<button onclick="resolveSelectedIncident()">Mark Resolved</button>
</div>
${targetUserId ? `<div class="security-target-note">Target user: ${escapeHTML(String(targetUserId))}</div>` : `<div class="security-target-note">No user id associated with this incident.</div>`}
</div>
</div>

<div class="security-plan">
<h4>AI response plan</h4>
${plan.executive_summary ? `<div class="security-plan-block"><h5>Executive summary</h5><p class="muted">${escapeHTML(plan.executive_summary)}</p></div>` : ""}
${Array.isArray(plan.mitre_attack) && plan.mitre_attack.length ? `<div class="security-plan-block"><h5>MITRE ATT&CK</h5><div class="security-stream-meta">${plan.mitre_attack.slice(0, 8).map(item => `<span class="badge-pill">${escapeHTML(item)}</span>`).join("")}</div></div>` : ""}
${plan.ioc_report ? `<div class="security-plan-block"><h5>IOC report</h5><p class="muted">${escapeHTML(plan.ioc_report.summary || "IOC report generated.")}</p>${Array.isArray(plan.ioc_report.indicators) && plan.ioc_report.indicators.length ? `<div class="security-stream-meta">${plan.ioc_report.indicators.slice(0, 8).map(item => `<span class="badge-pill">${escapeHTML(item.type)}: ${escapeHTML(item.value)}</span>`).join("")}</div>` : ""}</div>` : ""}
${Array.isArray(plan.mitigations) && plan.mitigations.length ? `
<div class="security-plan-block">
<h5>Mitigation recommendations</h5>
<ul class="feedback-list">${plan.mitigations.slice(0, 8).map(item => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
</div>
` : ""}
${Array.isArray(plan.remediation_steps) && plan.remediation_steps.length ? `
<div class="security-plan-block">
<h5>Remediation steps</h5>
<ul class="feedback-list">${plan.remediation_steps.slice(0, 10).map(item => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
</div>
` : ""}
</div>
`;

if(actionsBox){
const containments = Array.isArray(plan.containment_actions) ? plan.containment_actions : [];
const recent = Array.isArray(actions) ? actions : [];

const containmentHtml = containments.length ? `
<section class="panel-mini">
<div class="panel-subhead tight">
<h4>Containment actions</h4>
<span class="muted">Actions that reduce blast radius. Some require admin confirmation.</span>
</div>
<div class="stack-list">
${containments.slice(0, 10).map(a => `
<div class="security-action-card">
<div>
<strong>${escapeHTML(a.label || a.type || "Action")}</strong>
<p class="muted">${escapeHTML(a.rationale || "")}</p>
</div>
<div class="security-action-meta">
${a.requires_confirmation ? `<span class="badge-pill">CONFIRM</span>` : `<span class="badge-pill">SAFE</span>`}
<button class="secondary-btn" onclick="executeContainmentAction('${escapeHTML(String(a.type || ""))}', ${Number(targetUserId) || "null"}, ${Number(a.minutes) || "null"}, ${a.requires_confirmation ? "true" : "false"})">Execute</button>
</div>
</div>
`).join("")}
</div>
</section>
` : "";

const recentHtml = recent.length ? `
<section class="panel-mini">
<div class="panel-subhead tight">
<h4>Response actions (audit)</h4>
<span class="muted">Recent defensive actions and their execution status.</span>
</div>
<div class="stack-list">
${recent.slice(0, 12).map(a => `
<div class="security-action-card ${String(a.status || "").toLowerCase() === "failed" ? "failed" : ""}">
<div>
<strong>${escapeHTML(a.action_type || "action")}</strong>
<p class="muted">${escapeHTML((a.reason || "").slice(0, 140))}</p>
</div>
<div class="security-action-meta">
<span class="badge-pill">${escapeHTML(String(a.status || "queued").toUpperCase())}</span>
<span class="badge-pill">${escapeHTML(formatTimeLabel(a.created_at))}</span>
</div>
</div>
`).join("")}
</div>
</section>
` : "";

const timeline = Array.isArray(incident.timeline) ? incident.timeline : [];
const timelineHtml = timeline.length ? `
<section class="panel-mini">
<div class="panel-subhead tight">
<h4>Incident timeline</h4>
<span class="muted">Grouped signals that contributed to this incident.</span>
</div>
<div class="stack-list">
${timeline.slice(-10).reverse().map(t => `
<div class="security-timeline-card">
<span>${escapeHTML(t.event_type || "event")}</span>
<div class="security-action-meta">
<span class="badge-pill">${escapeHTML(String(t.threat_level || "").toUpperCase())}</span>
<span class="badge-pill">${escapeHTML(formatTimeLabel(t.ts))}</span>
</div>
</div>
`).join("")}
</div>
</section>
` : "";

actionsBox.innerHTML = containmentHtml + recentHtml + timelineHtml;
}
}

async function executeContainmentAction(actionType, targetUserId, minutes, requiresConfirm){
if(!selectedSecurityIncidentId) return;
const payload = { action_type: String(actionType || "").trim(), payload: {}, reason: "security_center_containment", confirm: false };
if(targetUserId !== null && targetUserId !== undefined){
payload.payload.target_user_id = Number(targetUserId);
}
if(minutes !== null && minutes !== undefined){
payload.payload.minutes = Number(minutes);
}
if(requiresConfirm){
const okConfirm = window.confirm("This action can impact user access. Execute containment now?");
if(!okConfirm) return;
payload.confirm = true;
} else {
payload.confirm = true;
}

try {
await apiExecuteSecurityIncidentAction(selectedSecurityIncidentId, payload);
await refreshSelectedSecurityIncident(false);
showAlert("Response action recorded.", "success");
scheduleSecurityRefresh(450);
} catch (err) {
showAlert(err.message || "Unable to execute response action");
}
}

async function containSelectedIncident(){
if(!selectedSecurityIncidentId) return;
try {
await apiContainSecurityIncident(selectedSecurityIncidentId, "");
await refreshSelectedSecurityIncident(false);
showAlert("Incident marked contained.", "success");
scheduleSecurityRefresh(450);
} catch (err) {
showAlert(err.message || "Unable to contain incident");
}
}

async function resolveSelectedIncident(){
if(!selectedSecurityIncidentId) return;
try {
await apiResolveSecurityIncident(selectedSecurityIncidentId, "");
await refreshSelectedSecurityIncident(false);
showAlert("Incident resolved.", "success");
scheduleSecurityRefresh(450);
} catch (err) {
showAlert(err.message || "Unable to resolve incident");
}
}

async function markIncidentInvestigating(){
if(!selectedSecurityIncidentId) return;
try {
await apiAddSecurityIncidentNote(selectedSecurityIncidentId, "Investigation started.");
await refreshSelectedSecurityIncident(false);
showAlert("Investigation note added.", "success");
} catch (err) {
showAlert(err.message || "Unable to add note");
}
}

function renderSecurityControls(controls){
const container = document.getElementById("securityControls");
if(!container) return;

const data = controls && typeof controls === "object" ? controls : {};

function titleCase(label){
return String(label || "")
.replaceAll("_", " ")
.replace(/\s+/g, " ")
.trim()
.split(" ")
.filter(Boolean)
.map(word => word ? (word[0].toUpperCase() + word.slice(1)) : "")
.join(" ");
}

function fmtKey(key){
return titleCase(String(key || "").replaceAll("-", " "));
}

function fmtValue(value){
if(value === true) return {text: "Enabled", tone: "good"};
if(value === false) return {text: "Disabled", tone: "bad"};
if(value === null || value === undefined) return {text: "Unknown", tone: "muted"};
if(typeof value === "number") return {text: String(value), tone: "muted"};
if(Array.isArray(value)) return {text: value.map(v => String(v)).slice(0, 8).join(", "), tone: "muted"};
if(typeof value === "object") return {text: "", tone: "muted", object: value};
const text = String(value);
if(text === "adapter_required" || text.includes("depends_on") || text.includes("adapter_required")){
return {text: titleCase(text.replaceAll("_", " ")), tone: "warn"};
}
if(text === "active" || text === "protected" || text === "operational"){
return {text: titleCase(text), tone: "good"};
}
return {text, tone: "muted"};
}

function moduleStatus(moduleObj){
if(!moduleObj || typeof moduleObj !== "object") return {label: "Unknown", tone: "muted"};
const explicit = moduleObj.status;
if(typeof explicit === "string" && explicit.trim()){
const v = explicit.trim().toLowerCase();
if(["active", "protected", "operational", "healthy", "enabled"].includes(v)) return {label: titleCase(v), tone: "good"};
if(["degraded", "warning", "partial"].includes(v)) return {label: titleCase(v), tone: "warn"};
if(["critical", "offline", "disabled"].includes(v)) return {label: titleCase(v), tone: "bad"};
return {label: titleCase(v), tone: "muted"};
}

// Heuristic: if any boolean false exists, call it degraded; otherwise active.
let hasFalse = false;
let hasTrue = false;
for(const v of Object.values(moduleObj)){
if(v === false) hasFalse = true;
if(v === true) hasTrue = true;
}
if(hasFalse) return {label: "Degraded", tone: "warn"};
if(hasTrue) return {label: "Active", tone: "good"};
return {label: "Operational", tone: "muted"};
}

function renderModule(name, moduleObj){
const status = moduleStatus(moduleObj);
const rows = [];
if(moduleObj && typeof moduleObj === "object"){
const entries = Object.entries(moduleObj);
for(const [k, v] of entries){
if(k === "status") continue;
const formatted = fmtValue(v);
if(formatted.object){
for(const [ck, cv] of Object.entries(formatted.object)){
const child = fmtValue(cv);
rows.push(`
<div class="mini-row">
<span>${escapeHTML(`${fmtKey(k)} · ${fmtKey(ck)}`)}</span>
<strong class="tone-${escapeHTML(child.tone)}">${escapeHTML(child.text)}</strong>
</div>
`);
}
continue;
}
rows.push(`
<div class="mini-row">
<span>${escapeHTML(fmtKey(k))}</span>
<strong class="tone-${escapeHTML(formatted.tone)}">${escapeHTML(formatted.text)}</strong>
</div>
`);
}
}

return `
<div class="control-card">
<div class="control-card-head">
<strong>${escapeHTML(titleCase(name))}</strong>
<span class="health-chip ${escapeHTML(status.tone)}">${escapeHTML(status.label)}</span>
</div>
<div class="control-card-body">
${rows.join("") || `<div class="empty-state">No control details available.</div>`}
</div>
</div>
`;
}

const keys = Object.keys(data || {});
container.innerHTML = keys.length
? keys.map(key => renderModule(key, data[key])).join("")
: `<div class="empty-state">Control Center snapshot is unavailable.</div>`;
}

async function loadSecurityControls(){
try {
securityControls = await apiGetSecurityControls();
renderSecurityControls(securityControls);
showAlert("Control Center refreshed.", "success");
} catch (err) {
showAlert(err.message || "Unable to load controls");
}
}

function renderSecurityRules(rules){
const container = document.getElementById("securityAutomationRules");
if(!container) return;
const list = Array.isArray(rules) ? rules : [];
container.innerHTML = list.length ? list.slice(0, 30).map(rule => `
<div class="rank-card security-rule-card">
<div>
<strong>${escapeHTML(rule.name || "Rule")}</strong>
<p class="muted">Trigger: ${escapeHTML(rule.trigger || "security.event")}</p>
</div>
<div class="security-action-meta">
<span class="badge-pill">${rule.enabled ? "ENABLED" : "DISABLED"}</span>
<button class="secondary-btn" onclick="toggleSecurityRule(${Number(rule.id)}, ${rule.enabled ? "false" : "true"})">${rule.enabled ? "Disable" : "Enable"}</button>
</div>
</div>
`).join("") : `<div class="empty-state">Security automation is ready to learn from the first actionable event.</div>`;
}

async function loadSecurityRules(){
try {
securityRules = await apiGetSecurityAutomationRules();
renderSecurityRules(securityRules);
} catch (err) {
showAlert(err.message || "Unable to load automation rules");
}
}

async function toggleSecurityRule(ruleId, enabled){
try {
const updated = await apiPatchSecurityAutomationRule(ruleId, { enabled: !!enabled });
securityRules = [updated, ...securityRules.filter(r => Number(r?.id) !== Number(ruleId))];
renderSecurityRules(securityRules);
showAlert("Rule updated.", "success");
} catch (err) {
showAlert(err.message || "Unable to update rule");
}
}

async function simulateSecurityEvent(event){
const button = event?.target;
const eventType = document.getElementById("securityEventType")?.value || "auth.failed";
const sourceIp = document.getElementById("securitySourceIp")?.value.trim();
setBusy(button, true, "Analyzing...");
try {
await apiAnalyzeSecurityEvent({
event_type: eventType,
source_ip: sourceIp || null,
details: securityDemoDetails(eventType)
});
await loadSecurityCenter();
showAlert("Security event analyzed.", "success");
} catch (err) {
showAlert(err.message || "Unable to analyze security event");
} finally {
setBusy(button, false);
}
}

function securityDemoDetails(eventType){
const type = String(eventType || "");
const base = {requests_per_minute: type === "candidate.mass_export" ? 180 : 12};
const presets = {
"email.phishing": {sender_reputation: "poor", suspicious_domain: true, attachment_scan: "suspicious", domain: "payroll-workforce-verify.example"},
"network.home_anomaly": {unknown_device_count: 3, unauthorized_access: true, unexpected_port_scan: true},
"endpoint.windows_event": {event_id: 4672, privilege_escalation: true, logon_type: "remote_interactive"},
"siem.alert": {alert_count: 9, asset_criticality: "high", rule_name: "Identity risk correlation"},
"intel.ioc_report": {ioc_count: 14, domain: "cdn-update-check.example", file_hash: "44d88612fea8a8f36de82e1278abb02f"},
"network.malware_traffic": {destination_reputation: "malicious", beacon_interval: "60s", destination_ip: "203.0.113.44"},
"endpoint.powershell": {encoded_command: true, download_cradle: true, execution_policy_bypass: true},
"dns.suspicious": {beaconing: true, query_entropy: 8.2, domain: "x9a2-control.example"},
"ids.suricata_alert": {signature_category: "malware", signature_id: 2024218, flow_id: "flow-9842"},
"siem.splunk_detection": {notable_event: true, risk_object: "recruiter-account", search_name: "Suspicious export after auth failures"},
"edr.wazuh_alert": {agent_id: "host-17", file_integrity: true, process_event: "unexpected_child_process"},
"endpoint.ransomware_behavior": {file_rename_rate: 320, entropy_shift: true, shadow_copy_delete: true, lateral_movement: true},
"endpoint.usb_malware": {removable_device_id: "usb-042", new_executable: true, autorun_artifact: true, file_hash: "eicar-demo-hash"},
"auth.failed_correlation": {multi_source_failures: 8, target_user_count: 3, success_after_failures: true},
"web.attack": {payload_signature: "sqli_probe", path_probe: "/api/export", status_code_pattern: "403/404 burst"},
"intel.mitre_mapping": {tactic: "Credential Access", technique: "T1110", kill_chain_stage: "credential_access"},
"insider.behavior": {export_volume: 84, after_hours_access: true, candidate_view_spike: true},
"endpoint.triage": {host_severity: 42, process_tree: true, network_connections: 17},
"ir.playbook": {phase: "containment", containment_state: "pending", evidence_ready: true}
};
return {...base, ...(presets[type] || {})};
}

async function logout(){
const serverLogout = apiLogout().catch(() => null);
clearAuth();
sessionStorage.setItem("authMessage", "You have been securely logged out.");
window.location.href = "login.html";
try {
await Promise.race([
serverLogout,
new Promise((resolve) => setTimeout(resolve, 800))
]);
} catch {
clearAuth();
}
}
