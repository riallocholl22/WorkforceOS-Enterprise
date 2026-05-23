async function loadDashboard(requestToken = viewRequestSeq){
if(isFresh("dashboard")){
renderWorkforceOS(workforceOSState);
renderDashboardRanking(matches.slice(0, 5));
renderMatchDistributionChart(matches);
renderDashboardExecutiveBrief(dashboardExecutiveBrief);
return;
}

showLoader();
setDashboardSkeletons();
try {
const summary = await apiGetDashboardSummary();
if(requestToken && !isCurrentViewRequest(requestToken, "dashboard")) return;
// Seed the ops recommendations immediately (even before the live stream connects),
// using backend autonomous priorities.
if(Array.isArray(summary?.autonomous_priorities) && summary.autonomous_priorities.length){
renderOpsRecommendations(summary.autonomous_priorities);
}
let ranking = [];
if(!jobs.length && (summary.total_jobs ?? 0) > 0){
// Keep global job list in sync so onboarding cards behave consistently.
await loadJobs().catch(() => null);
}

// Avoid auto-ranking until jobs exist (backend validation is correct; UX should be guided).
if((summary.total_jobs ?? 0) > 0){
try {
ranking = await apiMatchCandidates("");
} catch {
ranking = [];
}
} else {
ranking = [];
}

matches = ranking;
if(requestToken && !isCurrentViewRequest(requestToken, "dashboard")) return;
document.getElementById("metricCandidates").innerText = summary.total_candidates ?? 0;
document.getElementById("metricJobs").innerText = summary.total_jobs ?? 0;
document.getElementById("metricTopScore").innerText = `${summary.top_score ?? 0}%`;
document.getElementById("metricReports").innerText = summary.total_reports ?? 0;
document.getElementById("metricMembers").innerText = summary.total_members ?? workspace?.totals?.members ?? 0;
renderDashboardRanking(ranking.slice(0, 5));
renderTopSkills(summary.top_skills || []);
renderMatchDistributionChart(ranking);
loadWorkforceOS()
.catch(() => null)
.finally(() => loadDashboardExecutiveBrief().catch(() => null));
markLoaded("dashboard");
} catch (err) {
showAlert(err.message || "Unable to load dashboard");
renderDashboardRanking([]);
renderTopSkills([]);
renderMatchDistributionChart([]);
} finally {
hideLoader();
}
}

function openExecutiveBrief(){
switchView("brain");
setTimeout(() => {
try { generateExecutiveBrief(); } catch {}
}, 60);
}

function renderDashboardExecutiveBrief(data){
const container = document.getElementById("dashboardExecutiveBrief");
if(!container) return;

if(!data || typeof data !== "object" || data.status !== "ok"){
container.innerHTML = `
<div class="empty-state empty-onboarding">
<h4>Executive brief will appear here.</h4>
<p class="muted">As your workspace activity grows, I’ll summarize pipeline health, risk signals, and the next best actions.</p>
</div>
`;
return;
}

const windowDays = Number(data.window_days) || 14;
const highlights = Array.isArray(data.highlights) ? data.highlights : [];
const risks = Array.isArray(data.risks) ? data.risks : [];
const nextSteps = Array.isArray(data.next_steps) ? data.next_steps : [];
const osSummary = data.operating_summary || workforceOSState?.executive_intelligence?.operating_summary || {};
const forecast = osSummary.workforce_forecast || {};
const signals = data.autonomous_insights?.signals || null;
const signalTotal = signals && typeof signals === "object"
? Number(signals.resume_uploads || 0) + Number(signals.match_runs || 0) + Number(signals.shortlist_events || 0) + Number(signals.interview_events || 0)
: 0;
const confidenceClass = signalTotal >= 8 ? "conf-high" : signalTotal >= 3 ? "conf-med" : "conf-low";
const confidenceLabel = signalTotal >= 8 ? "High evidence" : signalTotal >= 3 ? "Moderate evidence" : "Early signal";
const uncertainty = signalTotal >= 3
? "Confidence is based on recent workflow events, candidate movement, and recruiter decisions."
: "Uncertainty is higher until the workspace has more uploads, rankings, shortlist decisions, and interviews.";

const monitoring = signals && typeof signals === "object"
? `Monitoring: uploads ${signals.resume_uploads ?? 0}, match runs ${signals.match_runs ?? 0}, shortlist ${signals.shortlist_events ?? 0}, interviews ${signals.interview_events ?? 0}.`
: "Monitoring: uploads, matching, shortlisting velocity, and interview scheduling.";
const summaryReadout = osSummary.boardroom_readout || "";
const executiveUncertainty = osSummary.uncertainty || uncertainty;
const strategicSignals = workforceOSState?.executive_intelligence?.strategic_signals || {};
const trustContract = workforceOSState?.trust_contract || {};

container.innerHTML = `
<div class="exec-brief-head">
<span class="muted">Window: last ${escapeHTML(windowDays)} days</span>
<span class="badge-pill badge-confidence ${confidenceClass}">${escapeHTML(confidenceLabel)}</span>
</div>
${summaryReadout ? `<p class="exec-brief-readout">${escapeHTML(summaryReadout)}</p>` : ""}
<div class="exec-kpi-strip">
<span><b>${escapeHTML(osSummary.recruiter_productivity_index ?? "--")}</b>Recruiter productivity</span>
<span><b>${escapeHTML(osSummary.ai_effectiveness_index ?? "--")}</b>AI effectiveness</span>
<span><b>${escapeHTML(forecast.delay_probability ?? "--")}%</b>Delay risk</span>
<span><b>${escapeHTML(_confidenceLabel(osSummary.confidence) || confidenceLabel)}</b>Forecast confidence</span>
</div>
<p class="exec-brief-monitor muted">${escapeHTML(monitoring)}</p>
<p class="exec-brief-monitor muted">${escapeHTML(executiveUncertainty)}</p>
<div class="ai-trust-strip exec-trust-strip">
<span><b>Reasoning</b>${escapeHTML(summaryReadout || "WorkforceOS is correlating recent workflow events with staffing risk, recruiter productivity, and operational readiness.")}</span>
<span><b>Confidence</b>${escapeHTML(_confidenceLabel(strategicSignals.confidence ?? osSummary.confidence) || confidenceLabel)}</span>
<span><b>Action</b>${escapeHTML(nextSteps[0] || "Maintain ranking, shortlist, schedule, and interview cadence.")}</span>
<span><b>Uncertainty</b>${escapeHTML(strategicSignals.uncertainty || executiveUncertainty)}</span>
</div>

<div class="exec-brief-grid">
<div class="exec-brief-block">
<h4>Highlights</h4>
<ul>${highlights.slice(0, 4).map(item => `<li>${escapeHTML(item)}</li>`).join("") || "<li>Workspace is in early-signal mode. Add hiring activity to establish velocity.</li>"}</ul>
</div>
<div class="exec-brief-block">
<h4>Risks</h4>
<ul>${risks.slice(0, 4).map(item => `<li>${escapeHTML(item)}</li>`).join("") || "<li>Current operating signals do not require escalation.</li>"}</ul>
</div>
<div class="exec-brief-block">
<h4>Next Steps</h4>
<ul>${nextSteps.slice(0, 4).map(item => `<li>${escapeHTML(item)}</li>`).join("") || "<li>Upload resumes, run matching, and review the top candidates.</li>"}</ul>
</div>
</div>
`;
}

