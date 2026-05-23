let currentView = "dashboard";
let candidates = [];
let jobs = [];
let matches = [];
let workspace = null;
let notifications = [];
let enterpriseState = {team: null, billing: null, security: null};
let feedbackData = null;
let feedbackState = "empty";
let feedbackError = "";
let lastResumeText = "";
let chatOpen = false;
let chatSessionId = sessionStorage.getItem("chatSessionId");
if(!chatSessionId){
chatSessionId = `chat-${Date.now()}-${Math.random().toString(16).slice(2)}`;
sessionStorage.setItem("chatSessionId", chatSessionId);
}
const chatRenderState = new WeakMap();
let lastLoaded = {
dashboard: 0,
candidates: 0,
jobs: 0,
matches: 0,
brain: 0,
workforce_os: 0,
exec_brief: 0
};
let shortlistEntries = [];
const shortlist = new Set();
let shortlistJobId = null;
const CACHE_TTL = 15000;
const MAX_TABLE_ROWS = 100;

// Live AI Ops stream (enterprise "operating system" feel)
let opsSocket = null;
let opsReconnectTimer = null;
let opsHeartbeatTimer = null;
let opsBackoffMs = 900;
let opsFeedItems = [];
let opsFallbackTimer = null;
let dashboardExecutiveBrief = null;
let workforceOSState = null;
const compareCandidateIds = new Set();
let lastAlertSignature = "";
let lastAlertAt = 0;
let viewRequestSeq = 0;
let operationalSyncTimer = null;
let loaderSafetyTimer = null;
let lastOpsCoordinationAt = 0;
let lastSecurityCoordinationAt = 0;

// Live Security stream (SOC feel)
let securitySocket = null;
let securityReconnectTimer = null;
let securityHeartbeatTimer = null;
let securityBackoffMs = 900;
let securityFeedItems = [];
let securityFallbackTimer = null;
let securityIncidents = [];
let securityRules = [];
let securityControls = null;
let selectedSecurityIncidentId = null;
let selectedSecurityIncident = null;
let selectedSecurityIncidentActions = [];
let securityRefreshTimer = null;

// Command palette (recruiter operating system feel)
let paletteOpen = false;
let paletteIndex = 0;
let paletteItems = [];
let paletteFiltered = [];
let realtimeHealth = {checkedAt: 0, available: null, guidance: ""};

function _loadSessionList(key, limit = 20){
try {
const raw = sessionStorage.getItem(key);
const parsed = raw ? JSON.parse(raw) : [];
return Array.isArray(parsed) ? parsed.slice(0, limit) : [];
} catch {
return [];
}
}

function _storeSessionList(key, list){
try {
sessionStorage.setItem(key, JSON.stringify(Array.isArray(list) ? list : []));
} catch {}
}

function rememberViewedCandidate(candidateId){
const id = String(candidateId || "").trim();
if(!id) return;
const existing = _loadSessionList("viewedCandidateIds", 24);
const next = [id, ...existing.filter(x => String(x) !== id)].slice(0, 24);
_storeSessionList("viewedCandidateIds", next);
}

function rememberRecruiterPreference(patch){
if(!patch || typeof patch !== "object") return;
let existing = {};
try { existing = JSON.parse(sessionStorage.getItem("recruiterPrefs") || "{}") || {}; } catch { existing = {}; }
const next = {...existing, ...patch, updated_at: new Date().toISOString()};
try { sessionStorage.setItem("recruiterPrefs", JSON.stringify(next)); } catch {}
}

function escapeHTML(value){
return String(value ?? "").replace(/[&<>"']/g, char => ({
"&": "&amp;",
"<": "&lt;",
">": "&gt;",
"\"": "&quot;",
"'": "&#039;"
})[char]);
}

function candidateDisplayName(candidate){
if(!candidate || typeof candidate !== "object") return "";
const name = String(candidate.candidate_name || candidate.name || "").trim();
if(name) return name;
const profileName = String(candidate.profile?.candidate_name || candidate.profile?.name || "").trim();
return profileName;
}

function candidatePrimaryLabel(candidate){
const name = candidateDisplayName(candidate);
return name || "Unnamed Candidate";
}

function candidateSecondaryLabel(candidate){
const id = String(candidate?.candidate_id || "").trim();
return id ? `ID: ${id}` : "";
}

function renderOnboardingCard(title, body, actionsHtml = ""){
return `
<div class="empty-state empty-onboarding">
<h4>${escapeHTML(title)}</h4>
<p class="muted">${escapeHTML(body)}</p>
${actionsHtml ? `<div class="onboarding-actions">${actionsHtml}</div>` : ""}
</div>
`;
}

function renderMatchOnboardingState(contextHint = ""){
const hint = contextHint ? `<p class="muted">${escapeHTML(contextHint)}</p>` : "";
const actions = `
<button onclick="switchView('jobs')">Create First Job</button>
<button class="secondary-btn" onclick="switchView('upload')">Upload Resume</button>
<button class="secondary-btn" onclick="switchView('brain')">Launch AI Decision Brain</button>
`;
return `
<div class="empty-state empty-onboarding">
<h4>Role intake is ready.</h4>
<p class="muted">Create your first job description to activate AI candidate ranking, shortlist intelligence, and recruiter recommendations.</p>
${hint}
<div class="onboarding-actions">${actions}</div>
</div>
`;
}

function candidateById(candidateId){
const id = String(candidateId || "").trim();
if(!id) return null;
return candidates.find(item => String(item.candidate_id || "") === id) || null;
}

function wsBaseUrl(){
const base = (window.API_BASE || "http://127.0.0.1:8000").replace(/\/$/, "");
const url = new URL(base);
const proto = url.protocol === "https:" ? "wss:" : "ws:";
return `${proto}//${url.host}`;
}

function formatTimeLabel(iso){
try {
const d = iso ? new Date(iso) : null;
if(!d || Number.isNaN(d.getTime())) return "";
return d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
} catch {
return "";
}
}

function reconnectDelay(baseMs){
const base = Math.max(700, Number(baseMs) || 900);
const jitter = Math.round(base * (0.18 + Math.random() * 0.22));
return Math.min(18000, base + jitter);
}

function startSocketHeartbeat(socketRef, timerSetter){
const timer = setInterval(() => {
try {
if(socketRef && socketRef.readyState === WebSocket.OPEN){
socketRef.send("ping");
}
} catch {}
}, 20000);
timerSetter(timer);
}

