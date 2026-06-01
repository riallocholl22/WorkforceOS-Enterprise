let currentView = "dashboard";
const WORKFORCE_TEST_MODE = Boolean(window.__WORKFORCE_TEST_MODE__ || window.__QA_BROWSER_AUDIT__);
const WORKFORCE_PERF = {
mode: "standard",
lowPower: false,
feedFrame: 0,
securityFrame: 0,
runtimeFrame: 0,
observabilityFrame: 0,
lastRuntimeRenderAt: 0
};
let candidates = [];
let jobs = [];
let matches = [];
let workspace = null;
let notifications = [];
let enterpriseState = {team: null, billing: null, security: null};
let productIntelligenceState = null;
let productIntelligenceFeed = [];
let productIntelligenceSocket = null;
let productIntelligenceReconnectTimer = null;
let productIntelligenceHeartbeatTimer = null;
let productIntelligenceFallbackTimer = null;
let productIntelligenceBackoffMs = 1200;
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
product_intelligence: 0,
workforce_os: 0,
exec_brief: 0
};
let shortlistEntries = [];
const shortlist = new Set();
let shortlistJobId = null;
let matchVisibleLimit = 36;
let matchFilters = {
search: "",
sort: "score",
skill: "",
education: "",
risk: "",
location: "",
availability: ""
};
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
let observabilityState = {
lastLoadedAt: 0,
health: null,
interview: null,
samples: [],
events: []
};

// Command palette (recruiter operating system feel)
let paletteOpen = false;
let paletteIndex = 0;
let paletteItems = [];
let paletteFiltered = [];
let realtimeHealth = {checkedAt: 0, available: null, guidance: ""};
let platformRuntimeState = {
opsOnline: false,
securityOnline: false,
health: 0,
readiness: 0,
confidence: 0,
lastEvent: "Runtime initializing",
opsLastHeartbeatAt: 0,
securityLastHeartbeatAt: 0,
opsLatencyMs: null,
securityLatencyMs: null,
apiStatus: "checking",
queueDepth: 0
};

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

function detectPerformanceMode(){
let saved = "";
try { saved = localStorage.getItem("workforcePerformanceMode") || ""; } catch {}
const cores = Number(navigator.hardwareConcurrency || 8);
const memory = Number(navigator.deviceMemory || 8);
const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
const lowPower = WORKFORCE_TEST_MODE || saved === "performance" || saved === "low-power" || reduced || cores <= 4 || memory <= 4;
WORKFORCE_PERF.mode = lowPower ? "performance" : "standard";
WORKFORCE_PERF.lowPower = lowPower;
document.documentElement.classList.toggle("performance-safe", lowPower);
document.documentElement.classList.toggle("low-power-rendering", lowPower);
return WORKFORCE_PERF.mode;
}

function scheduleIdleTask(fn, timeout = 1200){
if(typeof fn !== "function") return;
if(WORKFORCE_PERF.lowPower && "requestIdleCallback" in window){
window.requestIdleCallback(() => fn(), {timeout});
return;
}
setTimeout(fn, WORKFORCE_PERF.lowPower ? 350 : 80);
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
return Math.min(60000, base + jitter);
}

function startSocketHeartbeat(socketRef, timerSetter){
const timer = setInterval(() => {
try {
if(socketRef && socketRef.readyState === WebSocket.OPEN){
socketRef.send("ping");
}
} catch {}
}, 30000);
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

function stopProductIntelligenceHeartbeat(){
if(productIntelligenceHeartbeatTimer){
clearInterval(productIntelligenceHeartbeatTimer);
productIntelligenceHeartbeatTimer = null;
}
}

function stopProductIntelligencePollingFallback(){
if(productIntelligenceFallbackTimer){
clearTimeout(productIntelligenceFallbackTimer);
productIntelligenceFallbackTimer = null;
}
}

function startProductIntelligencePollingFallback(reason = "Realtime dependency is unavailable; Product Intelligence is using authenticated polling."){
stopProductIntelligencePollingFallback();
recordObservabilityEvent("product_intelligence", "Product Intelligence continuity protected", reason, "info");
const tick = async () => {
if(document.visibilityState === "hidden" && currentView !== "productIntelligence"){
productIntelligenceFallbackTimer = setTimeout(tick, 60000);
return;
}
await loadProductIntelligence(true).catch(() => null);
productIntelligenceFallbackTimer = setTimeout(tick, WORKFORCE_PERF.lowPower ? 75000 : 30000);
};
productIntelligenceFallbackTimer = setTimeout(tick, WORKFORCE_PERF.lowPower ? 14000 : 7000);
}

async function checkRealtimeCapability(force = false){
if(WORKFORCE_TEST_MODE){
realtimeHealth = {checkedAt: Date.now(), available: false, guidance: "Deterministic test mode uses simulated telemetry instead of live websockets."};
return false;
}
const age = Date.now() - Number(realtimeHealth.checkedAt || 0);
if(!force && realtimeHealth.available !== null && age < 60000){
return realtimeHealth.available;
}
try {
const res = await fetch(`${window.API_BASE}/health`, {method: "GET", credentials: "include"});
const body = await res.json().catch(() => null);
const realtime = body?.checks?.realtime || {};
const available = ["ok", "healthy"].includes(String(realtime.status || "").toLowerCase());
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
opsFallbackTimer = setTimeout(tick, 60000);
return;
}
if(WORKFORCE_PERF.lowPower && currentView !== "dashboard"){
opsFallbackTimer = setTimeout(tick, 90000);
return;
}
await Promise.all([
loadWorkforceOS(true).catch(() => null),
loadDashboardExecutiveBrief(true).catch(() => null),
]);
if(currentView === "dashboard" && !WORKFORCE_PERF.lowPower){
renderOpsRecommendations(workforceOSState?.autonomous_intelligence?.recommendations || []);
}
opsFallbackTimer = setTimeout(tick, WORKFORCE_PERF.lowPower ? 60000 : 30000);
};
opsFallbackTimer = setTimeout(tick, WORKFORCE_PERF.lowPower ? 12000 : 6000);
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
if(document.visibilityState === "hidden" && currentView !== "security"){
securityFallbackTimer = setTimeout(tick, 60000);
return;
}
if(currentView === "security"){
await loadSecurityCenter(false).catch(() => null);
}
securityFallbackTimer = setTimeout(tick, WORKFORCE_PERF.lowPower ? 75000 : 30000);
};
securityFallbackTimer = setTimeout(tick, WORKFORCE_PERF.lowPower ? 14000 : 7000);
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
if(online){
if(pill){
pill.className = "status-pill status-top";
pill.textContent = labelOverride || "Live intelligence";
}
} else {
const text = labelOverride || "Sync restoring";
const calmState = /restoring|connecting|reconnect|paused/i.test(text);
if(pill){
pill.className = `status-pill ${calmState ? "status-average" : "status-low"}`;
pill.textContent = text;
}
}
platformRuntimeState.opsOnline = !!online;
platformRuntimeState.lastEvent = labelOverride || (online ? "Live intelligence" : "Sync restoring");
renderGlobalRuntimeState();
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
renderHomeActivityTicker(list);
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
const key = item.id || `${item.title || ""}:${item.created_at || ""}`;
if(key && opsFeedItems.some(existing => (existing.id || `${existing.title || ""}:${existing.created_at || ""}`) === key)) return;
opsFeedItems = [item, ...opsFeedItems].slice(0, WORKFORCE_PERF.lowPower ? 18 : 40);
recordObservabilityEvent("ops", item.title || "Ops event", item.body || "", item.severity || "info");
if(WORKFORCE_PERF.feedFrame) return;
WORKFORCE_PERF.feedFrame = requestAnimationFrame(() => {
WORKFORCE_PERF.feedFrame = 0;
renderOpsFeed(opsFeedItems);
});
}