async function loadDashboardExecutiveBrief(force = false){
if(!force && isFresh("exec_brief") && dashboardExecutiveBrief){
renderDashboardExecutiveBrief(dashboardExecutiveBrief);
return;
}
const container = document.getElementById("dashboardExecutiveBrief");
if(container){
container.innerHTML = `
<div class="exec-brief-loading">
<div class="skeleton"></div>
<div class="skeleton"></div>
<div class="skeleton"></div>
</div>
`;
}

try {
dashboardExecutiveBrief = await apiExecutiveBrief(14);
renderDashboardExecutiveBrief(dashboardExecutiveBrief);
markLoaded("exec_brief");
} catch (err) {
dashboardExecutiveBrief = null;
if(container){
container.innerHTML = renderOnboardingCard(
"Executive brief unavailable yet.",
String(err?.message || "").toLowerCase().includes("organization")
? "Workspace initialization is in progress. Once your organization is ready, the executive brief will populate automatically."
: "I couldn’t generate the executive brief right now. Refresh in a moment."
);
}
}
}

function setDashboardSkeletons(){
["metricCandidates", "metricJobs", "metricReports", "metricMembers", "metricTopScore"].forEach(id => {
const node = document.getElementById(id);
if(node) node.innerHTML = `<span class="skeleton metric-skeleton"></span>`;
});
const ranking = document.getElementById("dashboardRanking");
if(ranking){
ranking.innerHTML = `
<div class="skeleton"></div>
<div class="skeleton"></div>
<div class="skeleton"></div>
`;
}
}

function renderDashboardRanking(data){
const container = document.getElementById("dashboardRanking");

if(!data.length){
if(!jobs.length){
container.innerHTML = renderMatchOnboardingState("AI Decision Brain is ready. Start by creating a hiring role, then upload resumes.");
} else {
container.innerHTML = renderOnboardingCard(
"Ranking intelligence is ready.",
"Upload resumes, then run AI ranking to see recruiter-grade fit insights."
);
}
return;
}

container.innerHTML = data.map((candidate, index) => `
<button class="rank-card ${index === 0 ? "top-rank" : ""}" onclick="openCandidateModalFromMatch(${index})">
<span>${escapeHTML(candidatePrimaryLabel(candidate))}</span>
${scoreBar(candidate.match_score ?? candidate.score)}
</button>
`).join("");
}

function renderTopSkills(data){
const container = document.getElementById("topSkills");
container.innerHTML = data.length
? data.map(item => `<span>${escapeHTML(item.skill)} ${escapeHTML(item.count)}</span>`).join("")
: `<span>Skill trends will appear after candidate intake</span>`;
}

async function loadCandidates(requestToken = viewRequestSeq){
if(isFresh("candidates") && candidates.length){
renderCandidateTable(candidates);
return;
}

showLoader();
try {
candidates = await apiGetCandidates();
if(requestToken && !isCurrentViewRequest(requestToken, "candidates") && currentView === "candidates") return;
renderCandidateTable(candidates);
markLoaded("candidates");
} catch (err) {
showAlert(err.message || "Unable to load candidates");
} finally {
hideLoader();
}
}

function renderCandidateTable(data){
const container = document.getElementById("candidateTable");
if(!container) return;

if(!data.length){
container.innerHTML = renderOnboardingCard(
"Candidate intake is ready.",
"Upload a resume to start building candidate profiles and AI insights.",
`<button onclick="switchView('upload')">Upload Resume</button>`
);
return;
}

const sorted = [...data].sort((a, b) => {
const aMatch = matches.find(item => item.candidate_id === a.candidate_id) || {};
const bMatch = matches.find(item => item.candidate_id === b.candidate_id) || {};
const aScore = aMatch.match_score ?? aMatch.score ?? 0;
const bScore = bMatch.match_score ?? bMatch.score ?? 0;
if(bScore !== aScore) return bScore - aScore;
return String(a.candidate_id || "").localeCompare(String(b.candidate_id || ""));
});

const visible = sorted.slice(0, MAX_TABLE_ROWS);
const overflow = data.length > MAX_TABLE_ROWS
? `<div class="empty-state">Showing first ${MAX_TABLE_ROWS} of ${data.length} candidates. Use matching to prioritize the full set.</div>`
: "";

container.innerHTML = `
${overflow}
<table class="data-table">
<thead>
<tr>
<th>Name</th>
<th>Skills</th>
<th>Match Score</th>
<th>Status</th>
<th></th>
</tr>
</thead>
<tbody>
${visible.map((candidate, index) => {
const matched = matches.find(item => item.candidate_id === candidate.candidate_id) || {};
const score = matched.match_score ?? matched.score ?? 0;
const status = scoreStatus(score);
const isShortlisted = shortlist.has(candidate.candidate_id);
const topClass = index === 0 ? "top-match-row" : index < 3 ? "top-three-row" : "";
const primary = candidatePrimaryLabel(candidate);
const secondary = candidateSecondaryLabel(candidate);
return `
<tr class="${[isShortlisted ? "shortlisted-row" : "", topClass].filter(Boolean).join(" ")}">
<td>
<div><strong>${escapeHTML(primary)}</strong></div>
${secondary ? `<div class="muted small">${escapeHTML(secondary)}</div>` : ""}
</td>
<td>${renderSkillTags(candidate.skills || [])}</td>
<td>${scoreBar(score)}</td>
<td><span class="status-pill ${status.className}">${status.label}</span></td>
<td class="row-actions">
<button onclick="openCandidateModalById('${escapeHTML(candidate.candidate_id)}')">View</button>
<button class="secondary-btn" onclick="toggleShortlist('${escapeHTML(candidate.candidate_id)}')">
${isShortlisted ? "Shortlisted" : "Shortlist"}
</button>
</td>
</tr>
`;
}).join("")}
</tbody>
</table>
`;
}