function stopOpsHeartbeat(){
if(opsHeartbeatTimer){
clearInterval(opsHeartbeatTimer);
opsHeartbeatTimer = null;
}
}

function stopSecurityHeartbeat(){
if(securityHeartbeatTimer){
clearInterval(securityHeartbeatTimer);
securityHeartbeatTimer = null;
}
}

async function checkRealtimeCapability(force = false){
const age = Date.now() - Number(realtimeHealth.checkedAt || 0);
if(!force && realtimeHealth.available !== null && age < 30000){
return realtimeHealth.available;
}
try {
const res = await fetch(`${window.API_BASE}/health`, {method: "GET", credentials: "include"});
const body = await res.json().catch(() => null);
const realtime = body?.checks?.realtime || {};
const available = String(realtime.status || "").toLowerCase() === "ok";
realtimeHealth = {
checkedAt: Date.now(),
available,
guidance: realtime.guidance || ""
};
return available;
} catch {
realtimeHealth = {checkedAt: Date.now(), available: false, guidance: "Realtime health check unavailable."};
return false;
}
}

function stopOpsPollingFallback(){
if(opsFallbackTimer){
clearTimeout(opsFallbackTimer);
opsFallbackTimer = null;
}
}

function startOpsPollingFallback(reason = "Realtime stream is in resilient polling mode."){
stopOpsPollingFallback();
setOpsPresence(false, "Live via resilient sync");
if(!opsFeedItems.some(item => item?.id === "ops-polling-fallback")){
pushOpsFeedItem({
id: "ops-polling-fallback",
title: "Realtime continuity protected",
body: reason,
created_at: new Date().toISOString(),
severity: "info"
});
}
const tick = async () => {
if(document.visibilityState === "hidden" && currentView !== "dashboard"){
opsFallbackTimer = setTimeout(tick, 18000);
return;
}
await Promise.all([
loadWorkforceOS(true).catch(() => null),
loadDashboardExecutiveBrief(true).catch(() => null),
]);
if(currentView === "dashboard"){
renderOpsRecommendations(workforceOSState?.autonomous_intelligence?.recommendations || []);
}
opsFallbackTimer = setTimeout(tick, 12000);
};
opsFallbackTimer = setTimeout(tick, 2500);
}

function stopSecurityPollingFallback(){
if(securityFallbackTimer){
clearTimeout(securityFallbackTimer);
securityFallbackTimer = null;
}
}

function startSecurityPollingFallback(reason = "Security stream is in resilient polling mode."){
stopSecurityPollingFallback();
setSecurityPresence(false, "Live via resilient sync");
if(!securityFeedItems.some(item => item?.id === "security-polling-fallback")){
pushSecurityFeedItem({
id: "security-polling-fallback",
title: "Security continuity protected",
body: reason,
created_at: new Date().toISOString(),
severity: "info"
});
}
const tick = async () => {
if(currentView === "security"){
await loadSecurityCenter(false).catch(() => null);
}
securityFallbackTimer = setTimeout(tick, 14000);
};
securityFallbackTimer = setTimeout(tick, 3000);
}

function shouldHoldRealtimeReconnect(){
return document.visibilityState === "hidden" && currentView !== "dashboard" && currentView !== "security";
}

function nextViewRequestToken(){
viewRequestSeq += 1;
return viewRequestSeq;
}

function isCurrentViewRequest(token, view = currentView){
return token === viewRequestSeq && view === currentView;
}

function _confidenceLabel(value){
const n = Number(value);
if(!Number.isFinite(n)) return "";
const v = n > 1 ? (n / 100) : n;
if(v >= 0.85) return "High";
if(v >= 0.65) return "Medium";
if(v >= 0.45) return "Low";
return "Very low";
}

function _confidenceBadge(value){
const label = _confidenceLabel(value);
if(!label) return "";
const n = Number(value);
const v = n > 1 ? (n / 100) : n;
const cls = v >= 0.85 ? "conf-high" : v >= 0.65 ? "conf-med" : v >= 0.45 ? "conf-low" : "conf-vlow";
return `<span class="badge-pill badge-confidence ${cls}">Confidence: ${escapeHTML(label)}</span>`;
}

function setOpsPresence(online, labelOverride = ""){
const pill = document.getElementById("opsPresence");
if(!pill) return;
if(online){
pill.className = "status-pill status-top";
pill.textContent = labelOverride || "Live intelligence";
} else {
const text = labelOverride || "Sync restoring";
const calmState = /restoring|connecting|reconnect|paused/i.test(text);
pill.className = `status-pill ${calmState ? "status-average" : "status-low"}`;
pill.textContent = text;
}
}

function invalidateOperationalCaches(keys = ["dashboard", "workforce_os", "exec_brief"]){
keys.forEach(key => {
if(Object.prototype.hasOwnProperty.call(lastLoaded, key)){
lastLoaded[key] = 0;
}
});
}

function scheduleOperationalSync(reason = "workflow_updated", delay = 450){
if(operationalSyncTimer){
clearTimeout(operationalSyncTimer);
}
operationalSyncTimer = setTimeout(async () => {
operationalSyncTimer = null;
invalidateOperationalCaches();
const tasks = [
loadWorkforceOS(true).catch(() => null),
loadDashboardExecutiveBrief(true).catch(() => null),
];
if(currentView === "dashboard"){
tasks.push(loadDashboard(nextViewRequestToken()).catch(() => null));
}
if(currentView === "shortlist"){
tasks.push(refreshShortlist(true).catch(() => null));
}
await Promise.all(tasks);
if(reason && currentView === "dashboard"){
renderOpsRecommendations(workforceOSState?.autonomous_intelligence?.recommendations || []);
}
if(!opsSocket || opsSocket.readyState === WebSocket.CLOSED){
connectOpsStream();
}
}, Math.max(150, Number(delay) || 450));
}

function recommendationTrustHtml(item){
if(!item || typeof item !== "object") return "";
const reasoning = item.reasoning || item.why || item.explanation?.reasoning || "";
const impact = item.impact || item.operational_impact || "";
const action = item.action || item.suggested_action || item.recommendation || "";
const uncertainty = item.uncertainty || item.caveat || "";
if(!reasoning && !impact && !action && !uncertainty) return "";
return `
<div class="ai-trust-strip">
${reasoning ? `<span><b>Reasoning</b>${escapeHTML(reasoning)}</span>` : ""}
${impact ? `<span><b>Impact</b>${escapeHTML(impact)}</span>` : ""}
${action ? `<span><b>Action</b>${escapeHTML(action)}</span>` : ""}
${uncertainty ? `<span><b>Uncertainty</b>${escapeHTML(uncertainty)}</span>` : ""}
</div>
`;
}