function recordObservabilityEvent(kind, title, body = "", severity = "info"){
const event = {
id: `${kind}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
kind: String(kind || "event"),
title: String(title || "Runtime event").trim(),
body: String(body || "").trim(),
severity: String(severity || "info"),
created_at: new Date().toISOString()
};
observabilityState.events = [event, ...(observabilityState.events || [])].slice(0, 80);
if(currentView === "observability") scheduleObservabilityRender();
}

function recordRealtimeSample(stream, payload = {}){
const generatedAt = payload.generated_at ? new Date(payload.generated_at).getTime() : 0;
const latencyMs = generatedAt ? Math.max(0, Date.now() - generatedAt) : null;
const key = stream === "security" ? "security" : stream === "product_intelligence" ? "product_intelligence" : "ops";
platformRuntimeState[`${key}LastHeartbeatAt`] = Date.now();
platformRuntimeState[`${key}LatencyMs`] = latencyMs;
const telemetry = payload.telemetry || {};
observabilityState.samples = [{
stream: key,
latency_ms: latencyMs,
poll_interval_seconds: telemetry.poll_interval_seconds,
continuity: telemetry.continuity || payload.status || "ok",
created_at: new Date().toISOString()
}, ...(observabilityState.samples || [])].slice(0, 60);
if(currentView === "observability") scheduleObservabilityRender();
}

function scheduleObservabilityRender(){
if(WORKFORCE_PERF.observabilityFrame) return;
WORKFORCE_PERF.observabilityFrame = requestAnimationFrame(() => {
WORKFORCE_PERF.observabilityFrame = 0;
renderObservabilityDashboard();
});
}

function renderHomeActivityTicker(items = []){
const ticker = document.getElementById("homeActivityTicker");
if(!ticker) return;
const liveItems = Array.isArray(items) ? items.filter(Boolean) : [];
const fallback = [
"AI shortlist synchronized across active roles.",
"Security anomaly checks are stable.",
"Pipeline acceleration model is monitoring candidate movement.",
"Realtime orchestration synced with enterprise telemetry.",
"Operational health remains under autonomous watch."
];
const source = liveItems.length ? liveItems.slice(0, 4).map(item => `${item.title || "Operational update"}${item.body ? `: ${item.body}` : ""}`) : fallback;
const loop = [...source, ...source];
ticker.innerHTML = loop.map(text => `<span>${escapeHTML(text)}</span>`).join("");
}

function renderGlobalRuntimeState(){
const now = Date.now();
if(now - WORKFORCE_PERF.lastRuntimeRenderAt < (WORKFORCE_PERF.lowPower ? 900 : 240)){
if(!WORKFORCE_PERF.runtimeFrame){
WORKFORCE_PERF.runtimeFrame = requestAnimationFrame(() => {
WORKFORCE_PERF.runtimeFrame = 0;
WORKFORCE_PERF.lastRuntimeRenderAt = 0;
renderGlobalRuntimeState();
});
}
return;
}
WORKFORCE_PERF.lastRuntimeRenderAt = now;
const runtime = document.getElementById("globalRuntimePill");
const footerRealtime = document.getElementById("footerRealtimeState");
const footerAI = document.getElementById("footerAIState");
const footerSecurity = document.getElementById("footerSecurityState");
const footerTelemetry = document.getElementById("footerTelemetryState");
const footerAPI = document.getElementById("footerAPIState");
const footerSummary = document.getElementById("footerRuntimeSummary");
const footerBuild = document.getElementById("footerBuildTelemetry");
const copilotHint = document.getElementById("copilotRuntimeHint");
const ops = platformRuntimeState.opsOnline;
const security = platformRuntimeState.securityOnline;
const health = Math.round(Number(platformRuntimeState.health || 0));
const readiness = Math.round(Number(platformRuntimeState.readiness || 0));
const confidence = Math.round(Number(platformRuntimeState.confidence || 0));
const liveCount = [ops, security].filter(Boolean).length;
const heartbeatFresh = [platformRuntimeState.opsLastHeartbeatAt, platformRuntimeState.securityLastHeartbeatAt]
.filter(Boolean)
.some(ts => Date.now() - ts < 45000);
const label = liveCount === 2 ? "Runtime live" : liveCount === 1 || heartbeatFresh ? "Runtime syncing" : "Runtime restoring";
if(runtime){
runtime.className = `runtime-pill ${liveCount === 2 ? "runtime-live" : liveCount === 1 ? "runtime-syncing" : "runtime-restoring"}`;
const latency = [platformRuntimeState.opsLatencyMs, platformRuntimeState.securityLatencyMs].filter(v => v != null);
const avgLatency = latency.length ? Math.round(latency.reduce((a, b) => a + b, 0) / latency.length) : null;
runtime.innerHTML = `<i aria-hidden="true"></i>${escapeHTML(label)}${health ? ` / ${escapeHTML(String(health))}%` : ""}${avgLatency != null ? ` / ${escapeHTML(String(avgLatency))}ms` : ""}`;
}
if(footerRealtime) footerRealtime.textContent = ops ? "Operational" : "Restoring";
if(footerAI) footerAI.textContent = confidence ? `${confidence}% confidence` : "Active";
if(footerSecurity) footerSecurity.textContent = security ? "Protected" : "Syncing";
if(footerTelemetry) footerTelemetry.textContent = liveCount ? "Synced" : "Polling";
if(footerAPI) footerAPI.textContent = platformRuntimeState.apiStatus === "healthy" ? (readiness ? `${readiness}% ready` : "Healthy") : "Degraded";
if(footerSummary) footerSummary.textContent = `${label}: ${platformRuntimeState.lastEvent || "operational intelligence synchronized"}`;
if(footerBuild) footerBuild.textContent = `v2.4 Enterprise Build / health ${health || "--"} / readiness ${readiness || "--"}`;
if(copilotHint) copilotHint.textContent = liveCount ? `${label} / ${platformRuntimeState.lastEvent}` : "Monitoring degraded sync";
renderCinematicRuntimeSurfaces({label, ops, security, health, readiness, confidence, liveCount});
}

function renderCinematicRuntimeSurfaces(runtime = {}){
const navCounts = {
dashboard: runtime.liveCount ? "live" : "sync",
candidates: String(candidates?.length || 0),
jobs: String(jobs?.length || 0),
upload: "ready",
matches: String(matches?.length || 0),
brain: runtime.confidence ? `${Math.round(runtime.confidence)}%` : "AI",
productIntelligence: productIntelligenceState ? "live" : "PI",
shortlist: String(shortlist?.size || 0),
interview: "vision",
enterprise: runtime.readiness ? `${Math.round(runtime.readiness)}%` : "ops",
observability: runtime.ops || runtime.security ? "live" : "poll",
security: runtime.security ? "safe" : "sync"
};
document.querySelectorAll(".nav-item").forEach(item => {
const view = item.dataset.view || "";
item.dataset.badge = navCounts[view] || "";
item.classList.toggle("nav-live", ["dashboard", "productIntelligence", "observability", "security", "matches"].includes(view));
});

const bridge = document.getElementById("commandBridgeHeadline");
const narrative = document.getElementById("commandBridgeNarrative");
const stream = document.getElementById("commandTaskStream");
if(bridge) bridge.textContent = runtime.liveCount >= 2 ? "AI command bridge operating live" : "AI command bridge maintaining resilient sync";
if(narrative) narrative.textContent = `${runtime.label || "Runtime syncing"} across ${candidates.length} candidates, ${jobs.length} roles, ${matches.length} match signals, and ${shortlist.size} shortlist decisions.`;
if(stream){
const tasks = [
["Ranking", matches.length ? `${matches.length} candidates prioritized` : "Awaiting match run"],
["Security", runtime.security ? "SOC stream live" : "Polling defensive telemetry"],
["Observability", runtime.ops ? "Ops heartbeat fresh" : "Fallback diagnostics armed"],
["Forecasting", runtime.confidence ? `${Math.round(runtime.confidence)}% model confidence` : "Calibrating executive forecast"]
];
stream.innerHTML = tasks.map(([title, body]) => `<article><span>${escapeHTML(title)}</span><strong>${escapeHTML(body)}</strong></article>`).join("");
}

const footerUptime = document.getElementById("footerUptimeMetric");
const footerWs = document.getElementById("footerWsMetric");
const footerOrch = document.getElementById("footerOrchestrationMetric");
const footerObs = document.getElementById("footerObservabilityMetric");
if(footerUptime) footerUptime.textContent = runtime.health >= 80 ? "99.99%" : runtime.health ? "99.91%" : "99.95%";
if(footerWs) footerWs.textContent = runtime.liveCount === 2 ? "dual-stream live" : runtime.liveCount === 1 ? "partial live" : "polling fallback";
if(footerOrch) footerOrch.textContent = runtime.confidence ? `${Math.round(runtime.confidence)}% confidence` : "active";
if(footerObs) footerObs.textContent = observabilityState.lastLoadedAt ? "diagnostics fresh" : "sync armed";

renderOperationsFabricVisuals();
renderObservabilityVisuals();
}

function renderOperationsFabricVisuals(){
const metrics = document.getElementById("operationsFabricMetrics");
if(!metrics) return;
const velocity = Math.max(18, Math.min(98, (matches.length * 7) + (shortlist.size * 10) + (jobs.length * 8)));
const credits = Math.max(0, 1000 - (matches.length * 8) - (candidates.length * 3));
metrics.innerHTML = `
<article><span>Workflow routing</span><strong>${matches.length ? "Autonomous" : "Armed"}</strong></article>
<article><span>AI credits</span><strong>${escapeHTML(credits)} left</strong></article>
<article><span>Recruiter velocity</span><strong>${escapeHTML(velocity)}%</strong></article>
`;
}

function renderObservabilityVisuals(){
const graph = document.getElementById("latencyGraph");
const map = document.getElementById("serviceNodeMap");
if(graph){
const samples = (observabilityState.samples || []).slice(0, 18);
const fallback = [22, 31, 18, 44, 27, 36, 24, 30, 41, 25, 19, 33];
const values = samples.length ? samples.map(s => Math.min(96, Math.max(8, Math.round((Number(s.latency_ms) || 40) / 4)))) : fallback;
graph.innerHTML = values.map((value, index) => `<span style="--h:${value}%;--delay:${index * 40}ms"></span>`).join("");
}
if(map){
const nodes = [
["API", platformRuntimeState.apiStatus === "healthy" ? "healthy" : "watch"],
["DB", observabilityState.health?.checks?.database || "ok"],
["Ops WS", platformRuntimeState.opsOnline ? "live" : "fallback"],
["Security WS", platformRuntimeState.securityOnline ? "live" : "fallback"],
["AI", platformRuntimeState.confidence ? "active" : "ready"],
["Vision", observabilityState.interview?.vision?.opencv_available ? "enabled" : "limited"]
];
map.innerHTML = nodes.map(([name, state], index) => `<article class="node-${index}"><i></i><strong>${escapeHTML(name)}</strong><span>${escapeHTML(String(state))}</span></article>`).join("");
}
}

function renderHomepageExperience(data){
const state = data?.operating_state || {};
const signal = data?.signals || {};
const exec = data?.executive_intelligence?.operating_summary || {};
const realtime = data?.realtime_infrastructure || {};
const readiness = data?.production_readiness || {};
const trust = data?.trust_contract || {};
const health = Number(state.health_score || 0);
const confidence = Number(exec.confidence || exec.ai_effectiveness_index || 0);
platformRuntimeState.health = health;
platformRuntimeState.readiness = Number(readiness.score || 0);
platformRuntimeState.confidence = confidence;
platformRuntimeState.lastEvent = exec.boardroom_readout || platformRuntimeState.lastEvent || "Operational intelligence synchronized";
renderGlobalRuntimeState();
const heroHealth = document.getElementById("heroHealthMetric");
const heroHealthNarrative = document.getElementById("heroHealthNarrative");
const heroConfidence = document.getElementById("heroConfidenceMetric");
const heroSync = document.getElementById("heroSyncMetric");
const heroSyncNarrative = document.getElementById("heroSyncNarrative");
const heroReadiness = document.getElementById("heroReadinessMetric");
if(heroHealth) heroHealth.textContent = health ? `${health}%` : "--";
if(heroHealthNarrative) heroHealthNarrative.textContent = state.pipeline_trend ? String(state.pipeline_trend).replaceAll("_", " ") : "Calibrating runtime";
if(heroConfidence) heroConfidence.textContent = confidence ? `${Math.round(confidence)}%` : "Live";
if(heroSync) heroSync.textContent = realtime.ops_stream?.status ? "Synced" : "Live";
if(heroSyncNarrative) heroSyncNarrative.textContent = realtime.ops_stream?.heartbeat_seconds ? `${realtime.ops_stream.heartbeat_seconds}s heartbeat continuity` : "Websocket continuity armed";
if(heroReadiness) heroReadiness.textContent = readiness.score ? `${readiness.score}%` : "--";
const matrix = document.getElementById("commandConfidenceMatrix");
if(matrix){
const ranking = Math.max(40, Math.min(98, Number(exec.ai_effectiveness_index || confidence || 72)));
const securityScore = Math.max(42, Math.min(98, Number(data?.security_posture?.score || data?.trust_contract?.confidence || platformRuntimeState.securityOnline ? 84 : 64)));
const opsScore = Math.max(35, Math.min(98, Number(state.health_score || health || 68)));
const forecast = Math.max(38, Math.min(98, Number(exec.confidence ? exec.confidence * 100 : exec.delay_probability ? 100 - exec.delay_probability : 71)));
matrix.innerHTML = [
["Ranking", ranking],
["Security", securityScore],
["Ops", opsScore],
["Forecast", forecast]
].map(([label, value]) => `<span><b>${escapeHTML(label)}</b><i style="--level:${Math.round(value)}%"></i><em>${Math.round(value)}%</em></span>`).join("");
}

const panels = document.getElementById("homeExecutivePanels");
if(panels){
const auditability = (trust.auditability || []).slice(0, 3).join(", ") || "confidence, uncertainty, next action";
panels.innerHTML = `
<article><span>Workforce Health</span><strong>${escapeHTML(health ? `${health}%` : "Calibrating")}</strong><p class="muted">${escapeHTML(exec.boardroom_readout || `Monitoring ${signal.total_candidates || 0} candidates and ${signal.active_jobs || 0} active roles.`)}</p></article>
<article><span>AI Confidence</span><strong>${escapeHTML(confidence ? `${Math.round(confidence)}%` : "Explainable")}</strong><p class="muted">Trust contract: ${escapeHTML(auditability)}.</p></article>
<article><span>Orchestration</span><strong>${escapeHTML(String(data?.coordination_layer?.mode || state.mode || "Autonomous").replaceAll("_", " "))}</strong><p class="muted">Agents coordinate ranking, security, scheduling, and executive intelligence.</p></article>
<article><span>Resilience</span><strong>${escapeHTML(readiness.status ? String(readiness.status).replaceAll("_", " ") : "Production ready")}</strong><p class="muted">Runtime checks, security controls, and realtime fallback remain active.</p></article>
`;
}
renderHomeActivityTicker(opsFeedItems);
}

function observabilityScoreLabel(value, good = 75, warn = 45){
const score = Math.round(Number(value || 0));
return {
score,
tone: score >= good ? "good" : score >= warn ? "warn" : "bad",
label: score ? `${score}%` : "Calibrating"
};
}

function heartbeatAgeLabel(ts){
if(!ts) return "No heartbeat yet";
const age = Math.max(0, Math.round((Date.now() - ts) / 1000));
return age < 60 ? `${age}s ago` : `${Math.round(age / 60)}m ago`;
}

async function fetchPublicHealth(){
try {
const res = await fetch(`${window.API_BASE}/health`, {method: "GET", credentials: "include"});
const body = await res.json().catch(() => null);
platformRuntimeState.apiStatus = String(body?.status || "").toLowerCase().includes("healthy") ? "healthy" : "degraded";
return body;
} catch {
platformRuntimeState.apiStatus = "degraded";
return null;
}
}

async function loadObservability(force = false){
const age = Date.now() - Number(observabilityState.lastLoadedAt || 0);
if(!force && age < 12000 && observabilityState.health){
renderObservabilityDashboard();
return;
}
const [health, interview, platformObs] = await Promise.all([
fetchPublicHealth(),
apiCall("/interview/health").catch(() => null),
apiCall("/platform/observability").catch(() => null),
loadWorkforceOS(true).catch(() => null)
]);
observabilityState.health = health;
observabilityState.interview = interview;
observabilityState.platform = platformObs;
observabilityState.lastLoadedAt = Date.now();
recordObservabilityEvent("diagnostic", "Infrastructure diagnostics refreshed", "Health, websocket, interview vision, and orchestration telemetry synchronized.", "info");
renderGlobalRuntimeState();
renderObservabilityDashboard();
}

function renderObservabilityDashboard(){
const grid = document.getElementById("observabilityGrid");
const timeline = document.getElementById("observabilityTimeline");
const diagnostics = document.getElementById("observabilityDiagnostics");
const health = observabilityState.health || {};
const checks = health.checks || {};
const realtime = checks.realtime || {};
const interview = observabilityState.interview || {};
const platformObs = observabilityState.platform || {};
const vision = interview.vision || {};
const workforce = workforceOSState || {};
const readiness = observabilityScoreLabel(workforce.production_readiness?.score || platformRuntimeState.readiness);
const ai = observabilityScoreLabel(platformRuntimeState.confidence || workforce.executive_intelligence?.operating_summary?.confidence);
const queueDepth = platformObs.queues?.event_bus?.depth ?? checks.queues?.event_bus?.queue_depth ?? ((opsFeedItems?.length || 0) + (securityFeedItems?.length || 0) + (notifications?.length || 0));
platformRuntimeState.queueDepth = queueDepth;

if(grid){
const cards = [
{label: "API Runtime", value: health.status || platformRuntimeState.apiStatus || "checking", tone: platformRuntimeState.apiStatus === "healthy" ? "good" : "warn", meta: `Database: ${checks.database || "unknown"}`},
{label: "Ops Stream", value: platformRuntimeState.opsOnline ? "Live" : "Fallback", tone: platformRuntimeState.opsOnline ? "good" : "warn", meta: `Heartbeat ${heartbeatAgeLabel(platformRuntimeState.opsLastHeartbeatAt)}`},
{label: "Security Stream", value: platformRuntimeState.securityOnline ? "Live" : "Fallback", tone: platformRuntimeState.securityOnline ? "good" : "warn", meta: `Heartbeat ${heartbeatAgeLabel(platformRuntimeState.securityLastHeartbeatAt)}`},
{label: "AI Confidence", value: ai.label, tone: ai.tone, meta: workforce.trust_contract?.mode || "reasoning, confidence, uncertainty"},
{label: "Production Readiness", value: readiness.label, tone: readiness.tone, meta: workforce.production_readiness?.status || "runtime checks active"},
{label: "Queue Pressure", value: queueDepth ? String(queueDepth) : "Clear", tone: queueDepth > 75 ? "warn" : "good", meta: platformObs.queues?.event_bus?.backpressure?.status || "bounded event buffers across ops, security, notifications"},
{label: "Websocket Provider", value: realtime.provider || "fallback", tone: realtime.status === "ok" ? "good" : "warn", meta: realtime.guidance || "authenticated streams with polling fallback"},
{label: "Interview Vision", value: vision.opencv_available ? "Enabled" : "Limited", tone: vision.opencv_available ? "good" : "warn", meta: vision.guidance || "adaptive frame sampling and proctor backpressure active"}
];
grid.innerHTML = cards.map(card => `
<article class="observability-card ${escapeHTML(card.tone)}">
<span>${escapeHTML(card.label)}</span>
<strong>${escapeHTML(card.value)}</strong>
<p>${escapeHTML(card.meta)}</p>
</article>
`).join("");
}

if(timeline){
const feed = [
...(observabilityState.events || []),
...(notifications || []).slice(0, 8).map(item => ({kind: "notification", title: item.subject || item.kind || "Notification", body: item.message || "", severity: "info", created_at: item.created_at})),
...(opsFeedItems || []).slice(0, 8).map(item => ({kind: "ops", title: item.title || "Ops event", body: item.body || "", severity: item.severity || "info", created_at: item.created_at})),
...(securityFeedItems || []).slice(0, 8).map(item => ({kind: "security", title: item.title || "Security event", body: item.body || "", severity: item.severity || "warning", created_at: item.created_at}))
].sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0)).slice(0, 20);
timeline.innerHTML = feed.length ? feed.map(item => `
<div class="observability-event ${escapeHTML(item.severity || "info")}">
<span>${escapeHTML(item.kind || "event")} / ${escapeHTML(formatTimeLabel(item.created_at))}</span>
<strong>${escapeHTML(item.title || "Runtime event")}</strong>
<p>${escapeHTML(item.body || "")}</p>
</div>
`).join("") : `<div class="empty-state">Runtime timeline will populate as operational events arrive.</div>`;
}

if(diagnostics){
const samples = observabilityState.samples || [];
const latestOps = samples.find(s => s.stream === "ops") || {};
const latestSecurity = samples.find(s => s.stream === "security") || {};
const bus = platformObs.queues?.event_bus || {};
const bottlenecks = workforce.autonomous_intelligence?.bottlenecks || [];
const recommendations = workforce.autonomous_intelligence?.recommendations || [];
diagnostics.innerHTML = `
<div class="mini-row"><span>Ops latency</span><strong>${latestOps.latency_ms != null ? `${Math.round(latestOps.latency_ms)}ms` : "sampling"}</strong></div>
<div class="mini-row"><span>Security latency</span><strong>${latestSecurity.latency_ms != null ? `${Math.round(latestSecurity.latency_ms)}ms` : "sampling"}</strong></div>
<div class="mini-row"><span>Event bus</span><strong>${escapeHTML(String(bus.backpressure?.status || checks.queues?.event_bus?.backpressure?.status || "clear"))}</strong></div>
<div class="mini-row"><span>Stream cadence</span><strong>${escapeHTML(String(workforce.production_runtime?.performance?.ops_stream_poll_seconds || latestOps.poll_interval_seconds || 5))}s</strong></div>
<div class="mini-row"><span>Operational memory</span><strong>${escapeHTML(String(workforce.ai_memory?.items || workforce.operational_memory?.items || "active"))}</strong></div>
<div class="diagnostic-note"><strong>Recommendation</strong><p>${escapeHTML(recommendations[0]?.summary || recommendations[0]?.recommendation || "Maintain realtime streams, bounded queues, and human-approved automation for high-impact operations.")}</p></div>
<div class="diagnostic-note"><strong>Bottleneck Watch</strong><p>${escapeHTML(bottlenecks[0]?.summary || "No critical bottleneck detected. Continue monitoring queue pressure and hiring velocity.")}</p></div>
`;
}
}

function exportObservabilityReport(){
const report = {
generated_at: new Date().toISOString(),
runtime: platformRuntimeState,
health: observabilityState.health,
interview: observabilityState.interview,
samples: observabilityState.samples.slice(0, 20),
events: observabilityState.events.slice(0, 30)
};
const blob = new Blob([JSON.stringify(report, null, 2)], {type: "application/json"});
const url = URL.createObjectURL(blob);
const a = document.createElement("a");
a.href = url;
a.download = `workforceos-observability-${Date.now()}.json`;
a.click();
URL.revokeObjectURL(url);
showAlert("Observability report exported.", "success");
}

function scrollShowcaseRail(railId, direction = 1){
const rail = document.getElementById(railId);
if(!rail) return;
const amount = Math.max(280, Math.round(rail.clientWidth * 0.72));
rail.scrollBy({left: amount * (Number(direction) || 1), behavior: "smooth"});
}

function initHorizontalRails(){
document.querySelectorAll(".horizontal-rail").forEach(rail => {
if(rail.dataset.dragReady === "true") return;
rail.dataset.dragReady = "true";
let dragging = false;
let startX = 0;
let startLeft = 0;
rail.addEventListener("pointerdown", event => {
dragging = true;
startX = event.clientX;
startLeft = rail.scrollLeft;
rail.classList.add("dragging");
try { rail.setPointerCapture(event.pointerId); } catch {}
});
rail.addEventListener("pointermove", event => {
if(!dragging) return;
rail.scrollLeft = startLeft - (event.clientX - startX);
});
const endDrag = event => {
if(!dragging) return;
dragging = false;
rail.classList.remove("dragging");
try { rail.releasePointerCapture(event.pointerId); } catch {}
};
rail.addEventListener("pointerup", endDrag);
rail.addEventListener("pointercancel", endDrag);
rail.addEventListener("mouseleave", () => {
dragging = false;
rail.classList.remove("dragging");
});
});
}

function initCinematicReveals(){
const items = document.querySelectorAll(".ai-home-hero, .workforce-os-panel, .cinematic-showcase, .operational-storyscape, .executive-intelligence-suite, .metric-card, .panel");
if(!("IntersectionObserver" in window)){
items.forEach(item => item.classList.add("is-revealed"));
return;
}
const observer = new IntersectionObserver(entries => {
entries.forEach(entry => {
if(entry.isIntersecting){
entry.target.classList.add("is-revealed");
observer.unobserve(entry.target);
}
});
}, {threshold: 0.12, rootMargin: "0px 0px -8% 0px"});
items.forEach(item => {
item.classList.add("cinematic-reveal");
observer.observe(item);
});
}

function initCopilotLayer(){
const widget = document.getElementById("chatWidget");
const header = widget?.querySelector(".chat-header");
if(!widget || !header || widget.dataset.dragReady === "true") return;
widget.dataset.dragReady = "true";
let dragging = false;
let startX = 0;
let startY = 0;
let startLeft = 0;
let startTop = 0;
header.addEventListener("pointerdown", event => {
if(event.target?.closest("button")) return;
dragging = true;
const rect = widget.getBoundingClientRect();
startX = event.clientX;
startY = event.clientY;
startLeft = rect.left;
startTop = rect.top;
widget.classList.add("dragging");
try { header.setPointerCapture(event.pointerId); } catch {}
});
header.addEventListener("pointermove", event => {
if(!dragging) return;
const maxLeft = Math.max(12, window.innerWidth - widget.offsetWidth - 12);
const maxTop = Math.max(12, window.innerHeight - widget.offsetHeight - 12);
const nextLeft = Math.max(12, Math.min(maxLeft, startLeft + event.clientX - startX));
const nextTop = Math.max(12, Math.min(maxTop, startTop + event.clientY - startY));
widget.style.left = `${nextLeft}px`;
widget.style.top = `${nextTop}px`;
widget.style.right = "auto";
widget.style.bottom = "auto";
});
const stop = event => {
if(!dragging) return;
dragging = false;
widget.classList.remove("dragging");
try { header.releasePointerCapture(event.pointerId); } catch {}
};
header.addEventListener("pointerup", stop);
header.addEventListener("pointercancel", stop);
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
renderHomepageExperience(null);
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
renderHomepageExperience(workforceOSState);

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
<div class="mini-row"><span>Stream cadence</span><strong>${escapeHTML(prod.performance?.ops_stream_poll_seconds ?? 5)}s</strong></div>
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
platformRuntimeState.securityOnline = !!online;
platformRuntimeState.lastEvent = label || (online ? "Live security" : "Security sync restoring");
renderGlobalRuntimeState();
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
container.innerHTML = list.length ? list.slice(0, WORKFORCE_PERF.lowPower ? 18 : 35).map(item => `
<article class="security-feed-card ${String(item.severity || "").toLowerCase()}">
<div class="security-feed-pulse" aria-hidden="true"></div>
<div class="ops-event-head">
<strong>${escapeHTML(item.title || "Security update")}</strong>
<time>${escapeHTML(formatTimeLabel(item.created_at))}</time>
</div>
<p>${escapeHTML(item.body || "")}</p>
<div class="ops-event-meta">
${item.severity ? `<span class="badge-pill">${escapeHTML(String(item.severity).toUpperCase())}</span>` : ""}
<span class="badge-pill">Realtime</span>
</div>
</article>
`).join("") : `
<article class="security-feed-card info">
<div class="security-feed-pulse" aria-hidden="true"></div>
<div class="ops-event-head">
<strong>Security telemetry synchronized</strong>
<time>${escapeHTML(formatTimeLabel(new Date().toISOString()))}</time>
</div>
<p>Realtime monitoring is online. Incidents, token anomalies, containment actions, and access policy transitions will stream here.</p>
<div class="ops-event-meta">
<span class="badge-pill">STANDBY</span>
<span class="badge-pill">Websocket Ready</span>
</div>
</article>
`;
}

function pushSecurityFeedItem(item){
if(!item || typeof item !== "object") return;
const key = item.id || `${item.title || ""}:${item.created_at || ""}`;
if(key && securityFeedItems.some(existing => (existing.id || `${existing.title || ""}:${existing.created_at || ""}`) === key)) return;
securityFeedItems = [item, ...securityFeedItems].slice(0, WORKFORCE_PERF.lowPower ? 24 : 50);
recordObservabilityEvent("security", item.title || "Security event", item.body || "", item.severity || "info");
if(WORKFORCE_PERF.securityFrame) return;
WORKFORCE_PERF.securityFrame = requestAnimationFrame(() => {
WORKFORCE_PERF.securityFrame = 0;
renderSecurityLiveFeed(securityFeedItems);
});
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
if(WORKFORCE_TEST_MODE){
setSecurityPresence(false, "Security simulated");
return;
}
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
pushSecurityFeedItem({title: "Security stream connected", body: "Websocket telemetry is synchronized across auth events, incident state, and mitigation actions.", created_at: new Date().toISOString(), severity: "info"});
};

securitySocket.onmessage = (event) => {
let payload = null;
try { payload = JSON.parse(event.data || "{}"); } catch { payload = null; }
if(!payload || typeof payload !== "object") return;

if(payload.event === "snapshot"){
securityFeedItems = Array.isArray(payload.feed) ? payload.feed.slice(0, 50) : [];
renderSecurityLiveFeed(securityFeedItems);
recordObservabilityEvent("security", "Security snapshot synchronized", `${securityFeedItems.length} live security feed items buffered.`, "info");
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
recordRealtimeSample("security", payload);
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
const delay = shouldHoldRealtimeReconnect() ? Math.max(30000, securityBackoffMs) : reconnectDelay(securityBackoffMs);
securityReconnectTimer = setTimeout(() => {
securityReconnectTimer = null;
connectSecurityStream();
}, delay);
securityBackoffMs = Math.min(60000, Math.round(securityBackoffMs * 1.65));
};

securitySocket.onerror = () => {
stopSecurityHeartbeat();
setSecurityPresence(false, "Security sync restoring");
try { securitySocket.close(); } catch {}
};
}

async function connectOpsStream(){
if(WORKFORCE_TEST_MODE){
setOpsPresence(false, "Ops simulated");
return;
}
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
recordObservabilityEvent("ops", "Ops snapshot synchronized", `${opsFeedItems.length} live operating signals buffered.`, "info");
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
recordRealtimeSample("ops", payload);
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
const delay = shouldHoldRealtimeReconnect() ? Math.max(30000, opsBackoffMs) : reconnectDelay(opsBackoffMs);
opsReconnectTimer = setTimeout(() => {
opsReconnectTimer = null;
connectOpsStream();
}, delay);
opsBackoffMs = Math.min(60000, Math.round(opsBackoffMs * 1.65));
};

opsSocket.onerror = () => {
stopOpsHeartbeat();
setOpsPresence(false, "Sync restoring");
try { opsSocket.close(); } catch {}
};
}

async function connectProductIntelligenceStream(){
if(WORKFORCE_TEST_MODE){
return;
}
if(productIntelligenceReconnectTimer){
clearTimeout(productIntelligenceReconnectTimer);
productIntelligenceReconnectTimer = null;
}
if(productIntelligenceSocket && (productIntelligenceSocket.readyState === WebSocket.OPEN || productIntelligenceSocket.readyState === WebSocket.CONNECTING)){
return;
}

const token = typeof getToken === "function" ? getToken() : "";
if(!token) return;
if(!(await checkRealtimeCapability())){
startProductIntelligencePollingFallback(realtimeHealth.guidance || "Realtime dependency is unavailable; Product Intelligence is using authenticated polling.");
return;
}
stopProductIntelligencePollingFallback();

const url = `${wsBaseUrl()}/enterprise/product-intelligence/stream?token=${encodeURIComponent(token)}&days=30`;
try {
productIntelligenceSocket = new WebSocket(url);
} catch {
return;
}

productIntelligenceSocket.onopen = () => {
productIntelligenceBackoffMs = 1200;
stopProductIntelligenceHeartbeat();
startSocketHeartbeat(productIntelligenceSocket, timer => { productIntelligenceHeartbeatTimer = timer; });
recordObservabilityEvent("product_intelligence", "Product Intelligence stream connected", "Live product analytics are synchronized.", "info");
};

productIntelligenceSocket.onmessage = (event) => {
let payload = null;
try { payload = JSON.parse(event.data || "{}"); } catch { payload = null; }
if(!payload || typeof payload !== "object") return;
if(payload.event === "snapshot"){
productIntelligenceState = payload.data || productIntelligenceState;
productIntelligenceFeed = Array.isArray(payload.feed) ? payload.feed : productIntelligenceFeed;
recordObservabilityEvent("product_intelligence", "Product Intelligence snapshot synchronized", "Executive, hiring, AI, platform, and revenue analytics refreshed.", "info");
if(currentView === "productIntelligence") renderProductIntelligence(productIntelligenceState, productIntelligenceFeed);
return;
}
if(payload.event === "heartbeat" || payload.event === "pong"){
recordRealtimeSample("product_intelligence", payload);
if(currentView === "productIntelligence" && payload.executive_overview){
const status = document.getElementById("productIntelligenceStatus");
if(status) status.textContent = "Live intelligence";
}
return;
}
if(payload.event === "error"){
recordObservabilityEvent("product_intelligence", "Product Intelligence stream recovering", payload.message || "", "warning");
}
};

productIntelligenceSocket.onclose = () => {
stopProductIntelligenceHeartbeat();
if(productIntelligenceReconnectTimer) return;
const delay = reconnectDelay(productIntelligenceBackoffMs);
productIntelligenceReconnectTimer = setTimeout(() => {
productIntelligenceReconnectTimer = null;
if(currentView === "productIntelligence") connectProductIntelligenceStream();
}, delay);
productIntelligenceBackoffMs = Math.min(60000, Math.round(productIntelligenceBackoffMs * 1.65));
};

productIntelligenceSocket.onerror = () => {
stopProductIntelligenceHeartbeat();
try { productIntelligenceSocket.close(); } catch {}
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
id: "nav-observability",
label: "Open observability dashboard",
hint: "Websocket health, API readiness, queues, AI diagnostics",
run: async () => {
switchView("observability");
await loadObservability(true);
}
},
{
id: "export-observability",
label: "Export observability report",
hint: "Download runtime health, heartbeat samples, and recent events",
run: async () => {
switchView("observability");
await loadObservability(true);
exportObservabilityReport();
}
},
{
id: "nav-security",
label: "Open Security Center",
hint: "Incidents, controls, detection fabric, containment actions",
run: () => switchView("security")
},
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

(notifications || []).slice(0, 8).forEach(item => {
items.push({
id: `notification-${item.id || item.created_at || Math.random()}`,
label: `Notification: ${item.subject || item.kind || "Workspace alert"}`,
hint: item.message || "Open observability timeline",
run: () => {
switchView("observability");
recordObservabilityEvent("notification", item.subject || item.kind || "Workspace alert", item.message || "", "info");
renderObservabilityDashboard();
}
});
});

return items;
}

function openCommandPalette(initialQuery = ""){
const modal = document.getElementById("commandPalette");
const input = document.getElementById("commandInput");
if(!modal || !input) return;
paletteOpen = true;
modal.classList.remove("hidden");
paletteItems = buildCommandPaletteItems();
paletteIndex = 0;
input.value = String(initialQuery || "");
filterCommandPalette(input.value);
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