function renderSkillTags(skills){
if(!skills.length) return `<span class="muted">No explicit skills extracted yet</span>`;
return `<div class="skill-cloud">${skills.slice(0, 6).map(skill => `<span>${escapeHTML(skill)}</span>`).join("")}</div>`;
}

async function loadJobs(requestToken = viewRequestSeq){
if(isFresh("jobs") && jobs.length){
renderJobs(jobs);
return;
}

showLoader();
try {
jobs = await apiGetJobs();
if(requestToken && !isCurrentViewRequest(requestToken, "jobs") && currentView === "jobs") return;
renderJobs(jobs);
markLoaded("jobs");
} catch (err) {
showAlert(err.message || "Unable to load jobs");
} finally {
hideLoader();
}
}

function renderJobs(data){
const container = document.getElementById("jobList");

if(!data.length){
container.innerHTML = `<div class="empty-state">Role intake is ready. Create a hiring role above to activate ranking, matching, and workflow intelligence.</div>`;
return;
}

container.innerHTML = data.map((job, index) => `
<article class="job-card">
<div>
<h4>${escapeHTML(job.title)}</h4>
<p>${escapeHTML(job.description)}</p>
</div>
<button onclick="useJobForMatch(${index})">Match</button>
</article>
`).join("");
}

async function createJob(event){
const title = document.getElementById("jobTitle").value.trim();
const description = document.getElementById("jobDesc").value.trim();
const status = document.getElementById("jobStatus");

if(!title || !description){
status.innerText = "Title and description are required.";
return;
}

const button = event?.target;
showLoader();
setBusy(button, true, "Creating...");
try {
await apiCreateJob({title, description});
lastLoaded.jobs = 0;
lastLoaded.dashboard = 0;
status.innerText = "Role created. WorkforceOS can now rank candidates against this hiring context.";
document.getElementById("jobTitle").value = "";
document.getElementById("jobDesc").value = "";
await loadJobs();
} catch (err) {
status.innerText = err.message || "Unable to create job.";
} finally {
setBusy(button, false);
hideLoader();
}
}

function useJobForMatch(index){
const description = jobs[index]?.description || "";
switchView("matches");
document.getElementById("matchJobDescription").value = description;
runMatch();
}

async function uploadResume(event){
const fileInput = document.getElementById("resumeFileInput");
const candidateId = document.getElementById("candidateIdInput").value.trim();
const status = document.getElementById("uploadStatus");
const file = fileInput.files[0];

if(!file){
status.innerHTML = `<span class="status-pill status-low">Select a PDF, DOCX, or TXT resume first.</span>`;
return;
}

const button = event?.target;
showLoader();
setBusy(button, true, "Processing...");
status.innerHTML = `
<span class="status-pill status-average">Uploading resume…</span>
<div class="progress-track"><span style="width:22%"></span></div>
<p class="muted">Extracting text and preparing candidate intelligence.</p>
`;

try {
const jobDescription = document.getElementById("matchJobDescription")?.value.trim() || "";
status.innerHTML = `
<span class="status-pill status-average">Extracting text…</span>
<div class="progress-track"><span style="width:46%"></span></div>
<p class="muted">Trying multiple parsers and falling back safely when needed.</p>
`;
const result = await apiUploadResume(file, candidateId, jobDescription);
status.innerHTML = `
<span class="status-pill status-average">Building profile and match signals…</span>
<div class="progress-track"><span style="width:82%"></span></div>
`;
lastResumeText = result.raw_text || result.text || result.text_snippet || "";
if(result.feedback){
feedbackData = result.feedback;
feedbackState = "ready";
renderFeedbackPanel();
}
const warnings = Array.isArray(result.warnings) ? result.warnings : [];
const warningHtml = warnings.length
? `<div class="status-note">${warnings.map(item => `<p class="muted">${escapeHTML(item.message || "")}</p>`).join("")}</div>`
: "";
const skillCount = Array.isArray(result.skills) ? result.skills.length : 0;
const extraction = result.extraction || {};
const parser = extraction.parser_used || "";
const confPct = extraction.confidence != null ? Math.round((Number(extraction.confidence) || 0) * 100) : null;
const advancedLayout = Boolean(extraction.is_scanned_pdf || extraction.is_scanned_docx || (parser && String(parser).includes("fallback")) || (parser && String(parser).includes("ocr")));
const ocrNote = extraction.ocr?.status === "used"
? "OCR fallback used"
: extraction.ocr?.status === "needed"
? "OCR recommended"
: "";
const extractionHtml = `
<div class="status-note">
${advancedLayout ? `<p class="muted"><b>Advanced CV detected.</b> We extracted available text and flagged confidence where needed.</p>` : ""}
<p class="muted">Parser: <b>${escapeHTML(parser || "auto")}</b>${confPct != null ? ` · Confidence: <b>${escapeHTML(confPct)}%</b>` : ""}${ocrNote ? ` · <b>${escapeHTML(ocrNote)}</b>` : ""}</p>
</div>
`;
const uploadedName = (result.profile && (result.profile.candidate_name || result.profile.name)) ? String(result.profile.candidate_name || result.profile.name).trim() : "";
status.innerHTML = `
<span class="status-pill ${warnings.length ? "status-average" : "status-top"}">${warnings.length ? "Processed with notes" : "Processed"}</span>
<p>Candidate <b>${escapeHTML(uploadedName || "Unnamed Candidate")}</b> added with ${skillCount} extracted skills.</p>
<div class="muted small">${escapeHTML(uploadedName ? `ID: ${result.candidate_id}` : `Candidate ID: ${result.candidate_id}`)}</div>
${extractionHtml}
${warningHtml}
${renderSkillTags(result.skills || [])}
`;

// Enterprise workflow automation signal (non-blocking): creates an ops event + notification.
apiEvaluateWorkflow({
candidate_id: result.candidate_id,
match_score: Number(result?.match?.match_score ?? result?.match?.score ?? 0) || 0,
job_id: ""
}).catch(() => null);

fileInput.value = "";
document.getElementById("candidateIdInput").value = "";
lastLoaded.candidates = 0;
lastLoaded.matches = 0;
lastLoaded.dashboard = 0;
await loadCandidates();
await runMatch(false);
await fetchAIFeedback(jobDescription);
showAlert(warnings.length ? "Resume processed with extraction notes." : "Resume processed. Candidate intelligence is ready.", warnings.length ? "info" : "success");
} catch (err) {
status.innerHTML = `
<span class="status-pill status-low">Upload failed</span>
<p class="muted">${escapeHTML(err.message || "Please retry. If the CV is scanned or designer-formatted, export a text-based PDF/DOCX or upload TXT.")}</p>
`;
} finally {
setBusy(button, false);
hideLoader();
}
}