function renderOpsRecommendations(list){
const container = document.getElementById("opsRecommendations");
if(!container) return;
const items = Array.isArray(list) ? list : [];
if(!items.length){
container.innerHTML = `<div class="empty-state">No intervention needed right now. WorkforceOS is watching pipeline movement, recruiter decisions, and scheduling risk.</div>`;
return;
}
container.innerHTML = items.slice(0, 4).map(item => `
<article class="ops-event">
<div class="ops-event-head">
<strong>${escapeHTML(item.title || "AI recommendation")}</strong>
<span class="badge-pill">${escapeHTML((item.priority || "low").toUpperCase())}</span>
</div>
<p>${escapeHTML(item.body || "")}</p>
${item.why ? `<p class="ops-event-sub">Why this surfaced: ${escapeHTML(item.why)}</p>` : ""}
${recommendationTrustHtml(item)}
<div class="ops-event-meta">
${_confidenceBadge(item.confidence)}
</div>
</article>
`).join("");
}

function renderOpsFeed(items){
const feed = document.getElementById("opsFeed");
if(!feed) return;
const list = Array.isArray(items) ? items : [];
if(!list.length){
feed.innerHTML = `<div class="empty-state">The operating timeline is armed. Create a role, upload resumes, or load Demo Data to begin live hiring intelligence.</div>`;
return;
}
feed.innerHTML = list.slice(0, 14).map(item => `
<article class="ops-event">
<div class="ops-event-head">
<strong>${escapeHTML(item.title || "Event")}</strong>
<time>${escapeHTML(formatTimeLabel(item.created_at))}</time>
</div>
<p>${escapeHTML(item.body || "")}</p>
<div class="ops-event-meta">
${item.severity ? `<span class="badge-pill">${escapeHTML(String(item.severity).toUpperCase())}</span>` : ""}
</div>
</article>
`).join("");
}

function renderComparisonTray(){
let tray = document.getElementById("comparisonTray");
if(!tray){
tray = document.createElement("div");
tray.id = "comparisonTray";
tray.className = "comparison-tray hidden";
document.body.appendChild(tray);
}
const selected = [...compareCandidateIds]
.map(id => matches.find(item => String(item.candidate_id) === id) || candidates.find(item => String(item.candidate_id) === id))
.filter(Boolean);
if(!selected.length){
tray.className = "comparison-tray hidden";
tray.innerHTML = "";
return;
}
tray.className = "comparison-tray";
tray.innerHTML = `
<div class="comparison-head">
<strong>Candidate Comparison</strong>
<button class="secondary-btn" onclick="clearComparison()">Clear</button>
</div>
<div class="comparison-grid">
${selected.slice(0, 3).map(candidate => {
const score = candidate.match_score ?? candidate.score ?? 0;
return `
<article>
<strong>${escapeHTML(candidatePrimaryLabel(candidate))}</strong>
<div class="muted small">${escapeHTML(candidate.candidate_id || "")}</div>
${scoreBar(score)}
<p>${escapeHTML(candidate.recruiter_summary || candidate.recommendation_reason || "Review candidate profile and match evidence.")}</p>
<div class="skill-cloud">${(candidate.matched_skills || candidate.skills || []).slice(0, 5).map(skill => `<span>${escapeHTML(skill)}</span>`).join("")}</div>
</article>
`;
}).join("")}
</div>
`;
}

function toggleCompareCandidate(candidateId){
const id = String(candidateId || "").trim();
if(!id) return;
if(compareCandidateIds.has(id)) compareCandidateIds.delete(id);
else {
if(compareCandidateIds.size >= 3){
showAlert("Compare up to three candidates at a time.", "info");
return;
}
compareCandidateIds.add(id);
}
renderComparisonTray();
renderMatchResults(matches);
}

function clearComparison(){
compareCandidateIds.clear();
renderComparisonTray();
renderMatchResults(matches);
}

function pushOpsFeedItem(item){
if(!item || typeof item !== "object") return;
opsFeedItems = [item, ...opsFeedItems].slice(0, 40);
renderOpsFeed(opsFeedItems);
}

