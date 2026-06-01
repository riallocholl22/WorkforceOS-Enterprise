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
let ranking = matches.slice(0, 5);
if(!jobs.length && (summary.total_jobs ?? 0) > 0){
// Keep global job list in sync so onboarding cards behave consistently.
await loadJobs().catch(() => null);
}

if(requestToken && !isCurrentViewRequest(requestToken, "dashboard")) return;
document.getElementById("metricCandidates").innerText = summary.total_candidates ?? 0;
document.getElementById("metricJobs").innerText = summary.total_jobs ?? 0;
document.getElementById("metricTopScore").innerText = `${summary.top_score ?? 0}%`;
document.getElementById("metricReports").innerText = summary.total_reports ?? 0;
document.getElementById("metricMembers").innerText = summary.total_members ?? workspace?.totals?.members ?? 0;
renderDashboardRanking(ranking.slice(0, 5));
renderTopSkills(summary.top_skills || []);
renderMatchDistributionChart(ranking);
scheduleIdleTask(() => {
loadWorkforceOS()
.catch(() => null)
.finally(() => loadDashboardExecutiveBrief().catch(() => null));
}, 1800);
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
matchVisibleLimit = 36;
matches = (await apiMatchCandidates(description)).sort(
(a, b) => (b.match_score ?? b.score ?? 0) - (a.match_score ?? a.score ?? 0)
);
populateMatchSkillFilter(matches);
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

function pctValue(value, fallback = 0){
const num = Number(value);
if(!Number.isFinite(num)) return Math.max(0, Math.min(100, Number(fallback) || 0));
return Math.max(0, Math.min(100, num > 1 ? num : num * 100));
}

function matchScore(candidate){
return pctValue(candidate?.match_score ?? candidate?.score, 0);
}

function matchConfidence(candidate){
return pctValue(candidate?.confidence, Math.max(35, matchScore(candidate) - 8));
}

function predictionPct(candidate, key, fallback){
return pctValue(candidate?.predictions?.[key]?.value, fallback);
}

function candidateExperience(candidate){
const raw = candidate?.experience_years ?? candidate?.experience?.min ?? candidate?.experience ?? 0;
const num = Number(raw);
return Number.isFinite(num) ? num : 0;
}

function riskLevel(candidate){
const score = matchScore(candidate);
const missing = Array.isArray(candidate?.missing_skills) ? candidate.missing_skills.length : 0;
const risk = predictionPct(candidate, "onboarding_risk", Math.max(8, 58 - score + missing * 6));
if(risk >= 62 || missing >= 6) return "high";
if(risk >= 34 || missing >= 3) return "medium";
return "low";
}

function fitSignals(candidate){
const score = matchScore(candidate);
const confidence = matchConfidence(candidate);
const skill = pctValue(candidate?.skill_score ?? candidate?.skill_breakdown?.skill_score, score * 0.86);
const experience = pctValue(candidate?.experience_score, Math.min(100, 42 + candidateExperience(candidate) * 8));
const education = pctValue(candidate?.education_fit, Math.max(48, Math.min(94, score - (candidate?.missing_skills?.length || 0) * 3 + 8)));
const behavioral = pctValue(candidate?.behavioral_fit, predictionPct(candidate, "offer_acceptance_probability", Math.max(45, score - 4)));
const culture = pctValue(candidate?.culture_fit, Math.max(50, (confidence * 0.58) + (behavioral * 0.42)));
const salary = pctValue(candidate?.salary_alignment, Math.max(45, Math.min(96, 100 - Math.max(0, candidateExperience(candidate) - 8) * 4 - (candidate?.missing_skills?.length || 0) * 2)));
const readiness = pctValue(candidate?.interview_readiness, predictionPct(candidate, "interview_success_probability", Math.max(42, (score * 0.62) + (confidence * 0.30))));
return {score, confidence, skill, experience, education, behavioral, culture, salary, readiness};
}

function signalBar(label, value){
const pct = Math.round(pctValue(value));
return `<div class="match-signal"><span>${escapeHTML(label)}</span><div class="signal-track"><i style="width:${pct}%"></i></div><strong>${pct}%</strong></div>`;
}

function fitRing(label, value, tone = ""){
const pct = Math.round(pctValue(value));
return `<div class="fit-ring ${tone}" style="--pct:${pct}"><span>${pct}%</span><small>${escapeHTML(label)}</small></div>`;
}

function radarChart(signals){
const points = [signals.skill, signals.experience, signals.education, signals.behavioral, signals.culture, signals.salary].map(value => Math.round(pctValue(value)));
return `<div class="fit-radar" style="--a:${points[0]}%;--b:${points[1]}%;--c:${points[2]}%;--d:${points[3]}%;--e:${points[4]}%;--f:${points[5]}%"><span>Skills</span><span>Exp</span><span>Edu</span><span>Beh</span><span>Culture</span><span>Salary</span></div>`;
}

function hiringRecommendation(candidate, signals){
if(candidate?.recommendation) return candidate.recommendation;
if(signals.score >= 85 && signals.confidence >= 75) return "Strong Match";
if(signals.score >= 72) return "Recommended";
if(signals.score >= 55) return "Potential Match";
return "Needs Review";
}

function aiReasoning(candidate, signals){
return candidate?.recruiter_summary
|| candidate?.recommendation_reason
|| candidate?.explanation
|| `Candidate shows ${Math.round(signals.skill)}% skills compatibility and ${Math.round(signals.experience)}% experience alignment with ${Math.round(signals.confidence)}% AI confidence.`;
}

function operationalReasoning(candidate, signals){
const missing = (candidate?.missing_skills || []).slice(0, 3);
if(missing.length) return `Advance with focused validation on ${missing.join(", ")}. Interview readiness is ${Math.round(signals.readiness)}% and risk is ${riskLevel(candidate)}.`;
return `Operationally ready for recruiter review with strong skill coverage, low visible gap pressure, and ${Math.round(signals.readiness)}% interview readiness.`;
}

function textForMatchSearch(candidate){
return [
candidatePrimaryLabel(candidate),
candidate?.candidate_id,
candidate?.role,
candidate?.location,
candidate?.recommendation,
candidate?.recruiter_summary,
candidate?.recommendation_reason,
...(candidate?.skills || []),
...(candidate?.matched_skills || []),
...(candidate?.missing_skills || []),
...(candidate?.strengths || []),
...(candidate?.risks || [])
].filter(Boolean).join(" ").toLowerCase();
}