async function runMatch(showLoading = true){
const input = document.getElementById("matchJobDescription");
let description = input?.value.trim() || "";
const container = document.getElementById("matchResults");
const status = document.getElementById("matchStatus");
const matchButton = document.getElementById("matchRunButton");

// Ensure we have the latest job list when entering Match Results.
if(!jobs.length){
try { await loadJobs(); } catch {}
}

// Onboarding: don't call /match unless we have a job description or at least one job configured.
if(!description && !jobs.length){
if(container) container.innerHTML = renderMatchOnboardingState();
if(status) status.innerHTML = `<span class="status-pill status-average">Create a job to activate AI ranking</span>`;
return;
}

// Convenience: if jobs exist and textarea is empty, use the latest job description.
if(!description && jobs.length){
description = String(jobs[0]?.description || "").trim();
if(input && description) input.value = description;
}

if(showLoading) showLoader();
setBusy(matchButton, true, "Matching...");
if(status) status.innerHTML = `<span class="status-pill status-average">Matching candidates…</span>`;

try {
matches = (await apiMatchCandidates(description)).sort(
(a, b) => (b.match_score ?? b.score ?? 0) - (a.match_score ?? a.score ?? 0)
);
renderMatchResults(matches);
renderMatchDistributionChart(matches);
markLoaded("matches");
if(currentView === "candidates") renderCandidateTable(candidates);
if(status) status.innerHTML = `<span class="status-pill status-top">Ranked ${matches.length} candidates with explainable fit signals</span>`;
updateShortlistCount();
if(lastResumeText){
await fetchAIFeedback(description);
}
} catch (err) {
// Never show raw backend validation messages as scary recruiter errors.
if(container){
container.innerHTML = jobs.length
? renderOnboardingCard(
"AI ranking is ready.",
"Add a clear job description, then run ranking again. If you just created a job, refresh Jobs once and retry."
)
: renderMatchOnboardingState();
}
if(status) status.innerHTML = `<span class="status-pill status-low">Match failed</span>`;
} finally {
setBusy(matchButton, false);
if(showLoading) hideLoader();
}
}

async function runDecisionAI(){
const panel = document.getElementById("decisionPanel");
const description = document.getElementById("matchJobDescription")?.value.trim() || "";

if(!jobs.length && !description){
showAlert("Create a job description first to activate recruiter Decision AI.", "info");
if(panel){
panel.classList.remove("hidden");
panel.innerHTML = renderMatchOnboardingState("Decision AI becomes available once a hiring role is configured.");
}
return;
}

if(!matches.length){
await runMatch(false);
}

if(!matches.length){
showAlert("Run matching before asking for a hiring decision.");
return;
}

if(panel){
panel.classList.remove("hidden");
panel.innerHTML = `<span class="status-pill status-average">Decision AI is reviewing candidates...</span>`;
}

try {
const data = await apiDecision({candidates: matches, job_description: description});
const best = data.best_candidate || {};
const suggestions = data.shortlist_suggestions || [];
if(panel){
const bestCandidate = candidateById(best.candidate_id) || best;
const bestLabel = candidatePrimaryLabel(bestCandidate);
panel.innerHTML = `
<h4>Recruiter Decision AI</h4>
<p><strong>Best candidate:</strong> ${escapeHTML(bestLabel)} ${best.candidate_id ? `<span class="muted small">(${escapeHTML(best.candidate_id)})</span>` : ""}</p>
<p>${escapeHTML(data.reasoning || "No reasoning returned.")}</p>
<div class="decision-shortlist">
${suggestions.map(item => `
<article>
<strong>${escapeHTML(candidatePrimaryLabel(candidateById(item.candidate_id) || item))}</strong>
<span>${escapeHTML(item.score ?? 0)}%</span>
<p>${escapeHTML(item.reason || "Recommended for shortlist.")}</p>
</article>
`).join("")}
</div>
`;
}
} catch (err) {
if(panel){
panel.innerHTML = `<span class="status-pill status-low">${escapeHTML(err.message || "Decision AI failed")}</span>`;
}
}
}