function renderWorkforceOS(data){
workforceOSState = data && typeof data === "object" ? data : null;
const status = document.getElementById("workforceOSStatus");
const mode = document.getElementById("workforceOSMode");
const narrative = document.getElementById("workforceOSNarrative");
const metrics = document.getElementById("workforceOSMetrics");
const priorities = document.getElementById("workforceOSPriorities");
const agents = document.getElementById("workforceOSAgents");
const simulations = document.getElementById("workforceOSSimulations");
const liveStrip = document.getElementById("workforceOSLiveStrip");
const connectors = document.getElementById("workforceOSConnectors");
const aiOps = document.getElementById("workforceOSAIOps");
const governance = document.getElementById("workforceOSGovernance");
const readiness = document.getElementById("workforceOSReadiness");
if(!status || !mode || !narrative || !metrics || !priorities || !agents || !simulations) return;

if(!workforceOSState || workforceOSState.status !== "ok"){
status.className = "status-pill status-average";
status.textContent = "Learning";
mode.textContent = "Operational intelligence is calibrating";
narrative.textContent = "Create roles, upload resumes, and run matches to activate autonomous workforce coordination.";
metrics.innerHTML = "";
priorities.innerHTML = renderOnboardingCard("WorkforceOS is calibrated for intake.", "As soon as hiring signals arrive, the command center will turn them into priorities, risk explanations, and next actions.");
agents.innerHTML = "";
simulations.innerHTML = "";
if(liveStrip) liveStrip.innerHTML = "";
if(connectors) connectors.innerHTML = "";
if(aiOps) aiOps.innerHTML = "";
if(governance) governance.innerHTML = "";
if(readiness) readiness.innerHTML = "";
return;
}

const state = workforceOSState.operating_state || {};
const signal = workforceOSState.signals || {};
const exec = workforceOSState.executive_intelligence?.operating_summary || {};
const realtime = workforceOSState.realtime_infrastructure || {};
const trust = workforceOSState.trust_contract || {};
const health = Number(state.health_score || 0);
status.className = `status-pill ${health >= 70 ? "status-top" : health >= 45 ? "status-average" : "status-low"}`;
status.textContent = `${state.pipeline_trend || "Monitoring"} / ${health}%`;
mode.textContent = "AI agents are coordinating hiring operations";
narrative.textContent = exec.boardroom_readout || `Monitoring ${signal.total_candidates || 0} candidates, ${signal.active_jobs || 0} roles, ${signal.shortlisted_candidates || 0} shortlisted candidates, and ${signal.scheduled_candidates || 0} scheduled interviews.`;

metrics.innerHTML = (workforceOSState.command_metrics || []).map(item => `
<article class="metric-card os-metric-card">
<span>${escapeHTML(item.label || "Metric")}</span>
<strong>${escapeHTML(item.value || "0")}</strong>
<em class="status-pill status-${String(item.tone || "average").replace(/[^a-z-]/g, "")}">${escapeHTML(item.tone || "active")}</em>
</article>
`).join("");

if(liveStrip){
const cadence = realtime.ops_stream?.heartbeat_seconds || 20;
const streamStatus = realtime.ops_stream?.status || "resilient";
const coordinationMode = workforceOSState.coordination_layer?.mode || state.mode || "autonomous monitoring";
const auditability = (trust.auditability || []).slice(0, 4).join(", ") || "reasoning, confidence, uncertainty, next action";
liveStrip.innerHTML = `
<article>
<span class="live-dot" aria-hidden="true"></span>
<div><b>Realtime continuity</b><small>${escapeHTML(streamStatus)} stream / ${escapeHTML(cadence)}s heartbeat</small></div>
</article>
<article>
<span class="live-dot live-dot-blue" aria-hidden="true"></span>
<div><b>Coordination mode</b><small>${escapeHTML(String(coordinationMode).replaceAll("_", " "))}</small></div>
</article>
<article>
<span class="live-dot live-dot-gold" aria-hidden="true"></span>
<div><b>Trust contract</b><small>${escapeHTML(auditability)}</small></div>
</article>
`;
}

const bottlenecks = workforceOSState.autonomous_intelligence?.bottlenecks || [];
priorities.innerHTML = bottlenecks.length ? bottlenecks.slice(0, 4).map(item => `
<article class="ops-event os-priority ${escapeHTML(item.severity || "low")}">
<div class="ops-event-head">
<strong>${escapeHTML(item.title || "AI priority")}</strong>
<span class="badge-pill">${escapeHTML(String(item.severity || "low").toUpperCase())}</span>
</div>
<p>${escapeHTML(item.summary || "")}</p>
<p class="ops-event-sub">${escapeHTML(item.recommendation || "")}</p>
${recommendationTrustHtml(item)}
<div class="ops-event-meta">
${_confidenceBadge(item.confidence)}
${item.why ? `<span class="muted small">${escapeHTML(item.why)}</span>` : ""}
</div>
</article>
`).join("") : `<div class="empty-state">No intervention needed. WorkforceOS is maintaining watch across ranking, shortlist, scheduling, and interview signals.</div>`;

agents.innerHTML = (workforceOSState.agents || []).slice(0, 6).map(agent => `
<article class="agent-card">
<div class="agent-card-head">
<strong>${escapeHTML(agent.name || "AI Agent")}</strong>
<span class="status-pill status-average">${escapeHTML(agent.status || "active")}</span>
</div>
<p class="muted">${escapeHTML(agent.focus || "")}</p>
<p>${escapeHTML(agent.summary || "")}</p>
<div class="ops-event-meta">${_confidenceBadge(agent.confidence)}</div>
</article>
`).join("");

simulations.innerHTML = (workforceOSState.simulations || []).slice(0, 3).map(item => `
<article class="simulation-card">
<div class="ops-event-head">
<strong>${escapeHTML(item.name || "Scenario")}</strong>
<span>${escapeHTML(item.hiring_outcome_probability ?? 0)}%</span>
</div>
<div class="simulation-bars">
<span style="width:${Math.max(0, Math.min(100, Number(item.hiring_outcome_probability) || 0))}%"></span>
</div>
<p>${escapeHTML(item.summary || "")}</p>
<div class="mini-row"><span>Delay risk</span><strong>${escapeHTML(item.delay_probability ?? 0)}%</strong></div>
<div class="mini-row"><span>Shortage forecast</span><strong>${escapeHTML(item.staffing_shortage ?? 0)}</strong></div>
</article>
`).join("");

if(connectors){
const registry = workforceOSState.integration_architecture?.registry || [];
connectors.innerHTML = registry.slice(0, 8).map(item => `
<article class="connector-card">
<div>
<strong>${escapeHTML(item.provider || "Connector")}</strong>
<p class="muted">${escapeHTML(item.category || "integration")}</p>
</div>
<span class="status-pill ${String(item.status || "").includes("ready") ? "status-top" : "status-average"}">${escapeHTML(item.status || "configured")}</span>
</article>
`).join("");
}

if(aiOps){
const ops = workforceOSState.ai_operations_monitoring || {};
const cost = workforceOSState.cost_resource_optimization || {};
const comms = workforceOSState.communication_infrastructure || {};
const schedule = workforceOSState.scheduling_intelligence || {};
aiOps.innerHTML = `
<article class="infra-card">
<div class="mini-row"><span>AI health</span><strong>${escapeHTML(ops.health_score ?? 0)}%</strong></div>
<div class="mini-row"><span>Quality</span><strong>${escapeHTML(ops.quality_score ?? 0)}%</strong></div>
<div class="mini-row"><span>Fallback</span><strong>${escapeHTML(ops.fallback_activity || "available")}</strong></div>
<div class="mini-row"><span>Estimated tokens</span><strong>${escapeHTML(cost.estimated_tokens ?? 0)}</strong></div>
<div class="mini-row"><span>Cost estimate</span><strong>$${escapeHTML(cost.estimated_cost_usd ?? 0)}</strong></div>
</article>
<article class="infra-card">
<strong>Communication AI</strong>
<p class="muted">${escapeHTML((comms.ai_suggestions || [])[0] || "Recruiter communication intelligence is monitoring candidate touchpoints.")}</p>
<div class="mini-row"><span>Events</span><strong>${escapeHTML(comms.events ?? 0)}</strong></div>
<div class="mini-row"><span>Reminders</span><strong>${escapeHTML(schedule.pending_reminders ?? 0)}</strong></div>
</article>
`;
}

if(governance){
const compliance = workforceOSState.compliance_governance || {};
const market = workforceOSState.market_intelligence || {};
governance.innerHTML = `
<article class="infra-card">
<div class="mini-row"><span>Audit readiness</span><strong>${escapeHTML(compliance.audit_readiness ?? 0)}%</strong></div>
<div class="mini-row"><span>GDPR</span><strong>${escapeHTML(compliance.gdpr || "policy ready")}</strong></div>
<div class="mini-row"><span>Retention</span><strong>${escapeHTML(compliance.retention_policy?.candidate_records_days ?? 365)} days</strong></div>
</article>
<article class="infra-card">
<strong>Market Intelligence</strong>
<p class="muted">${escapeHTML((market.alerts || [])[0] || "Talent market signals are balanced.")}</p>
<div class="mini-row"><span>Availability</span><strong>${escapeHTML(market.talent_availability ?? 0)}%</strong></div>
<div class="mini-row"><span>Competitiveness</span><strong>${escapeHTML(market.competitiveness || "balanced")}</strong></div>
</article>
`;
}

if(readiness){
const prod = workforceOSState.production_readiness || {};
const executive = workforceOSState.executive_intelligence?.operating_summary || {};
const checks = [...(prod.workflow_checks || []), ...(prod.security_checks || [])].slice(0, 7);
readiness.innerHTML = `
<article class="infra-card">
<div class="mini-row"><span>Readiness</span><strong>${escapeHTML(prod.score ?? 0)}%</strong></div>
<div class="mini-row"><span>Status</span><strong>${escapeHTML(String(prod.status || "setup_required").replaceAll("_", " "))}</strong></div>
<div class="mini-row"><span>Stream cadence</span><strong>${escapeHTML(prod.performance?.ops_stream_poll_seconds ?? 1.6)}s</strong></div>
</article>
<article class="infra-card executive-os-card">
<strong>Executive Operating Readout</strong>
<p class="muted">${escapeHTML(executive.boardroom_readout || "Executive intelligence is watching hiring velocity, delay risk, and operating readiness.")}</p>
<div class="mini-row"><span>Velocity</span><strong>${escapeHTML(executive.velocity_trend || "calibrating")}</strong></div>
<div class="mini-row"><span>AI effectiveness</span><strong>${escapeHTML(executive.ai_effectiveness_index ?? 0)}%</strong></div>
<div class="mini-row"><span>Forecast confidence</span><strong>${escapeHTML(_confidenceLabel(executive.confidence) || "Early signal")}</strong></div>
</article>
<article class="infra-card readiness-list">
${checks.map(item => `
<div class="mini-row">
<span>${escapeHTML(item.name || "Check")}</span>
<strong class="${item.status === "ready" ? "tone-good" : "tone-warn"}">${escapeHTML(String(item.status || "review").replaceAll("_", " "))}</strong>
</div>
`).join("")}
</article>
`;
}
}