function filteredMatchResults(data){
const search = (matchFilters.search || "").toLowerCase().trim();
const location = (matchFilters.location || "").toLowerCase().trim();
let out = [...(data || [])].filter(candidate => {
const signals = fitSignals(candidate);
if(search && !textForMatchSearch(candidate).includes(search)) return false;
if(location && !String(candidate?.location || candidate?.remote_preference || "").toLowerCase().includes(location)) return false;
if(matchFilters.skill){
const skills = [...(candidate?.skills || []), ...(candidate?.matched_skills || [])].map(skill => String(skill).toLowerCase());
if(!skills.includes(matchFilters.skill.toLowerCase())) return false;
}
if(matchFilters.education === "strong" && signals.education < 72) return false;
if(matchFilters.education === "review" && signals.education >= 72) return false;
if(matchFilters.risk && riskLevel(candidate) !== matchFilters.risk) return false;
if(matchFilters.availability === "ready" && signals.readiness < 70) return false;
if(matchFilters.availability === "review" && signals.readiness >= 70) return false;
return true;
});
const sort = matchFilters.sort || "score";
out.sort((a, b) => {
const as = fitSignals(a);
const bs = fitSignals(b);
if(sort === "confidence") return bs.confidence - as.confidence;
if(sort === "experience") return bs.experience - as.experience;
if(sort === "salary") return bs.salary - as.salary;
if(sort === "readiness") return bs.readiness - as.readiness;
return bs.score - as.score;
});
return out;
}

function populateMatchSkillFilter(data){
const select = document.getElementById("matchSkillFilter");
if(!select) return;
const current = select.value || "";
const skills = [...new Set((data || []).flatMap(candidate => [...(candidate.skills || []), ...(candidate.matched_skills || [])]).filter(Boolean).map(String))]
.sort((a, b) => a.localeCompare(b))
.slice(0, 80);
select.innerHTML = `<option value="">All skills</option>${skills.map(skill => `<option value="${escapeHTML(skill)}">${escapeHTML(skill)}</option>`).join("")}`;
if(skills.includes(current)) select.value = current;
}

function updateMatchFilters(){
matchFilters = {
search: document.getElementById("matchSearchInput")?.value || "",
sort: document.getElementById("matchSortSelect")?.value || "score",
skill: document.getElementById("matchSkillFilter")?.value || "",
education: document.getElementById("matchEducationFilter")?.value || "",
risk: document.getElementById("matchRiskFilter")?.value || "",
location: document.getElementById("matchLocationFilter")?.value || "",
availability: document.getElementById("matchAvailabilityFilter")?.value || ""
};
matchVisibleLimit = 36;
renderMatchResults(matches);
}

function loadMoreMatches(){
matchVisibleLimit = Math.min(MAX_TABLE_ROWS, matchVisibleLimit + 36);
renderMatchResults(matches);
}

function exportCandidateProfile(candidateId){
const candidate = matches.find(item => item.candidate_id === candidateId) || candidates.find(item => item.candidate_id === candidateId);
if(!candidate){
showAlert("Candidate profile is not available for export.", "info");
return;
}
const payload = {
exported_at: new Date().toISOString(),
candidate_id: candidate.candidate_id,
candidate_name: candidatePrimaryLabel(candidate),
match_score: matchScore(candidate),
confidence: matchConfidence(candidate),
recommendation: hiringRecommendation(candidate, fitSignals(candidate)),
skills: candidate.skills || [],
matched_skills: candidate.matched_skills || [],
missing_skills: candidate.missing_skills || [],
strengths: candidate.strengths || [],
risks: candidate.risks || [],
ai_reasoning: aiReasoning(candidate, fitSignals(candidate)),
next_operating_step: candidate.next_operating_step || operationalReasoning(candidate, fitSignals(candidate))
};
const blob = new Blob([JSON.stringify(payload, null, 2)], {type: "application/json"});
const url = URL.createObjectURL(blob);
const a = document.createElement("a");
a.href = url;
a.download = `${String(candidateId || "candidate").replace(/[^a-z0-9_-]/gi, "_")}_match_profile.json`;
document.body.appendChild(a);
a.click();
a.remove();
URL.revokeObjectURL(url);
showAlert("Candidate profile exported.", "success");
}

function assignRecruiter(candidateId){
showAlert(`Recruiter assignment queued for ${candidateId}. Team ownership will sync with operational notes.`, "success");
recordObservabilityEvent("recruiter", "Recruiter assignment queued", `Candidate ${candidateId} is ready for owner assignment.`, "info");
}

function addOperationalNote(candidateId){
showAlert(`Operational note opened for ${candidateId}. Use shortlist notes or Candidate 360 for persistent review context.`, "info");
recordObservabilityEvent("note", "Operational note", `Recruiter note intent captured for candidate ${candidateId}.`, "info");
}

function renderMatchInsights(data, visible){
const container = document.getElementById("matchInsights");
if(!container) return;
if(!data.length){
container.innerHTML = `<div class="match-empty-telemetry"><span class="live-dot"></span><strong>No candidates analyzed yet.</strong><p class="muted">Upload resumes to begin AI workforce matching. Telemetry, confidence, and readiness signals will populate here.</p></div>`;
return;
}
const scores = data.map(matchScore);
const avg = Math.round(scores.reduce((sum, value) => sum + value, 0) / Math.max(1, scores.length));
const confidence = Math.round(data.map(matchConfidence).reduce((sum, value) => sum + value, 0) / Math.max(1, data.length));
const riskCounts = data.reduce((acc, candidate) => {
acc[riskLevel(candidate)] += 1;
return acc;
}, {low: 0, medium: 0, high: 0});
const shortages = [...new Set(data.flatMap(candidate => candidate.missing_skills || []))].slice(0, 5);
const pools = [...new Set(data.flatMap(candidate => candidate.matched_skills || candidate.skills || []))].slice(0, 5);
const readiness = Math.round(data.map(candidate => fitSignals(candidate).readiness).reduce((sum, value) => sum + value, 0) / Math.max(1, data.length));
container.innerHTML = `
<div class="match-insight-grid">
<article><span>Total analyzed</span><strong>${escapeHTML(data.length)}</strong><small>${escapeHTML(visible.length)} in current view</small></article>
<article><span>Avg match quality</span><strong>${escapeHTML(avg)}%</strong><small>Score-weighted ranking</small></article>
<article><span>AI confidence</span><strong>${escapeHTML(confidence)}%</strong><small>Evidence and requirement clarity</small></article>
<article><span>Workforce readiness</span><strong>${escapeHTML(readiness)}%</strong><small>Interview-ready operating signal</small></article>
<article><span>Risk distribution</span><strong>${escapeHTML(riskCounts.low)}/${escapeHTML(riskCounts.medium)}/${escapeHTML(riskCounts.high)}</strong><small>Low / medium / high</small></article>
<article><span>Hiring velocity</span><strong>${escapeHTML(Math.max(12, Math.min(96, data.length * 8 + shortlist.size * 9)))}%</strong><small>Upload-to-shortlist momentum</small></article>
</div>
<div class="match-intel-strip">
<span><b>Strongest pools</b>${escapeHTML(pools.join(", ") || "Collecting skill evidence")}</span>
<span><b>Skill shortages</b>${escapeHTML(shortages.join(", ") || "No critical shortage detected")}</span>
<span><b>Diversity metrics</b>Evidence-aware review ready; demographic scoring is intentionally excluded.</span>
</div>
`;
}