function renderMatchResults(data){
const container = document.getElementById("matchResults");
if(!container) return;

if(!data.length){
if(!jobs.length){
container.innerHTML = renderMatchOnboardingState("Candidate intelligence activates once jobs are configured.");
} else if(!candidates.length){
container.innerHTML = renderOnboardingCard(
"Candidate intake is ready.",
"Upload a resume to start building candidate profiles, then run AI ranking.",
`<button onclick="switchView('upload')">Upload Resume</button>`
);
} else {
container.innerHTML = renderOnboardingCard(
"Ranking intelligence is ready.",
"Run AI ranking to generate recruiter-grade summaries, missing skills, and shortlist recommendations."
);
}
return;
}

container.innerHTML = `
<div class="match-list">
${data.slice(0, MAX_TABLE_ROWS).map((candidate, index) => {
const score = candidate.match_score ?? candidate.score ?? 0;
const status = scoreStatus(score);
const isShortlisted = shortlist.has(candidate.candidate_id);
const preds = candidate?.predictions || {};
const interviewProb = preds?.interview_success_probability?.value;
const onboardingRisk = preds?.onboarding_risk?.value;
const offerProb = preds?.offer_acceptance_probability?.value;
const fmtProb = (val) => {
const num = Number(val);
if(!Number.isFinite(num)) return null;
const pct = Math.round(Math.max(0, Math.min(1, num)) * 100);
return `${pct}%`;
};
const interviewPct = fmtProb(interviewProb);
const riskPct = fmtProb(onboardingRisk);
const offerPct = fmtProb(offerProb);
return `
<article class="match-card ${index === 0 ? "highlight" : ""} ${isShortlisted ? "shortlisted-row" : ""}">
<div>
<div class="match-rank">#${index + 1}</div>
<h4>${escapeHTML(candidatePrimaryLabel(candidate))}</h4>
${candidateSecondaryLabel(candidate) ? `<div class="muted small">${escapeHTML(candidateSecondaryLabel(candidate))}</div>` : ""}
<div class="reco-row">
<span class="reco-pill ${escapeHTML((candidate.recommendation || '').toLowerCase().replaceAll(' ', '-')) || 'needs-review'}">
${escapeHTML(candidate.recommendation || "Needs Review")}
</span>
${Array.isArray(candidate.insight_badges) ? candidate.insight_badges.slice(0, 3).map(b => `<span class="badge-pill">${escapeHTML(b)}</span>`).join("") : ""}
</div>
<p class="muted">${escapeHTML(candidate.recruiter_summary || candidate.text_snippet || "Run matching against a role to generate recruiter-grade insight.")}</p>
${renderSkillTags(candidate.matched_skills || candidate.skills || [])}
<div class="explain-panel">
<strong>Recruiter notes</strong>
<p>${escapeHTML(candidate.recommendation_reason || candidate.explanation || "Match reasoning will appear after role context is available.")}</p>
${candidate.next_operating_step ? `<p><strong>Next operating step:</strong> ${escapeHTML(candidate.next_operating_step)}</p>` : ""}
${(interviewPct || riskPct || offerPct) ? `
<div class="predict-row">
${interviewPct ? `<span class="badge-pill">Interview: ${escapeHTML(interviewPct)}</span>` : ""}
${riskPct ? `<span class="badge-pill">Onboarding risk: ${escapeHTML(riskPct)}</span>` : ""}
${offerPct ? `<span class="badge-pill">Offer accept: ${escapeHTML(offerPct)}</span>` : ""}
</div>
` : ""}
${(Array.isArray(candidate.strengths) && candidate.strengths.length) ? `
<div class="mini-kv">
<span class="muted">Strengths</span>
<span>${escapeHTML(candidate.strengths.slice(0, 2).join(" "))}</span>
</div>
` : ""}
<div class="skill-cloud">
${(candidate.missing_skills || []).slice(0, 6).map(skill => `<span class="status-pill status-low">${escapeHTML(skill)}</span>`).join("")
|| (candidate.required_skills?.length ? `<span class="muted">No major gaps detected from the job’s must-haves</span>` : `<span class="muted">Job description didn’t list clear must-have skills</span>`)}
</div>
</div>
</div>
<div class="match-score">
${scoreBar(score)}
${candidate.confidence != null ? (() => {
const pct = Math.max(0, Math.min(100, Math.round((Number(candidate.confidence) || 0) * 100)));
const ringClass = pct >= 78 ? "ring-high" : pct >= 55 ? "ring-mid" : "ring-low";
return `
<div class="confidence-row">
<div class="confidence-ring ${ringClass}" style="--pct:${pct}">
<span>${pct}%</span>
</div>
<div>
<div class="muted">Confidence</div>
<strong>${pct}%</strong>
</div>
</div>
`;
})() : ""}
<span class="status-pill ${status.className}">${status.label}</span>
<button onclick="openCandidateModalFromMatch(${index})">View</button>
<button class="secondary-btn" onclick="toggleCompareCandidate('${escapeHTML(candidate.candidate_id)}')">
${compareCandidateIds.has(String(candidate.candidate_id)) ? "Comparing" : "Compare"}
</button>
<button class="secondary-btn" onclick="startCandidateInterview('${escapeHTML(candidate.candidate_id)}')">Interview</button>
<button class="secondary-btn" onclick="toggleShortlist('${escapeHTML(candidate.candidate_id)}')">
${isShortlisted ? "Shortlisted" : "Shortlist"}
</button>
<button class="secondary-btn" onclick="approveCandidateFromMatch('${escapeHTML(candidate.candidate_id)}')">Approve</button>
<button class="danger-btn" onclick="rejectCandidateFromMatch('${escapeHTML(candidate.candidate_id)}')">Reject</button>
</div>
</article>
`;
}).join("")}
</div>
`;
}

function startCandidateInterview(candidateId){
switchView("interview");
const input = document.getElementById("interviewCandidateId");
if(input) input.value = candidateId;
}

function openCandidateModal(index){
const candidate = candidates[index];
const matched = matches.find(item => item.candidate_id === candidate.candidate_id) || {};
rememberViewedCandidate(candidate?.candidate_id);
renderCandidateModal({...candidate, ...matched});
}

function openCandidateModalById(candidateId){
const candidate = candidates.find(item => item.candidate_id === candidateId) || {};
const matched = matches.find(item => item.candidate_id === candidateId) || {};
rememberViewedCandidate(candidateId);
renderCandidateModal({...candidate, ...matched});
}

function openCandidateModalFromMatch(index){
const item = matches[index];
rememberViewedCandidate(item?.candidate_id);
renderCandidateModal(item);
}

function renderCandidateModal(candidate){
const primary = candidatePrimaryLabel(candidate);
const secondary = candidateSecondaryLabel(candidate);
const confidence = Number(candidate.confidence || 0);
const confidencePct = confidence > 1 ? Math.round(confidence) : Math.round(confidence * 100);
const missing = Array.isArray(candidate.missing_skills) ? candidate.missing_skills : [];
const matched = Array.isArray(candidate.matched_skills) ? candidate.matched_skills : [];
const uncertainty = [];
const modelUncertainty = Array.isArray(candidate.uncertainty) ? candidate.uncertainty : [];
if(confidencePct && confidencePct < 55) uncertainty.push("Match confidence is limited; review the resume text before making a final decision.");
if(missing.length) uncertainty.push(`Validate ${missing.slice(0, 3).join(", ")} before advancing.`);
if(!candidate.recruiter_summary && !candidate.recommendation_reason) uncertainty.push("This profile has limited generated context; run matching against a specific role for stronger guidance.");
modelUncertainty.forEach(item => {
if(item && !uncertainty.includes(item)) uncertainty.push(item);
});
document.getElementById("modalName").innerText = primary;
document.getElementById("modalInsight").innerText = candidate.recruiter_summary || candidate.recommendation_reason || candidate.text_snippet || "Run matching against a role to generate a recruiter-ready summary.";
document.getElementById("modalScore").innerHTML = scoreBar(candidate.match_score ?? candidate.score ?? 0);
document.getElementById("modalSkills").innerHTML = renderSkillTags(candidate.skills || candidate.matched_skills || []);
const strengths = Array.isArray(candidate.strengths) ? candidate.strengths : [];
const risks = Array.isArray(candidate.risks) ? candidate.risks : [];
const focus = candidate?.interview_plan?.focus_areas || [];
const questions = candidate?.interview_plan?.suggested_questions || [];

document.getElementById("modalSnippet").innerText = [
secondary ? secondary : "",
candidate.recommendation ? `Recommendation: ${candidate.recommendation}` : "",
confidencePct ? `Confidence: ${confidencePct}%` : "",
candidate.recommendation_reason || "",
candidate.next_operating_step ? `Next operating step:\n- ${candidate.next_operating_step}` : "",
matched.length ? `Evidence:\n- Matched role signals: ${matched.slice(0, 6).join(", ")}` : "",
uncertainty.length ? `Uncertainty:\n- ${uncertainty.join("\n- ")}` : "",
strengths.length ? `Strengths:\n- ${strengths.join("\n- ")}` : "",
risks.length ? `Risks:\n- ${risks.join("\n- ")}` : "",
missing.length ? `Missing skills:\n- ${missing.join("\n- ")}` : "",
focus.length ? `Interview focus:\n- ${focus.slice(0, 5).join("\n- ")}` : "",
questions.length ? `Suggested questions:\n- ${questions.slice(0, 5).join("\n- ")}` : "",
candidate.text_snippet || ""
].filter(Boolean).join("\n\n");
document.getElementById("candidateModal").classList.add("visible");
}