async function loadWorkforceOS(force = false){
if(!force && isFresh("workforce_os") && workforceOSState){
renderWorkforceOS(workforceOSState);
return;
}
try {
const data = await apiWorkforceOS(14);
renderWorkforceOS(data);
markLoaded("workforce_os");
} catch (err) {
renderWorkforceOS(null);
}
}

async function loadDemoEnvironment(event){
const button = event?.target;
setBusy(button, true, "Preparing...");
try {
const result = await apiSeedDemoEnvironment();
["dashboard", "candidates", "jobs", "matches", "shortlist", "workforce_os", "exec_brief"].forEach(key => {
lastLoaded[key] = 0;
});
await Promise.all([
loadJobs().catch(() => null),
loadCandidates().catch(() => null),
refreshShortlist(true).catch(() => null),
loadWorkforceOS(true).catch(() => null),
loadDashboardExecutiveBrief(true).catch(() => null),
]);
scheduleOperationalSync("demo_environment_ready", 250);
showAlert(result?.summary || "Enterprise demo environment is ready.", "success");
} catch (err) {
showAlert(err.message || "Unable to prepare demo environment.");
} finally {
setBusy(button, false);
}
}

function setSecurityPresence(online, label = ""){
const pill = document.getElementById("securityStreamStatus");
if(!pill) return;
pill.classList.remove("status-top", "status-average", "status-low");
if(online){
pill.classList.add("status-top");
pill.textContent = label || "Live security";
return;
}
const text = label || "Security sync restoring";
pill.classList.add(text.toLowerCase().includes("connecting") || text.toLowerCase().includes("reconnect") || text.toLowerCase().includes("restoring") || text.toLowerCase().includes("paused") ? "status-average" : "status-low");
pill.textContent = text;
}

function renderSecurityLiveFeed(items){
const container = document.getElementById("securityLiveFeed");
if(!container) return;
const list = Array.isArray(items) ? items : [];
container.innerHTML = list.length ? list.slice(0, 50).map(item => `
<article class="ops-event security-event ${String(item.severity || "").toLowerCase()}">
<div class="ops-event-head">
<strong>${escapeHTML(item.title || "Security update")}</strong>
<time>${escapeHTML(formatTimeLabel(item.created_at))}</time>
</div>
<p>${escapeHTML(item.body || "")}</p>
<div class="ops-event-meta">
${item.severity ? `<span class="badge-pill">${escapeHTML(String(item.severity).toUpperCase())}</span>` : ""}
</div>
</article>
`).join("") : `<div class="empty-state">Security monitoring is active. Incidents and access anomalies will appear here when they require attention.</div>`;
}

function pushSecurityFeedItem(item){
if(!item || typeof item !== "object") return;
securityFeedItems = [item, ...securityFeedItems].slice(0, 60);
renderSecurityLiveFeed(securityFeedItems);
}

function scheduleSecurityRefresh(delayMs = 900){
if(securityRefreshTimer){
clearTimeout(securityRefreshTimer);
securityRefreshTimer = null;
}
securityRefreshTimer = setTimeout(() => {
securityRefreshTimer = null;
if(currentView === "security"){
loadSecurityCenter(false);
}
}, delayMs);
}