function renderMatchResults(data){
const container = document.getElementById("matchResults");
if(!container) return;
populateMatchSkillFilter(data);

if(!data.length){
renderMatchInsights([], []);
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

const filtered = filteredMatchResults(data);
renderMatchInsights(data, filtered);
const visible = filtered.slice(0, Math.min(matchVisibleLimit, MAX_TABLE_ROWS));
const overflow = filtered.length > visible.length
? `<button class="secondary-btn match-load-more" onclick="loadMoreMatches()">Load more candidates (${filtered.length - visible.length} remaining)</button>`
: "";

container.innerHTML = `
<div class="match-list">
${visible.map((candidate, localIndex) => {
const index = matches.findIndex(item => item.candidate_id === candidate.candidate_id);
const rank = index >= 0 ? index + 1 : localIndex + 1;
const signals = fitSignals(candidate);
const score = signals.score;
const status = scoreStatus(score);
const isShortlisted = shortlist.has(candidate.candidate_id);
const interviewPct = Math.round(predictionPct(candidate, "interview_success_probability", signals.readiness));
const riskPct = Math.round(predictionPct(candidate, "onboarding_risk", riskLevel(candidate) === "high" ? 68 : riskLevel(candidate) === "medium" ? 42 : 18));
const offerPct = Math.round(predictionPct(candidate, "offer_acceptance_probability", Math.max(40, signals.score - 5)));
const recommendation = hiringRecommendation(candidate, signals);
const missing = candidate.missing_skills || [];
const matched = candidate.matched_skills || candidate.skills || [];
const risk = riskLevel(candidate);
const confidenceClass = signals.confidence >= 78 ? "ring-high" : signals.confidence >= 55 ? "ring-mid" : "ring-low";
return `
<article class="match-card match-card-pro ${rank === 1 ? "highlight" : ""} ${isShortlisted ? "shortlisted-row" : ""}">
<div class="match-main">
<div class="match-card-head">
<div>
<div class="match-rank">Rank #${rank}</div>
<h4>${escapeHTML(candidatePrimaryLabel(candidate))}</h4>
${candidateSecondaryLabel(candidate) ? `<div class="muted small">${escapeHTML(candidateSecondaryLabel(candidate))}</div>` : ""}
</div>
${fitRing("Match", signals.score, "primary-ring")}
</div>
<div class="reco-row">
<span class="reco-pill ${escapeHTML((recommendation || '').toLowerCase().replaceAll(' ', '-')) || 'needs-review'}">
${escapeHTML(recommendation)}
</span>
${Array.isArray(candidate.insight_badges) ? candidate.insight_badges.slice(0, 3).map(b => `<span class="badge-pill">${escapeHTML(b)}</span>`).join("") : ""}
<span class="badge-pill risk-${escapeHTML(risk)}">${escapeHTML(risk)} risk</span>
</div>
<p class="match-summary">${escapeHTML(aiReasoning(candidate, signals))}</p>
<div class="match-visual-grid">
<div class="match-breakdown">
${signalBar("Skills compatibility", signals.skill)}
${signalBar("Experience alignment", signals.experience)}
${signalBar("Education fit", signals.education)}
${signalBar("Behavioral fit", signals.behavioral)}
${signalBar("Culture fit", signals.culture)}
${signalBar("Salary alignment", signals.salary)}
</div>
${radarChart(signals)}
</div>
<div class="skill-overlap">
<strong>Skill overlap</strong>
<div class="skill-cloud">
${matched.slice(0, 8).map(skill => `<span>${escapeHTML(skill)}</span>`).join("") || `<span class="muted">No explicit overlapping skills yet</span>`}
</div>
<div class="skill-cloud">
${missing.slice(0, 6).map(skill => `<span class="status-pill status-low">${escapeHTML(skill)}</span>`).join("")
|| (candidate.required_skills?.length ? `<span class="muted">No major gaps detected from the job must-haves</span>` : `<span class="muted">Job description did not list clear must-have skills</span>`)}
</div>
</div>
<div class="explain-panel ai-reasoning-panel">
<strong>AI reasoning</strong>
<p>${escapeHTML(operationalReasoning(candidate, signals))}</p>
<div class="match-reason-grid">
<span><b>Confidence analysis</b>${escapeHTML(Math.round(signals.confidence))}% based on similarity, skill coverage, experience, and evidence quality.</span>
<span><b>Interview prediction</b>${escapeHTML(interviewPct)}% expected screen readiness.</span>
<span><b>Likely performance</b>${escapeHTML(Math.round((signals.skill * .42) + (signals.experience * .34) + (signals.behavioral * .24)))}% directional role-performance signal.</span>
<span><b>Leadership indicators</b>${escapeHTML((candidate.insight_badges || []).includes("Leadership Signal") ? "Visible leadership signal" : candidate.seniority_estimate || "No explicit leadership signal")}</span>
<span><b>Communication analysis</b>${escapeHTML((candidate.risks || []).some(item => /communication/i.test(item)) ? "Needs interview validation" : "No communication risk flagged")}</span>
<span><b>Technical depth</b>${escapeHTML(Math.round((signals.skill * .7) + (signals.experience * .3)))}% inferred from matched skills and role evidence.</span>
</div>
${(Array.isArray(candidate.strengths) && candidate.strengths.length) ? `<div class="strength-strip"><strong>Strengths</strong><p>${escapeHTML(candidate.strengths.slice(0, 3).join(" "))}</p></div>` : ""}
${(Array.isArray(candidate.risks) && candidate.risks.length) ? `<div class="risk-strip"><strong>Risk indicators</strong><p>${escapeHTML(candidate.risks.slice(0, 3).join(" "))}</p></div>` : ""}
</div>
</div>
<div class="match-score">
${scoreBar(score)}
<div class="confidence-row">
<div class="confidence-ring ${confidenceClass}" style="--pct:${Math.round(signals.confidence)}">
<span>${Math.round(signals.confidence)}%</span>
</div>
<div>
<div class="muted">AI confidence</div>
<strong>${Math.round(signals.confidence)}%</strong>
</div>
</div>
<div class="readiness-gauge">
${fitRing("Interview readiness", signals.readiness)}
<span class="badge-pill">Offer ${escapeHTML(offerPct)}%</span>
<span class="badge-pill">Onboarding risk ${escapeHTML(riskPct)}%</span>
</div>
<span class="status-pill ${status.className}">${status.label}</span>
<button onclick="openCandidateModalFromMatch(${Math.max(0, index)})">Full Analysis</button>
<button class="secondary-btn" onclick="toggleCompareCandidate('${escapeHTML(candidate.candidate_id)}')">
${compareCandidateIds.has(String(candidate.candidate_id)) ? "Comparing" : "Compare"}
</button>
<button class="secondary-btn" onclick="startCandidateInterview('${escapeHTML(candidate.candidate_id)}')">Interview</button>
<button class="secondary-btn" onclick="toggleShortlist('${escapeHTML(candidate.candidate_id)}')">
${isShortlisted ? "Shortlisted" : "Shortlist"}
</button>
<button class="secondary-btn" onclick="exportCandidateProfile('${escapeHTML(candidate.candidate_id)}')">Export</button>
<button class="secondary-btn" onclick="assignRecruiter('${escapeHTML(candidate.candidate_id)}')">Assign</button>
<button class="secondary-btn" onclick="addOperationalNote('${escapeHTML(candidate.candidate_id)}')">Notes</button>
<button class="secondary-btn" onclick="approveCandidateFromMatch('${escapeHTML(candidate.candidate_id)}')">Approve</button>
<button class="danger-btn" onclick="rejectCandidateFromMatch('${escapeHTML(candidate.candidate_id)}')">Reject</button>
</div>
</article>
`;
}).join("")}
${overflow}
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
let interviewFrameBusy = false;
let interviewFrameDelayMs = 6000;
let interviewFrameFailures = 0;
let lastInterviewFrameHash = "";
let lastInterviewFrameAt = 0;
let lastInterviewAlertKey = "";
let lastInterviewAlertAt = 0;
let interviewStartBusy = false;
let interviewSubmitBusy = false;
let dashboardLiveScoreTimer = null;
let dashboardLiveScoreBusy = false;
let dashboardLastLiveScoreAt = 0;
let dashboardLastLiveScoreText = "";
let dashboardCameraStarting = false;
let dashboardPreviewDetectTimer = null;

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
const normalized = String(text || "").trim();
if(normalized.length < 16) return;
if(dashboardLiveScoreTimer) clearTimeout(dashboardLiveScoreTimer);
dashboardLiveScoreTimer = setTimeout(() => runDashboardLiveScore(normalized), WORKFORCE_PERF.lowPower ? 1600 : 850);
}

async function runDashboardLiveScore(text){
if (!interviewActive || !currentInterviewQuestion || !text.trim()) return;
const now = Date.now();
const changedEnough = Math.abs(text.length - dashboardLastLiveScoreText.length) >= 24;
if(dashboardLiveScoreBusy || (!changedEnough && now - dashboardLastLiveScoreAt < 7000)) return;
try {
dashboardLiveScoreBusy = true;
dashboardLastLiveScoreAt = now;
dashboardLastLiveScoreText = text;
const data = await apiInterviewRealtimeScore({
question: currentInterviewQuestion.question || "",
answer: text,
time_taken: interviewQuestionStartedAt ? (Date.now() - interviewQuestionStartedAt) / 1000 : 0,
question_type: currentInterviewQuestion.type || "technical"
});
renderDashboardLiveSignals(data);
} catch (err) {
console.warn("Live scoring recovered:", err);
} finally {
dashboardLiveScoreBusy = false;
}
}

async function startInterview() {
if(interviewStartBusy) return;
const candidateId = document.getElementById("interviewCandidateId").value.trim();
const candidate = matches.find(item => item.candidate_id === candidateId) || candidates.find(item => item.candidate_id === candidateId) || {};
const jobDescription = document.getElementById("matchJobDescription")?.value.trim() || "";

if (!candidateId) {
showAlert("Please enter a Candidate ID to start the interview.");
return;
}

try {
interviewStartBusy = true;
showLoader();
const data = await apiInterviewStart({
candidate_id: candidateId,
experience_level: "mid",
interview_type: "mixed",
job_description: jobDescription,
candidate_skills: candidate.skills || [],
resume_text: candidate.text_snippet || ""
});

interviewSessionId = data.session_id;
interviewResponses = [];
interviewActive = true;
interviewTranscript = "";
interviewQuestionTotal = data.total || 0;
interviewFrameDelayMs = WORKFORCE_PERF.lowPower ? 12000 : 8500;
interviewFrameFailures = 0;
lastInterviewFrameHash = "";
lastInterviewFrameAt = 0;
dashboardLastLiveScoreAt = 0;
dashboardLastLiveScoreText = "";

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
interviewStartBusy = false;
hideLoader();
}
}

async function endInterview(showCompletedAlert = true) {
interviewActive = false;
stopInterviewVoice();
stopInterviewVideo();

if (interviewInterval) {
clearTimeout(interviewInterval);
interviewInterval = null;
}
if (dashboardLiveScoreTimer) {
clearTimeout(dashboardLiveScoreTimer);
dashboardLiveScoreTimer = null;
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

if (!video) {
showAlert("Camera preview is not ready. Refresh the interview view and try again.", "info");
return;
}
if (!navigator.mediaDevices?.getUserMedia) {
setDashboardCameraState("unavailable", "Camera unavailable", "Use a secure browser with camera support.");
showAlert("Camera is unavailable in this browser. Use a secure browser with camera support.", "info");
return;
}
if (location.protocol !== "https:" && !["localhost", "127.0.0.1"].includes(location.hostname)) {
setDashboardCameraState("blocked", "Camera blocked", "Camera requires HTTPS or localhost.");
showAlert("Camera permission requires a secure connection. Open WorkforceOS over HTTPS.", "info");
return;
}
if (dashboardCameraStarting) return;
if (interviewStream && interviewStream.getVideoTracks().some(track => track.readyState === "live")) {
await attachInterviewStream(video, interviewStream);
setDashboardCameraState("active", "Camera active", "Live preview is running.");
scheduleInterviewFrame(900);
return;
}

try {
dashboardCameraStarting = true;
setDashboardCameraState("requesting", "Requesting permission", "Your browser will ask for camera access.");
if(startBtn) startBtn.disabled = true;
if(stopBtn) stopBtn.disabled = true;
if(interviewStream) stopInterviewVideo(false);
interviewStream = await navigator.mediaDevices.getUserMedia({
video: {
width: {ideal: WORKFORCE_PERF.lowPower ? 320 : 480},
height: {ideal: WORKFORCE_PERF.lowPower ? 240 : 360},
frameRate: {ideal: WORKFORCE_PERF.lowPower ? 10 : 15, max: WORKFORCE_PERF.lowPower ? 12 : 18}
},
audio: false
});
await attachInterviewStream(video, interviewStream);
interviewStream.getVideoTracks().forEach(track => {
track.onended = () => {
if(interviewStream === video.srcObject) stopInterviewVideo();
};
});
if(startBtn) startBtn.disabled = true;
if(stopBtn) stopBtn.disabled = false;
setDashboardCameraState("detecting", "Detecting face", "Center your face in the guide.");
if(!interviewSessionId) scheduleDashboardPassiveFaceCheck(700);
scheduleInterviewFrame(900);
} catch (err) {
const message = cameraFriendlyMessage(err);
const state = /permission|blocked/i.test(message) ? "blocked" : "unavailable";
setDashboardCameraState(state, state === "blocked" ? "Camera blocked" : "Camera unavailable", message);
showAlert(message, "info");
if(startBtn) startBtn.disabled = false;
if(stopBtn) stopBtn.disabled = true;
} finally {
dashboardCameraStarting = false;
}
}

async function attachInterviewStream(video, stream) {
video.muted = true;
video.autoplay = true;
video.playsInline = true;
video.setAttribute("muted", "");
video.setAttribute("autoplay", "");
video.setAttribute("playsinline", "");
video.srcObject = stream;
video.style.display = "block";
await new Promise(resolve => {
if(video.readyState >= 2 && video.videoWidth) return resolve();
const done = () => resolve();
video.onloadedmetadata = done;
setTimeout(done, 1800);
});
try {
await video.play();
} catch {
await new Promise(resolve => setTimeout(resolve, 250));
try { await video.play(); } catch {}
}
setDashboardCameraState("active", "Camera active", "Live preview is running.");
}

function stopInterviewVideo(showStopped = true) {
if (interviewStream) {
interviewStream.getTracks().forEach(track => { track.onended = null; });
interviewStream.getTracks().forEach(track => track.stop());
interviewStream = null;
}

const video = document.getElementById("interviewVideo");
if (video) {
video.pause();
video.srcObject = null;
video.removeAttribute("src");
video.load?.();
}

document.getElementById("startVideoBtn").disabled = false;
document.getElementById("stopVideoBtn").disabled = true;
if(showStopped) setDashboardCameraState("stopped", "Camera stopped", "Start camera to resume the live preview.");

if (interviewInterval) {
clearTimeout(interviewInterval);
interviewInterval = null;
}
if (dashboardPreviewDetectTimer) {
clearTimeout(dashboardPreviewDetectTimer);
dashboardPreviewDetectTimer = null;
}
}

function scheduleInterviewFrame(delayMs = interviewFrameDelayMs) {
if (interviewInterval) clearTimeout(interviewInterval);
if (!interviewActive || !interviewStream) return;
if(WORKFORCE_TEST_MODE) return;
interviewInterval = setTimeout(processInterviewFrame, Math.max(2500, Number(delayMs) || interviewFrameDelayMs));
}

function captureCompressedInterviewFrame(video) {
const canvas = document.createElement("canvas");
const maxWidth = WORKFORCE_PERF.lowPower ? 240 : 320;
const scale = Math.min(1, maxWidth / Math.max(1, video.videoWidth));
canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
return canvas.toDataURL("image/jpeg", WORKFORCE_PERF.lowPower ? 0.42 : 0.50);
}

function updateFaceStatus(text, color) {
const statusDot = document.getElementById("faceStatusDot");
const statusText = document.getElementById("faceStatusText");

if (statusDot) statusDot.style.backgroundColor = color;
if (statusText) statusText.textContent = text;
}

function setDashboardCameraState(state, label, detail = "") {
const shell = document.getElementById("dashboardCameraShell");
const health = document.getElementById("cameraHealthText");
const confidence = document.getElementById("cameraConfidenceText");
if(shell) shell.dataset.cameraState = state || "idle";
const colorMap = {
idle: "gray",
requesting: "#FBBF24",
active: "#22C55E",
detecting: "#38BDF8",
face: "#22C55E",
"low-light": "#F59E0B",
blocked: "#EF4444",
unavailable: "#EF4444",
stopped: "gray"
};
updateFaceStatus(label || "Camera idle", colorMap[state] || "gray");
if(health) health.textContent = label || "Camera idle";
if(confidence && detail) confidence.textContent = detail;
}

function cameraFriendlyMessage(error) {
const name = String(error?.name || "");
const raw = String(error?.message || error || "");
if(/notallowed|permission|denied/i.test(`${name} ${raw}`)) return "Camera permission is required to begin a proctored interview.";
if(/notfound|devicesnotfound/i.test(`${name} ${raw}`)) return "Camera unavailable. Connect a webcam and try again.";
if(/notreadable|trackstart|in use/i.test(`${name} ${raw}`)) return "Camera is already in use by another app. Close the other app and try again.";
if(/security|insecure/i.test(`${name} ${raw}`)) return "Camera requires a secure browser session. Open WorkforceOS over HTTPS.";
return "Camera could not start. Check browser permissions and try again.";
}

function scheduleDashboardPassiveFaceCheck(delayMs = 800) {
if(dashboardPreviewDetectTimer) clearTimeout(dashboardPreviewDetectTimer);
dashboardPreviewDetectTimer = setTimeout(runDashboardPassiveFaceCheck, Math.max(500, Number(delayMs) || 800));
}

async function runDashboardPassiveFaceCheck() {
dashboardPreviewDetectTimer = null;
if(!interviewStream || interviewSessionId) return;
try {
const imageBase64 = await captureInterviewFrame();
const candidateId = document.getElementById("interviewCandidateId")?.value.trim() || "";
const data = await apiFaceVerify({image_base64: imageBase64, candidate_id: candidateId});
if(data?.verified){
setDashboardCameraState("face", "Face detected", "Confidence strong");
} else {
setDashboardCameraState("detecting", "Detecting face", "No face detected yet. Center your face in the frame.");
scheduleDashboardPassiveFaceCheck(6000);
}
} catch {
if(interviewStream && !interviewSessionId) scheduleDashboardPassiveFaceCheck(7000);
}
}

async function processInterviewFrame() {
if (!interviewStream || !interviewActive) return;
if (interviewFrameBusy) {
scheduleInterviewFrame(Math.min(18000, interviewFrameDelayMs + 1000));
return;
}
if (document.visibilityState === "hidden") {
scheduleInterviewFrame(18000);
return;
}

const video = document.getElementById("interviewVideo");
if (!video || !video.videoWidth) {
scheduleInterviewFrame(2500);
return;
}

const imageBase64 = captureCompressedInterviewFrame(video);
const frameHash = imageBase64.slice(0, 180) + imageBase64.slice(-180);
const now = Date.now();
if(frameHash === lastInterviewFrameHash && now - lastInterviewFrameAt < 10000){
scheduleInterviewFrame(Math.min(18000, interviewFrameDelayMs + 2500));
return;
}
lastInterviewFrameHash = frameHash;
lastInterviewFrameAt = now;

try {
interviewFrameBusy = true;
const proctorData = interviewSessionId && document.getElementById("interviewCandidateId")?.value.trim()
? await apiInterviewProctor({
image_base64: imageBase64,
session_id: interviewSessionId,
candidate_id: document.getElementById("interviewCandidateId").value.trim()
}).catch(() => null)
: null;

let liveData = null;
const responseText = document.getElementById("interviewResponse")?.value || "";
if(responseText.trim().length >= 18 && !proctorData?.throttled){
liveData = await apiInterviewLive({
image_base64: imageBase64,
session_id: interviewSessionId,
current_question: currentInterviewQuestion?.question || "",
candidate_response: responseText
}).catch(() => null);
}

const data = liveData || {};
const proctor = proctorData || {};
interviewFrameFailures = 0;

const attention = proctor.attention_score ?? data.attention_score;
document.getElementById("attentionMetric").textContent = attention != null ? `${attention}%` : "Awaiting signal";
const visibility = Number(proctor.visibility_score ?? 0) || 0;
const lighting = Number(proctor.lighting_score ?? 0) || 0;
const rawAlerts = [...(proctor.raw_alerts || []), ...(proctor.alerts || [])];
if(rawAlerts.includes("low_light") || rawAlerts.includes("dim_light")){
setDashboardCameraState("low-light", "Low light detected", "Adjust lighting for stronger face confidence.");
} else if (data.face_detected || (proctor.face_count && !proctor.alerts?.includes("no_face"))) {
setDashboardCameraState("face", proctor.cheating_flag ? "Integrity alert" : "Face detected", `Confidence ${Math.round(visibility || attention || 0)}%`);
} else {
setDashboardCameraState("detecting", "Detecting face", "No face detected yet. Center your face in the frame.");
}

const mergedAlerts = [...(data.alerts || []), ...(proctor.alerts || [])];
const uniqueAlerts = [...new Set(mergedAlerts)];
const alertsContainer = document.getElementById("interviewAlerts");
const alertKey = uniqueAlerts.slice(0, 3).join("|");
const alertNow = Date.now();
if (alertsContainer && uniqueAlerts.length > 0 && (alertKey !== lastInterviewAlertKey || alertNow - lastInterviewAlertAt > 15000)) {
lastInterviewAlertKey = alertKey;
lastInterviewAlertAt = alertNow;
alertsContainer.innerHTML = uniqueAlerts.map(alert =>
`<div class="alert-item">${escapeHTML(alert)}</div>`
).join("");
} else if (alertsContainer) {
alertsContainer.innerHTML = "";
}
interviewFrameDelayMs = proctorData?.throttled
? Math.min(22000, Number(proctorData.retry_after_seconds || proctorData.next_sample_seconds || 8) * 1000)
: uniqueAlerts.some(alert => ["no_face", "multiple_faces"].includes(String(alert)))
? 6500
: Number(proctorData?.next_sample_seconds || 9) * 1000;
} catch (err) {
interviewFrameFailures += 1;
interviewFrameDelayMs = Math.min(18000, 6000 + interviewFrameFailures * 3500);
} finally {
interviewFrameBusy = false;
scheduleInterviewFrame(interviewFrameDelayMs);
}
}

async function captureInterviewFrame(){
const video = document.getElementById("interviewVideo");
if(!video || !video.videoWidth){
throw new Error("Start the camera before verifying face.");
}

return captureCompressedInterviewFrame(video);
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
showAlert("No face detected yet. Center your face in the frame and try verification again.", "info");
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
if(interviewSubmitBusy) return;
const response = document.getElementById("interviewResponse").value.trim();
const candidateId = document.getElementById("interviewCandidateId").value.trim();
if (!response || !candidateId || !interviewSessionId || !currentInterviewQuestion) {
showAlert("Please provide an answer before submitting.");
return;
}

try {
interviewSubmitBusy = true;
document.getElementById("submitResponseBtn").disabled = true;
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
interviewSubmitBusy = false;
if(interviewActive) document.getElementById("submitResponseBtn").disabled = false;
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

function productIntelligenceKpi(label, value, detail = "", tone = "average"){
return `
<article class="product-kpi ${escapeHTML(tone)}">
<span>${escapeHTML(label)}</span>
<strong>${escapeHTML(value ?? "--")}</strong>
${detail ? `<small>${escapeHTML(detail)}</small>` : ""}
</article>
`;
}

function productMiniList(items, emptyText = "Signals will appear as the workspace records activity."){
const list = Array.isArray(items) ? items : [];
if(!list.length) return `<div class="empty-state">${escapeHTML(emptyText)}</div>`;
return list.slice(0, 8).map(item => {
if(typeof item === "string") return `<div class="product-signal"><strong>${escapeHTML(item)}</strong></div>`;
const title = item.title || item.name || item.skill || item.stage || item.recommendation || "Signal";
const body = item.message || item.body || item.reasoning || item.impact || item.summary || "";
const score = item.score ?? item.demand ?? item.shortage ?? item.confidence ?? "";
return `
<article class="product-signal">
<div>
<strong>${escapeHTML(title)}</strong>
${body ? `<p class="muted">${escapeHTML(body)}</p>` : ""}
</div>
${score !== "" ? `<span class="badge-pill">${escapeHTML(score)}</span>` : ""}
</article>
`;
}).join("");
}

function productScoreBar(label, value){
const score = Math.max(0, Math.min(100, Number(value) || 0));
return `
<div class="product-score-row">
<span>${escapeHTML(label)}</span>
<div class="score-track"><i style="width:${score}%"></i></div>
<strong>${escapeHTML(score)}%</strong>
</div>
`;
}

function renderProductTrendGraph(points){
const items = Array.isArray(points) ? points : [];
const max = Math.max(1, ...items.map(item => Number(item.activity || item.health || 0)));
return `
<div class="product-trend-graph" aria-label="Product intelligence trend graph">
${items.slice(-8).map((item, index) => {
const h = Math.max(10, Math.round((Number(item.activity || item.health || 0) / max) * 100));
return `<span style="--h:${h}%;--delay:${index * 60}ms" title="${escapeHTML(item.label || "")}"></span>`;
}).join("")}
</div>
`;
}

function renderProductHeatmap(items){
const list = Array.isArray(items) ? items : [];
return `
<div class="product-heatmap" aria-label="Pipeline heatmap">
${list.map(item => {
const score = Number(item.score || 0);
const cls = score >= 70 ? "hot" : score >= 40 ? "warm" : "";
return `<span class="${cls}"><b>${escapeHTML(item.stage || "stage")}</b><em>${escapeHTML(score)}%</em></span>`;
}).join("") || `<span><b>pipeline</b><em>--</em></span>`}
</div>
`;
}

function renderProductIntelligence(data, feed = productIntelligenceFeed){
const root = document.getElementById("productIntelligenceRoot");
if(!root) return;
if(!data || typeof data !== "object"){
root.innerHTML = renderOnboardingCard(
"Product Intelligence is ready.",
"The center will populate executive metrics, candidate quality, hiring risk, AI performance, platform adoption, revenue, forecasts, and recommendations as data arrives."
);
return;
}

const exec = data.executive_overview || {};
const candidatesData = data.candidate_analysis || {};
const jobsData = data.job_analysis || {};
const recruiter = data.recruiter_analysis || {};
const interview = data.interview_analysis || {};
const pipeline = data.pipeline_analysis || {};
const platform = data.platform_analytics || {};
const ai = data.ai_analytics || {};
const revenue = data.revenue_analytics || {};
const forecasting = data.forecasting || {};
const visual = data.visualizations || {};
const status = document.getElementById("productIntelligenceStatus");
if(status) status.textContent = "Live intelligence";

root.innerHTML = `
<section class="product-hero">
<div>
<p class="eyebrow">Product Intelligence Center</p>
<h2 id="productIntelligenceTitle">Workforce intelligence across people, jobs, AI, operations, and revenue</h2>
<p class="section-desc">Executive-grade analytics stitched from candidates, recruiters, jobs, interviews, hiring pipelines, organizations, platform telemetry, AI quality, and revenue motion.</p>
</div>
<div class="product-hero-actions">
<button onclick="loadProductIntelligence(true)">Refresh</button>
<button class="secondary-btn" onclick="connectProductIntelligenceStream()">Reconnect Live</button>
</div>
</section>

<section class="product-kpi-grid">
${productIntelligenceKpi("Product Health", `${exec.product_health_score ?? "--"}%`, "Executive operating index", (exec.product_health_score || 0) >= 70 ? "top" : "average")}
${productIntelligenceKpi("Organizations", exec.total_organizations, "Active workspaces", "average")}
${productIntelligenceKpi("Candidates", exec.total_candidates, "Analyzed profiles", "average")}
${productIntelligenceKpi("Open Jobs", exec.total_jobs, "Current demand", "average")}
${productIntelligenceKpi("Interviews", exec.total_interviews, "Window activity", "average")}
${productIntelligenceKpi("Hiring Velocity", `${exec.hiring_velocity ?? 0}%`, "Pipeline movement", "top")}
${productIntelligenceKpi("Revenue", `${revenue.platform_revenue ?? exec.platform_revenue ?? 0}`, "Recognized platform revenue", "average")}
${productIntelligenceKpi("AI Decisions / Day", exec.ai_decisions_per_day, "Automation volume", "top")}
</section>

<section class="product-grid">
<article class="product-panel wide">
<div class="panel-subhead tight"><h4>Executive Overview</h4><span class="muted">CEO-level indicators and growth motion.</span></div>
<div class="product-score-stack">
${productScoreBar("Monthly Growth", exec.monthly_growth)}
${productScoreBar("Platform Health", platform.platform_health_score)}
${productScoreBar("Adoption", platform.adoption_score)}
${productScoreBar("Revenue Growth Forecast", revenue.revenue_growth_forecast)}
</div>
</article>
<article class="product-panel">
<div class="panel-subhead tight"><h4>Trend Graph</h4><span class="muted">Recent operating activity.</span></div>
${renderProductTrendGraph(visual.trends)}
</article>
<article class="product-panel">
<div class="panel-subhead tight"><h4>Pipeline Heatmap</h4><span class="muted">Stage health and conversion pressure.</span></div>
${renderProductHeatmap(visual.pipeline_heatmap)}
</article>
</section>

<section class="product-grid three">
<article class="product-panel">
<div class="panel-subhead tight"><h4>Candidate Analytics</h4><span class="muted">Resume quality, skill coverage, readiness, confidence.</span></div>
${productScoreBar("Candidate Score", candidatesData.candidate_score)}
${productScoreBar("Match Score", candidatesData.match_score)}
${productScoreBar("Readiness", candidatesData.readiness_score)}
<div class="product-list">${productMiniList(candidatesData.top_candidates, "Upload resumes to activate candidate analysis.")}</div>
</article>
<article class="product-panel">
<div class="panel-subhead tight"><h4>Job Analytics</h4><span class="muted">Demand, time-to-fill, shortages, risk.</span></div>
${productScoreBar("Job Health", jobsData.job_health_score)}
${productScoreBar("Market Demand", jobsData.market_demand_score)}
${productScoreBar("Fill Probability", jobsData.fill_probability)}
${productScoreBar("Hiring Risk", jobsData.hiring_risk_score)}
<div class="product-list">${productMiniList(jobsData.talent_shortages, "No severe talent shortage detected.")}</div>
</article>
<article class="product-panel">
<div class="panel-subhead tight"><h4>Recruiter Analytics</h4><span class="muted">Performance, efficiency, conversion, velocity.</span></div>
${productScoreBar("Performance", recruiter.recruiter_performance_score)}
${productScoreBar("Efficiency", recruiter.efficiency_score)}
${productScoreBar("Conversion", recruiter.conversion_rate)}
<div class="product-list">${productMiniList(recruiter.leaderboard, "Recruiter leaderboard activates after team activity.")}</div>
</article>
</section>

<section class="product-grid three">
<article class="product-panel">
<div class="panel-subhead tight"><h4>Interview Analytics</h4><span class="muted">Quality, confidence, completion, communication.</span></div>
${productScoreBar("Interview Quality", interview.interview_quality_score)}
${productScoreBar("Candidate Confidence", interview.candidate_confidence_score)}
${productScoreBar("Completion Rate", interview.interview_completion_rate)}
<div class="mini-row"><span>Confidence trend</span><strong>${escapeHTML(interview.behavioral_patterns?.confidence_trend || "--")}</strong></div>
</article>
<article class="product-panel">
<div class="panel-subhead tight"><h4>Pipeline Analytics</h4><span class="muted">Applications through hires.</span></div>
<div class="product-stage-grid">
${["applications", "screening", "shortlisting", "interviews", "offers", "hires"].map(key => `<span><b>${escapeHTML(pipeline[key] ?? 0)}</b>${escapeHTML(key)}</span>`).join("")}
</div>
${productScoreBar("Pipeline Health", pipeline.pipeline_health_score)}
${productScoreBar("Drop-off Rate", pipeline.drop_off_rate)}
<div class="product-list">${productMiniList(pipeline.bottleneck_detection, "No bottlenecks detected.")}</div>
</article>
<article class="product-panel">
<div class="panel-subhead tight"><h4>AI Analytics</h4><span class="muted">Accuracy, confidence, parsing, drift.</span></div>
${productScoreBar("AI Confidence", ai.ai_confidence_index)}
${productScoreBar("AI Performance", ai.ai_performance_score)}
${productScoreBar("Matching Accuracy", ai.ai_matching_accuracy)}
${productScoreBar("Resume Parsing", ai.resume_parsing_accuracy)}
<div class="mini-row"><span>Drift</span><strong>${escapeHTML(ai.ai_drift_detection?.status || "--")} ${escapeHTML(ai.ai_drift_detection?.score ?? "")}</strong></div>
</article>
</section>

<section class="product-grid">
<article class="product-panel">
<div class="panel-subhead tight"><h4>Platform Analytics</h4><span class="muted">Usage, adoption, APIs, latency, queues.</span></div>
<div class="product-stage-grid">
<span><b>${escapeHTML(platform.active_users ?? 0)}</b>active users</span>
<span><b>${escapeHTML(platform.daily_usage ?? 0)}</b>daily usage</span>
<span><b>${escapeHTML(platform.api_usage ?? 0)}</b>API events</span>
<span><b>${escapeHTML(platform.system_latency_ms ?? "--")}</b>latency ms</span>
</div>
<div class="product-list">${productMiniList(Object.entries(platform.feature_adoption || {}).map(([name, score]) => ({title:name, score})), "Feature adoption appears after activity.")}</div>
</article>
<article class="product-panel">
<div class="panel-subhead tight"><h4>Forecasting</h4><span class="muted">Demand, success, performance, revenue, expansion.</span></div>
${productScoreBar("Hiring Demand", forecasting.hiring_demand)}
${productScoreBar("Candidate Success", forecasting.candidate_success)}
${productScoreBar("Recruiter Performance", forecasting.recruiter_performance)}
${productScoreBar("Revenue Growth", forecasting.revenue_growth)}
${productScoreBar("Organization Expansion", forecasting.organization_expansion)}
</article>
</section>

<section class="product-grid">
<article class="product-panel">
<div class="panel-subhead tight"><h4>AI Product Analyst</h4><span class="muted">Copilot insights and recommendations.</span></div>
<div class="product-list analyst">${productMiniList(data.copilot_insights, "AI Product Analyst is waiting for signals.")}</div>
<div class="product-list">${productMiniList(data.recommendations, "No recommendations right now.")}</div>
</article>
<article class="product-panel">
<div class="panel-subhead tight"><h4>Observability Integration</h4><span class="muted">Events shared with Observability, Security Center, AI Decision Brain, and Operations Fabric.</span></div>
<div class="product-list">${productMiniList((data.observability_integration?.destinations || []).map(dest => ({title: dest, body: "receiving product intelligence events"})))}</div>
<div class="product-list">${productMiniList(feed, "Live event feed will appear after activity.")}</div>
</article>
</section>
`;
}

async function loadProductIntelligence(force = false){
if(!force && isFresh("product_intelligence") && productIntelligenceState){
renderProductIntelligence(productIntelligenceState, productIntelligenceFeed);
connectProductIntelligenceStream();
return;
}
const root = document.getElementById("productIntelligenceRoot");
if(root) root.innerHTML = `<div class="product-loading"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>`;
showLoader();
try {
const [data, events] = await Promise.all([
apiProductIntelligence(30),
apiProductIntelligenceEvents(20).catch(() => ({events: []})),
]);
productIntelligenceState = data;
productIntelligenceFeed = events?.events || [];
renderProductIntelligence(productIntelligenceState, productIntelligenceFeed);
markLoaded("product_intelligence");
connectProductIntelligenceStream();
} catch (err) {
if(root){
root.innerHTML = renderOnboardingCard(
"Product Intelligence could not refresh.",
err.message || "The intelligence service needs a moment to sync."
);
}
showAlert(err.message || "Unable to load Product Intelligence");
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
updateMatchFilters,
loadMoreMatches,
runDecisionAI,
refreshAIFeedback,
openCommandPalette,
closeCommandPalette,
analyzeCandidate360,
generateExecutiveBrief,
loadProductIntelligence,
connectProductIntelligenceStream,
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
scrollShowcaseRail,
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
exportCandidateProfile,
assignRecruiter,
addOperationalNote,
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