function closeModal(){
document.getElementById("candidateModal").classList.remove("visible");
}

// Interview system variables
let interviewStream = null;
let interviewSessionId = "";
let currentInterviewQuestion = null;
let interviewResponses = [];
let interviewActive = false;
let interviewInterval = null;
let interviewVoiceRecognition = null;
let interviewVoiceEnabled = false;
let interviewTranscript = "";
let interviewQuestionStartedAt = 0;
let interviewQuestionTotal = 0;

function getSpeechRecognition(){
return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

function renderDashboardInterviewQuestion(question, progress = 0, total = interviewQuestionTotal){
currentInterviewQuestion = question || null;
interviewQuestionTotal = total || interviewQuestionTotal;
const questionsContainer = document.getElementById("interviewQuestions");
if (questionsContainer) {
if (!question) {
questionsContainer.innerHTML = `
<div class="question-card current">
<div class="question-type">Complete</div>
<p class="question-text">Interview complete. Generate the final evaluation to review the result.</p>
</div>
`;
} else {
questionsContainer.innerHTML = `
<div class="question-card current">
<div class="question-type">Type: ${escapeHTML(question.type || "general")}</div>
<p class="question-text">${escapeHTML(question.question || "")}</p>
</div>
`;
}
}
const totalSafe = total || interviewQuestionTotal || 1;
const currentSafe = progress || (question ? interviewResponses.length + 1 : totalSafe);
document.getElementById("questionTypeMetric").textContent = question?.type || "Complete";
const progressPct = Math.max(0, Math.min(100, (currentSafe / totalSafe) * 100));
const progressFill = document.getElementById("interviewProgressFill");
const progressText = document.getElementById("interviewProgressText");
if (progressFill) progressFill.style.width = `${progressPct}%`;
if (progressText) progressText.textContent = `${Math.min(currentSafe, totalSafe)} of ${totalSafe} questions`;
const alertsContainer = document.getElementById("interviewAlerts");
if (alertsContainer && question?.follow_up) {
alertsContainer.innerHTML = `<div class="alert-item">Adaptive follow-up inserted to probe the previous answer more deeply.</div>`;
}
interviewQuestionStartedAt = Date.now();
}

function updateDashboardLiveTranscript(text){
interviewTranscript = text || "";
const transcript = document.getElementById("liveTranscript");
if (transcript) {
transcript.textContent = interviewTranscript || "Live transcript will appear here.";
}
}

function renderDashboardLiveSignals(data){
const answerScore = data.score != null ? data.score : data.evaluation?.score;
document.getElementById("liveScoreMetric").textContent = data.live_score != null ? `${data.live_score}/100` : "Awaiting signal";
document.getElementById("liveConfidenceMetric").textContent = data.confidence_level || "Awaiting signal";
document.getElementById("responseMetric").textContent = answerScore != null ? `${answerScore}/10` : "Awaiting answer";
const signalPanel = document.getElementById("liveSignals");
if (!signalPanel) return;

const goods = data.good_signals || [];
const risks = data.risk_signals || [];
const keywords = data.keywords_detected || [];
signalPanel.innerHTML = `
<div class="signal-good">${goods.length ? goods.map(escapeHTML).join(" | ") : "Positive signals will appear as the answer develops."}</div>
<div class="signal-risk">${risks.length ? risks.map(escapeHTML).join(" | ") : "No interview risk signal requires attention yet."}</div>
<div class="muted">${keywords.length ? `Keywords: ${keywords.map(escapeHTML).join(", ")}` : "Keywords will populate as the answer develops."}</div>
`;
}

async function refreshDashboardLiveScore(text){
if (!interviewActive || !currentInterviewQuestion || !text.trim()) {
return;
}

try {
const data = await apiInterviewRealtimeScore({
question: currentInterviewQuestion.question || "",
answer: text,
time_taken: interviewQuestionStartedAt ? (Date.now() - interviewQuestionStartedAt) / 1000 : 0,
question_type: currentInterviewQuestion.type || "technical"
});
renderDashboardLiveSignals(data);
} catch (err) {
console.warn("Live scoring recovered:", err);
}
}

async function startInterview() {
const candidateId = document.getElementById("interviewCandidateId").value.trim();
const sessionIdInput = document.getElementById("interviewSessionId").value.trim();
const candidate = matches.find(item => item.candidate_id === candidateId) || candidates.find(item => item.candidate_id === candidateId) || {};
const jobDescription = document.getElementById("matchJobDescription")?.value.trim() || "";

if (!candidateId) {
showAlert("Please enter a Candidate ID to start the interview.");
return;
}

try {
showLoader();
const data = await apiInterviewStart({
candidate_id: candidateId,
experience_level: "mid",
interview_type: "mixed",
job_description: jobDescription,
candidate_skills: candidate.skills || [],
resume_text: candidate.text_snippet || ""
});

interviewSessionId = sessionIdInput || data.session_id;
interviewResponses = [];
interviewActive = true;
interviewTranscript = "";
interviewQuestionTotal = data.total || 0;

document.getElementById("interviewSessionId").value = interviewSessionId;
document.getElementById("startInterviewBtn").disabled = true;
document.getElementById("endInterviewBtn").disabled = false;
document.getElementById("generateEvaluationBtn").disabled = true;
document.getElementById("submitResponseBtn").disabled = false;
document.getElementById("interviewResponse").value = "";
updateDashboardLiveTranscript("");
renderDashboardInterviewQuestion(data.question, data.progress, data.total);
showAlert("Interview started. Live intelligence is monitoring response quality.", "success");
} catch (err) {
showAlert("Failed to start interview: " + err.message);
} finally {
hideLoader();
}
}

async function endInterview(showCompletedAlert = true) {
interviewActive = false;
stopInterviewVoice();
stopInterviewVideo();

if (interviewInterval) {
clearInterval(interviewInterval);
interviewInterval = null;
}

document.getElementById("startInterviewBtn").disabled = false;
document.getElementById("endInterviewBtn").disabled = true;
document.getElementById("submitResponseBtn").disabled = true;
document.getElementById("generateEvaluationBtn").disabled = false;

if (showCompletedAlert) {
showAlert("Interview ended. Generate the final evaluation to review the result.", "success");
}
}

async function startInterviewVideo() {
const video = document.getElementById("interviewVideo");
const startBtn = document.getElementById("startVideoBtn");
const stopBtn = document.getElementById("stopVideoBtn");

if (!video) return;

try {
interviewStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
video.srcObject = interviewStream;
startBtn.disabled = true;
stopBtn.disabled = false;
updateFaceStatus("Camera active", "green");
interviewInterval = setInterval(processInterviewFrame, 2000);
} catch (err) {
updateFaceStatus("Camera access failed", "red");
showAlert("Unable to access webcam: " + err.message);
}
}

function stopInterviewVideo() {
if (interviewStream) {
interviewStream.getTracks().forEach(track => track.stop());
interviewStream = null;
}

const video = document.getElementById("interviewVideo");
if (video) video.srcObject = null;

document.getElementById("startVideoBtn").disabled = false;
document.getElementById("stopVideoBtn").disabled = true;
updateFaceStatus("Camera stopped", "gray");

if (interviewInterval) {
clearInterval(interviewInterval);
interviewInterval = null;
}
}

function updateFaceStatus(text, color) {
const statusDot = document.getElementById("faceStatusDot");
const statusText = document.getElementById("faceStatusText");

if (statusDot) statusDot.style.backgroundColor = color;
if (statusText) statusText.textContent = text;
}

async function processInterviewFrame() {
if (!interviewStream || !interviewActive) return;

const video = document.getElementById("interviewVideo");
if (!video || !video.videoWidth) return;

const canvas = document.createElement("canvas");
canvas.width = video.videoWidth;
canvas.height = video.videoHeight;
const ctx = canvas.getContext("2d");
ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
const imageBase64 = canvas.toDataURL("image/png");

try {
const [liveData, proctorData] = await Promise.all([
apiInterviewLive({
image_base64: imageBase64,
session_id: interviewSessionId,
current_question: currentInterviewQuestion?.question || "",
candidate_response: document.getElementById("interviewResponse")?.value || ""
}),
interviewSessionId && document.getElementById("interviewCandidateId")?.value.trim()
? apiInterviewProctor({
image_base64: imageBase64,
session_id: interviewSessionId,
candidate_id: document.getElementById("interviewCandidateId").value.trim()
}).catch(() => null)
: Promise.resolve(null)
]);

const data = liveData || {};
const proctor = proctorData || {};

const attention = proctor.attention_score ?? data.attention_score;
document.getElementById("attentionMetric").textContent = attention != null ? `${attention}%` : "Awaiting signal";
if (data.face_detected || !proctor.alerts?.includes("no_face")) {
updateFaceStatus(proctor.cheating_flag ? "Integrity alert" : "Face detected", proctor.cheating_flag ? "orange" : "green");
} else {
updateFaceStatus("No face detected", "red");
}

const mergedAlerts = [...(data.alerts || []), ...(proctor.alerts || [])];
const uniqueAlerts = [...new Set(mergedAlerts)];
const alertsContainer = document.getElementById("interviewAlerts");
if (alertsContainer && uniqueAlerts.length > 0) {
alertsContainer.innerHTML = uniqueAlerts.map(alert =>
`<div class="alert-item">${escapeHTML(alert)}</div>`
).join("");
} else if (alertsContainer) {
alertsContainer.innerHTML = "";
}
} catch (err) {
console.warn("Frame processing recovered:", err);
}
}

async function captureInterviewFrame(){
const video = document.getElementById("interviewVideo");
if(!video || !video.videoWidth){
throw new Error("Start the camera before verifying face.");
}

const canvas = document.createElement("canvas");
canvas.width = video.videoWidth;
canvas.height = video.videoHeight;
canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
return canvas.toDataURL("image/png");
}

async function verifyInterviewFace(){
try {
const imageBase64 = await captureInterviewFrame();
const candidateId = document.getElementById("interviewCandidateId")?.value.trim() || "";
const [faceData, proctorData] = await Promise.all([
apiFaceVerify({image_base64: imageBase64, candidate_id: candidateId}),
interviewSessionId && candidateId
? apiInterviewProctor({image_base64: imageBase64, session_id: interviewSessionId, candidate_id: candidateId}).catch(() => null)
: Promise.resolve(null)
]);
const verified = faceData.verified;
updateFaceStatus(verified ? "Face verified" : "Face not verified", verified ? "green" : "red");
if(proctorData?.attention_score != null){
document.getElementById("attentionMetric").textContent = `${proctorData.attention_score}%`;
}
showAlert(faceData.message || "Face verification complete.", verified ? "success" : "error");
} catch (err) {
showAlert(err.message || "Face verification failed.");
}
}

function startInterviewVoice() {
const Recognition = getSpeechRecognition();
if (!Recognition) {
showAlert("Speech recognition is not supported in this browser.");
return;
}

if (!interviewVoiceRecognition) {
interviewVoiceRecognition = new Recognition();
interviewVoiceRecognition.continuous = true;
interviewVoiceRecognition.interimResults = true;
interviewVoiceRecognition.lang = "en-US";

interviewVoiceRecognition.onresult = event => {
let transcript = "";
for (let index = 0; index < event.results.length; index += 1) {
transcript += event.results[index][0].transcript + " ";
}
transcript = transcript.trim();
document.getElementById("interviewResponse").value = transcript;
updateDashboardLiveTranscript(transcript);
refreshDashboardLiveScore(transcript);
};

interviewVoiceRecognition.onerror = event => {
interviewVoiceEnabled = false;
document.getElementById("startVoiceBtn").disabled = false;
document.getElementById("stopVoiceBtn").disabled = true;
showAlert("Voice input error: " + event.error);
};

interviewVoiceRecognition.onend = () => {
if (!interviewVoiceEnabled) {
document.getElementById("startVoiceBtn").disabled = false;
document.getElementById("stopVoiceBtn").disabled = true;
}
};
}

interviewVoiceEnabled = true;
document.getElementById("startVoiceBtn").disabled = true;
document.getElementById("stopVoiceBtn").disabled = false;
interviewVoiceRecognition.start();
}

function stopInterviewVoice() {
interviewVoiceEnabled = false;
if (interviewVoiceRecognition) {
interviewVoiceRecognition.stop();
}
const startBtn = document.getElementById("startVoiceBtn");
const stopBtn = document.getElementById("stopVoiceBtn");
if (startBtn) startBtn.disabled = false;
if (stopBtn) stopBtn.disabled = true;
}

async function submitResponse() {
const response = document.getElementById("interviewResponse").value.trim();
const candidateId = document.getElementById("interviewCandidateId").value.trim();
if (!response || !candidateId || !interviewSessionId || !currentInterviewQuestion) {
showAlert("Please provide an answer before submitting.");
return;
}

try {
showLoader();
const data = await apiInterviewAnswer({
session_id: interviewSessionId,
candidate_id: candidateId,
answer: response,
transcript: interviewTranscript || response,
time_taken: interviewQuestionStartedAt ? (Date.now() - interviewQuestionStartedAt) / 1000 : 0
});

interviewResponses.push({
question: currentInterviewQuestion.question,
response,
timestamp: new Date().toISOString(),
score: data.score,
feedback: data.feedback,
});
renderDashboardLiveSignals(data);
showAlert(data.feedback || "Answer evaluated.", "success");

if (data.completed) {
await endInterview(false);
if (data.result) {
renderDashboardFinalEvaluation(data.result);
document.getElementById("generateEvaluationBtn").disabled = false;
}
return;
}

document.getElementById("interviewResponse").value = "";
updateDashboardLiveTranscript("");
renderDashboardInterviewQuestion(data.next_question, data.progress, data.total);
} catch (err) {
showAlert("Failed to process response: " + err.message);
} finally {
hideLoader();
}
}

function renderDashboardFinalEvaluation(data){
const resultsContainer = document.getElementById("evaluationResults");
if (!resultsContainer) return;

const insights = data.insights || [];
const strengths = data.strengths || [];
const weaknesses = data.weaknesses || [];
resultsContainer.innerHTML = `
<div class="evaluation-summary">
<div class="final-score">
<h4>Final Score: ${escapeHTML(data.overall_score ?? "Awaiting score")}</h4>
<div class="recommendation ${escapeHTML((data.recommendation || "consider").toLowerCase())}">
${escapeHTML(data.recommendation || "consider")}
</div>
<div class="muted" style="margin-top:8px;">Decision: ${escapeHTML(data.decision_intelligence?.decision || "consider")} (${escapeHTML(data.decision_intelligence?.confidence ?? 0)}%)</div>
</div>
<div class="score-breakdown">
<h4>Score Breakdown</h4>
<div class="breakdown-grid">
<div>Technical: ${escapeHTML(data.technical_score ?? data.breakdown?.technical ?? 0)}</div>
<div>Communication: ${escapeHTML(data.communication_score ?? data.breakdown?.communication ?? 0)}</div>
<div>Behavioral: ${escapeHTML(data.breakdown?.behavioral ?? 0)}</div>
<div>Problem Solving: ${escapeHTML(data.breakdown?.problem_solving ?? 0)}</div>
<div>Attention: ${escapeHTML(data.proctor_summary?.attention_score ?? 100)}</div>
<div>Integrity: ${escapeHTML(data.proctor_summary?.proctor_score ?? 100)}</div>
</div>
</div>
<div class="insights">
<h4>Strengths</h4>
<ul>${strengths.map(item => `<li>${escapeHTML(item)}</li>`).join("") || "<li>No strengths captured.</li>"}</ul>
<h4>Weaknesses</h4>
<ul>${weaknesses.map(item => `<li>${escapeHTML(item)}</li>`).join("") || "<li>No weaknesses captured.</li>"}</ul>
<h4>Risk Flags</h4>
<ul>${(data.decision_intelligence?.risk_flags || []).map(flag => `<li>${escapeHTML(flag)}</li>`).join("") || "<li>Decision intelligence did not flag escalation risk.</li>"}</ul>
<h4>Key Insights</h4>
<ul>${insights.map(insight => `<li>${escapeHTML(insight)}</li>`).join("") || "<li>No additional insights.</li>"}</ul>
</div>
</div>
`;
}

async function generateEvaluation() {
if (!interviewSessionId) {
showAlert("Start an interview first.");
return;
}

try {
showLoader();
const data = await apiInterviewResult(interviewSessionId);
renderDashboardFinalEvaluation(data);
showAlert("Evaluation complete. Decision intelligence is ready for review.", "success");
} catch (err) {
showAlert("Failed to generate evaluation: " + err.message);
} finally {
hideLoader();
}
}

function exposeUIActions(){
const actions = {
switchView,
refreshCurrentView,
logout,
loadDemoEnvironment,
loadDashboardExecutiveBrief,
openExecutiveBrief,
createJob,
uploadResume,
runMatch,
runDecisionAI,
refreshAIFeedback,
openCommandPalette,
closeCommandPalette,
analyzeCandidate360,
generateExecutiveBrief,
saveDefaultAutomationRule,
runAutoShortlist,
startInterview,
endInterview,
startInterviewVideo,
verifyInterviewFace,
stopInterviewVideo,
startInterviewVoice,
stopInterviewVoice,
submitResponse,
generateEvaluation,
inviteTeamMember,
changeBillingPlan,
generateInvoice,
startMpesaPayment,
startProviderCheckout,
cancelSubscription,
loadCandidateIntelligence,
createAssessment,
createSchedule,
runBiasCheck,
connectSecurityStream,
loadSecurityCenter,
simulateSecurityEvent,
loadSecurityIncidents,
refreshSelectedSecurityIncident,
loadSecurityControls,
loadSecurityRules,
toggleChat,
useChatShortcut,
sendChatMessage,
closeModal,
toggleCompareCandidate,
clearComparison,
openCandidateModalFromMatch,
openCandidateModalById,
selectSecurityIncident,
markIncidentInvestigating,
containSelectedIncident,
resolveSelectedIncident,
executeContainmentAction,
loadSecurityRules,
toggleSecurityRule,
};
Object.entries(actions).forEach(([name, fn]) => {
if(typeof fn === "function"){
window[name] = fn;
}
});
}

exposeUIActions();
window.addEventListener("DOMContentLoaded", boot);