async function connectSecurityStream(){
if(securityReconnectTimer){
clearTimeout(securityReconnectTimer);
securityReconnectTimer = null;
}
if(securitySocket && (securitySocket.readyState === WebSocket.OPEN || securitySocket.readyState === WebSocket.CONNECTING)){
return;
}

const token = typeof getToken === "function" ? getToken() : "";
if(!token){
setSecurityPresence(false, "Security auth required");
return;
}

if(!(await checkRealtimeCapability())){
startSecurityPollingFallback(realtimeHealth.guidance || "Realtime dependency is unavailable; Security Center is using authenticated polling.");
return;
}
stopSecurityPollingFallback();

const url = `${wsBaseUrl()}/enterprise/security/stream?token=${encodeURIComponent(token)}`;
try {
securitySocket = new WebSocket(url);
} catch {
setSecurityPresence(false, "Security stream unavailable");
return;
}

setSecurityPresence(false, "Security sync connecting");

securitySocket.onopen = () => {
securityBackoffMs = 900;
setSecurityPresence(true, "Live security");
stopSecurityHeartbeat();
startSocketHeartbeat(securitySocket, timer => { securityHeartbeatTimer = timer; });
pushSecurityFeedItem({title: "Security stream connected", body: "Live SOC monitoring is online.", created_at: new Date().toISOString(), severity: "info"});
};

securitySocket.onmessage = (event) => {
let payload = null;
try { payload = JSON.parse(event.data || "{}"); } catch { payload = null; }
if(!payload || typeof payload !== "object") return;

if(payload.event === "snapshot"){
securityFeedItems = Array.isArray(payload.feed) ? payload.feed.slice(0, 50) : [];
renderSecurityLiveFeed(securityFeedItems);
securityIncidents = Array.isArray(payload.incidents) ? payload.incidents : securityIncidents;
renderSecurityIncidents(securityIncidents);
if(selectedSecurityIncidentId){
refreshSelectedSecurityIncident(false);
}
scheduleSecurityRefresh(450);
return;
}

if(payload.event === "security_event" || payload.event === "security_action" || payload.event === "security_incident"){
if(payload.feed_item) pushSecurityFeedItem(payload.feed_item);
if(payload.event === "security_incident" && payload.item?.incident_id){
const id = Number(payload.item.incident_id);
securityIncidents = [payload.item, ...securityIncidents.filter(x => Number(x?.incident_id) !== id)].slice(0, 40);
renderSecurityIncidents(securityIncidents);
if(selectedSecurityIncidentId && Number(selectedSecurityIncidentId) === id){
refreshSelectedSecurityIncident(false);
}
}
scheduleSecurityRefresh(650);
return;
}

if(payload.event === "pong" || payload.event === "heartbeat"){
setSecurityPresence(true, "Live security");
const now = Date.now();
if(payload.coordination && currentView === "security" && now - lastSecurityCoordinationAt > 45000){
lastSecurityCoordinationAt = now;
pushSecurityFeedItem({
id: `security-heartbeat-${payload.generated_at || now}`,
title: payload.coordination.title || "Security continuity check",
body: payload.coordination.body || "Security monitoring remains synchronized.",
created_at: payload.generated_at || new Date().toISOString(),
severity: payload.coordination.severity || "info"
});
}
return;
}

if(payload.event === "error"){
setSecurityPresence(false, "Security sync degraded");
pushSecurityFeedItem({title: "Security sync recovering", body: payload.message || "Live monitoring is restoring continuity.", created_at: new Date().toISOString(), severity: "warning"});
}
};

securitySocket.onclose = () => {
stopSecurityHeartbeat();
setSecurityPresence(false, "Security sync restoring");
if(securityReconnectTimer) return;
const delay = shouldHoldRealtimeReconnect() ? Math.max(8000, securityBackoffMs) : reconnectDelay(securityBackoffMs);
securityReconnectTimer = setTimeout(() => {
securityReconnectTimer = null;
connectSecurityStream();
}, delay);
securityBackoffMs = Math.min(12000, Math.round(securityBackoffMs * 1.55));
};

securitySocket.onerror = () => {
stopSecurityHeartbeat();
setSecurityPresence(false, "Security sync restoring");
try { securitySocket.close(); } catch {}
};
}

async function connectOpsStream(){
if(opsReconnectTimer){
clearTimeout(opsReconnectTimer);
opsReconnectTimer = null;
}
if(opsSocket && (opsSocket.readyState === WebSocket.OPEN || opsSocket.readyState === WebSocket.CONNECTING)){
return;
}

const token = typeof getToken === "function" ? getToken() : "";
if(!token){
setOpsPresence(false, "Ops auth required");
return;
}

if(!(await checkRealtimeCapability())){
startOpsPollingFallback(realtimeHealth.guidance || "Realtime dependency is unavailable; WorkforceOS is using authenticated polling.");
return;
}
stopOpsPollingFallback();

const url = `${wsBaseUrl()}/enterprise/ops/stream?token=${encodeURIComponent(token)}`;
try {
opsSocket = new WebSocket(url);
} catch {
setOpsPresence(false);
return;
}

opsSocket.onopen = () => {
opsBackoffMs = 900;
setOpsPresence(true);
stopOpsHeartbeat();
startSocketHeartbeat(opsSocket, timer => { opsHeartbeatTimer = timer; });
pushOpsFeedItem({title: "Ops stream connected", body: "Live hiring intelligence is online.", created_at: new Date().toISOString(), severity: "info"});
};

opsSocket.onmessage = (event) => {
let payload = null;
try { payload = JSON.parse(event.data || "{}"); } catch { payload = null; }
if(!payload || typeof payload !== "object") return;

if(payload.event === "snapshot"){
opsFeedItems = Array.isArray(payload.feed) ? payload.feed.slice(0, 30) : [];
renderOpsFeed(opsFeedItems);
renderOpsRecommendations(payload.recommendations || []);
return;
}

if(payload.event === "recommendations"){
renderOpsRecommendations(payload.recommendations || []);
return;
}

if(payload.event === "ops_event"){
pushOpsFeedItem(payload.item || null);
return;
}

if(payload.event === "pong" || payload.event === "heartbeat"){
setOpsPresence(true, payload.telemetry?.continuity === "healthy" ? "Live intelligence" : "Live sync");
const now = Date.now();
if(payload.coordination && currentView === "dashboard" && now - lastOpsCoordinationAt > 45000){
lastOpsCoordinationAt = now;
pushOpsFeedItem({
id: `ops-heartbeat-${payload.generated_at || now}`,
title: payload.coordination.title || "WorkforceOS coordination check",
body: payload.coordination.body || "Live operations are synchronized.",
created_at: payload.generated_at || new Date().toISOString(),
severity: payload.coordination.severity || "info"
});
}
return;
}

if(payload.event === "error"){
setOpsPresence(false, "Ops sync degraded");
pushOpsFeedItem({title: "Ops sync recovering", body: payload.message || "Live hiring intelligence is restoring continuity.", created_at: new Date().toISOString(), severity: "warning"});
}
};

opsSocket.onclose = () => {
stopOpsHeartbeat();
setOpsPresence(false);
if(opsReconnectTimer) return;
const delay = shouldHoldRealtimeReconnect() ? Math.max(8000, opsBackoffMs) : reconnectDelay(opsBackoffMs);
opsReconnectTimer = setTimeout(() => {
opsReconnectTimer = null;
connectOpsStream();
}, delay);
opsBackoffMs = Math.min(12000, Math.round(opsBackoffMs * 1.55));
};

opsSocket.onerror = () => {
stopOpsHeartbeat();
setOpsPresence(false, "Sync restoring");
try { opsSocket.close(); } catch {}
};
}

function renderMatchDistributionChart(items){
const canvas = document.getElementById("matchDistributionChart");
if(!canvas) return;
const ctx = canvas.getContext("2d");
if(!ctx) return;

const matchesLocal = Array.isArray(items) ? items : [];
const scores = matchesLocal.map(c => Number(c.match_score ?? c.score ?? 0) || 0).filter(v => Number.isFinite(v));
const bins = new Array(10).fill(0);
for(const s of scores){
const idx = Math.max(0, Math.min(9, Math.floor((Math.max(0, Math.min(100, s)) / 100) * 10)));
bins[Math.min(9, idx)] += 1;
}

const w = canvas.width;
const h = canvas.height;
ctx.clearRect(0, 0, w, h);

// Background grid
ctx.fillStyle = "rgba(11, 18, 32, 0.65)";
ctx.fillRect(0, 0, w, h);
ctx.strokeStyle = "rgba(148, 163, 184, 0.10)";
ctx.lineWidth = 1;
for(let i = 1; i < 5; i++){
const y = Math.round((h / 5) * i);
ctx.beginPath();
ctx.moveTo(0, y);
ctx.lineTo(w, y);
ctx.stroke();
}

const maxVal = Math.max(1, ...bins);
const padding = 10;
const barGap = 6;
const barW = (w - padding * 2 - barGap * 9) / 10;

for(let i = 0; i < 10; i++){
const x = padding + i * (barW + barGap);
const val = bins[i];
const barH = Math.round(((val / maxVal) * (h - 28)));
const y = h - 12 - barH;

ctx.fillStyle = "rgba(34, 197, 94, 0.75)";
if(i >= 7) ctx.fillStyle = "rgba(56, 189, 248, 0.75)";
if(i <= 2) ctx.fillStyle = "rgba(239, 68, 68, 0.55)";

ctx.fillRect(x, y, barW, barH);
}

ctx.fillStyle = "rgba(148, 163, 184, 0.75)";
ctx.font = "12px Inter, Segoe UI, Arial";
ctx.fillText("Match distribution (bucketed)", 10, 18);
}

function buildCommandPaletteItems(){
const top = matches?.[0] || null;
const activeJob = document.getElementById("matchJobDescription")?.value.trim() || jobs?.[0]?.description || "";

const items = [
{
id: "nav-brain",
label: "Go to AI Decision Brain",
hint: "Candidate 360, decisions, next actions",
run: () => switchView("brain")
},
{
id: "brain-top-360",
label: "Analyze top match (Candidate 360)",
hint: top?.candidate_id ? `Top match: ${top.candidate_id}` : "Runs against latest job",
run: async () => {
switchView("brain");
const input = document.getElementById("brainCandidateId");
const jobText = document.getElementById("brainJobDescription");
if(input && top?.candidate_id) input.value = top.candidate_id;
if(jobText && activeJob) jobText.value = activeJob;
await analyzeCandidate360();
}
},
{
id: "modal-top",
label: "Open top match",
hint: "View recruiter summary, strengths, risks, interview plan",
run: () => {
if(matches?.length) openCandidateModalFromMatch(0);
else showAlert("Run ranking first so WorkforceOS has candidate evidence to compare.", "info");
}
},
{
id: "shortlist-auto",
label: "Auto-shortlist (>= 75%)",
hint: "Adds candidates to shortlist using your job description",
run: async () => {
switchView("shortlist");
const threshold = document.getElementById("shortlistThreshold");
if(threshold) threshold.value = "75";
await runAutoShortlist({target: null});
}
},
{
id: "exec-brief",
label: "Generate executive brief",
hint: "Operational highlights, risks, next steps",
run: async () => {
switchView("brain");
await generateExecutiveBrief();
}
},
{
id: "seed-demo",
label: "Prepare enterprise demo data",
hint: "Jobs, candidates, shortlist, scheduling, and operational signals",
run: async () => {
switchView("dashboard");
await loadDemoEnvironment();
}
},
{
id: "copilot-explain-top",
label: "Ask Copilot: explain the top candidate",
hint: "Recruiter-style explanation and next steps",
run: () => {
const id = top?.candidate_id || "";
const prompt = id
? `Explain why ${id} is ranked #1. Summarize strengths, risks, and the next best actions.`
: "Explain the current match results and recommend next actions.";
useChatShortcut(prompt);
}
}
];

// Add a few dynamic candidate quick-open actions.
(matches || []).slice(0, 6).forEach(candidate => {
if(!candidate?.candidate_id) return;
items.push({
id: `candidate-${candidate.candidate_id}`,
label: `Open candidate: ${candidate.candidate_id}`,
hint: candidate.recommendation || "Candidate 360 / match detail",
run: () => openCandidateModalById(candidate.candidate_id)
});
});

return items;
}

function openCommandPalette(){
const modal = document.getElementById("commandPalette");
const input = document.getElementById("commandInput");
if(!modal || !input) return;
paletteOpen = true;
modal.classList.remove("hidden");
paletteItems = buildCommandPaletteItems();
paletteIndex = 0;
input.value = "";
filterCommandPalette("");
input.focus();
}

function closeCommandPalette(){
const modal = document.getElementById("commandPalette");
if(!modal) return;
paletteOpen = false;
modal.classList.add("hidden");
}

function filterCommandPalette(query){
const q = String(query || "").toLowerCase().trim();
paletteFiltered = (paletteItems || []).filter(item => {
const hay = `${item.label || ""} ${item.hint || ""}`.toLowerCase();
return !q || hay.includes(q);
});
paletteIndex = Math.max(0, Math.min(paletteIndex, Math.max(0, paletteFiltered.length - 1)));
renderCommandPalette();
}

function renderCommandPalette(){
const list = document.getElementById("commandList");
if(!list) return;
const items = paletteFiltered || [];
if(!items.length){
list.innerHTML = `<div class="empty-state">No commands found.</div>`;
return;
}
list.innerHTML = items.slice(0, 16).map((item, idx) => `
<div class="palette-item ${idx === paletteIndex ? "active" : ""}" onclick="runPaletteItem(${idx})">
<div>
<strong>${escapeHTML(item.label || "Command")}</strong>
<div><span>${escapeHTML(item.hint || "")}</span></div>
</div>
<span>Enter</span>
</div>
`).join("");
}

async function runPaletteItem(index){
const item = (paletteFiltered || [])[index];
if(!item) return;
closeCommandPalette();
try {
await item.run();
} catch (err) {
showAlert(err.message || "Command failed");
}
}

window.addEventListener("keydown", event => {
const isMac = navigator.platform.toLowerCase().includes("mac");
const openChord = (isMac ? event.metaKey : event.ctrlKey) && (event.key || "").toLowerCase() === "k";
if(openChord){
event.preventDefault();
if(paletteOpen) closeCommandPalette();
else openCommandPalette();
return;
}

if(!paletteOpen) return;

if(event.key === "Escape"){
event.preventDefault();
closeCommandPalette();
return;
}
if(event.key === "ArrowDown"){
event.preventDefault();
paletteIndex = Math.min((paletteFiltered?.length || 1) - 1, paletteIndex + 1);
renderCommandPalette();
return;
}
if(event.key === "ArrowUp"){
event.preventDefault();
paletteIndex = Math.max(0, paletteIndex - 1);
renderCommandPalette();
return;
}
if(event.key === "Enter"){
event.preventDefault();
runPaletteItem(paletteIndex);
}
});

function showLoader(){
const loader = document.getElementById("globalLoader");
loader?.classList.add("visible");
if(loaderSafetyTimer) clearTimeout(loaderSafetyTimer);
loaderSafetyTimer = setTimeout(() => {
loader?.classList.remove("visible");
loaderSafetyTimer = null;
}, 12000);
}

function hideLoader(){
if(loaderSafetyTimer){
clearTimeout(loaderSafetyTimer);
loaderSafetyTimer = null;
}
document.getElementById("globalLoader")?.classList.remove("visible");
}

function showAlert(message, type = "error"){
const alertBox = document.getElementById("alertBox");
const cleanMessage = normalizeUserMessage(message);
const signature = `${type}:${cleanMessage}`;
const now = Date.now();
if(signature === lastAlertSignature && now - lastAlertAt < 2200){
return;
}
lastAlertSignature = signature;
lastAlertAt = now;
if(!alertBox){
console[type === "error" ? "error" : "log"](cleanMessage);
return;
}
alertBox.className = `alert ${type}`;
alertBox.innerText = cleanMessage;

setTimeout(() => {
alertBox.className = "alert hidden";
}, 4500);
}

function normalizeUserMessage(message){
const raw = String(message || "The workspace needs a moment. Please try again.").trim();
if(/failed to fetch|network|unable to reach api|request timeout/i.test(raw)){
return "The workspace connection is taking longer than expected. Check the API service and retry when it settles.";
}
if(/not authenticated|missing bearer|authentication required|session expired/i.test(raw)){
return "Your secure session needs to be refreshed. Please sign in again to continue.";
}
if(/validation|field required|invalid value/i.test(raw)){
return "A required detail is missing or malformed. Review the highlighted fields and try again.";
}
if(/unable to load|could not load/i.test(raw)){
return "That workspace view needs a moment to refresh. Try again once the signal settles.";
}
if(/unable to approve|unable to reject|unable to update shortlist/i.test(raw)){
return "The candidate decision was not recorded yet. Recheck the shortlist state and try the action again.";
}
if(/failed|unavailable/i.test(raw)){
return raw
.replace(/^Failed to /i, "Could not ")
.replace(/ unavailable\.?$/i, " is not ready yet.");
}
return raw.replace(/^Failed to /i, "Could not ");
}

window.addEventListener("error", event => {
console.warn("[UI RECOVERED]", event.error || event.message);
hideLoader();
showAlert("The interface hit a recoverable error. Please retry the action.");
});

window.addEventListener("unhandledrejection", event => {
console.warn("[UI REQUEST RECOVERED]", event.reason);
hideLoader();
showAlert(event.reason?.message || "A network or application request failed.");
});

window.addEventListener("offline", () => {
setOpsPresence(false, "Connection paused");
setSecurityPresence(false, "Security sync paused");
showAlert("Connection paused. WorkforceOS will reconnect when the browser is back online.", "info");
});

window.addEventListener("online", () => {
showAlert("Connection restored. Live operations are reconnecting.", "success");
if(currentView === "dashboard") connectOpsStream();
if(currentView === "security") connectSecurityStream();
refreshCurrentView().catch(() => null);
});

function registerPWA(){
if(!("serviceWorker" in navigator)) return;
navigator.serviceWorker.register("service-worker.js").catch(error => {
console.warn("[PWA] Service worker registration skipped", error);
});
}

function setupResumeDropZone(){
const dropZone = document.getElementById("resumeDropZone");
const fileInput = document.getElementById("resumeFileInput");
if(!dropZone || !fileInput || dropZone.dataset.ready) return;
dropZone.dataset.ready = "true";

["dragenter", "dragover"].forEach(type => {
dropZone.addEventListener(type, event => {
event.preventDefault();
dropZone.classList.add("drag-over");
});
});
["dragleave", "drop"].forEach(type => {
dropZone.addEventListener(type, event => {
event.preventDefault();
dropZone.classList.remove("drag-over");
});
});
dropZone.addEventListener("drop", event => {
if(event.dataTransfer?.files?.length){
fileInput.files = event.dataTransfer.files;
showAlert(`${event.dataTransfer.files[0].name} ready to upload.`, "success");
}
});
fileInput.addEventListener("change", () => {
if(fileInput.files?.[0]){
showAlert(`${fileInput.files[0].name} selected.`, "success");
}
});
}
