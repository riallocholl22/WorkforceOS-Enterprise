function setupCommandPalette(){
const modal = document.getElementById("commandPalette");
const input = document.getElementById("commandInput");
if(!modal || !input || modal.dataset.ready) return;
modal.dataset.ready = "true";

input.addEventListener("input", () => filterCommandPalette(input.value));

modal.addEventListener("click", (event) => {
if(event.target === modal){
closeCommandPalette();
}
});
}

function scoreStatus(score){
if(score >= 75) return {label: "Top", className: "status-top"};
if(score >= 50) return {label: "Average", className: "status-average"};
return {label: "Low", className: "status-low"};
}

function scoreBar(score){
const safeScore = Math.max(0, Math.min(100, Number(score) || 0));
const status = scoreStatus(safeScore);

return `
<div class="score-wrap">
<div class="score-line">
<span style="width:${safeScore}%"></span>
</div>
<strong>${safeScore.toFixed(2)}%</strong>
<em class="status-pill ${status.className}">${status.label}</em>
</div>
`;
}

function isFresh(view){
return Date.now() - (lastLoaded[view] || 0) < CACHE_TTL;
}

function markLoaded(view){
lastLoaded[view] = Date.now();
}

function setBusy(button, isBusy, label = "Working..."){
if(!button) return;

if(isBusy){
button.dataset.originalText = button.innerText;
button.innerText = label;
button.disabled = true;
} else {
button.innerText = button.dataset.originalText || button.innerText;
button.disabled = false;
}
}

async function updateShortlistCount(){
const metric = document.getElementById("metricShortlisted");
if(!metric) return;
try {
const data = await apiShortlistAnalytics();
const totals = data?.totals || {};
metric.innerText = String(totals.shortlisted ?? totals.total ?? 0);
} catch {
metric.innerText = String(shortlist.size);
}
}

function _selectedJobId(){
const select = document.getElementById("shortlistJobSelect");
const raw = select?.value ?? "";
const parsed = raw ? Number(raw) : null;
if(parsed && Number.isFinite(parsed)) return parsed;
return jobs[0]?.id ?? null;
}

async function toggleShortlist(candidateId){
const jobId = _selectedJobId();
const match = matches.find(item => item.candidate_id === candidateId) || {};
const score = match.match_score ?? match.score ?? 0;
const reason = match.explanation || "Added to shortlist based on match score and skill overlap.";

try {
if(shortlist.has(candidateId)){
await apiRemoveFromShortlist(candidateId, jobId);
} else {
await apiAddToShortlist({
candidate_id: candidateId,
job_id: jobId,
shortlist_score: Math.round(score),
confidence: Math.round(match.tfidf_score ?? score),
ai_reason: reason,
recruiter_notes: ""
});
}
await refreshShortlist(true);
renderDashboardRanking(matches.slice(0, 5));
renderCandidateTable(candidates);
renderMatchResults(matches);
updateShortlistCount();
} catch (err) {
showAlert(err.message || "Unable to update shortlist");
}
}

function renderShortlistAnalytics(analytics){
const container = document.getElementById("shortlistAnalytics");
if(!container) return;
const totals = analytics?.totals || {total: 0, approved: 0, rejected: 0, shortlisted: 0};
container.innerHTML = `
<article class="metric-card"><span>Shortlisted</span><strong>${escapeHTML(totals.shortlisted ?? 0)}</strong></article>
<article class="metric-card"><span>Approved</span><strong>${escapeHTML(totals.approved ?? 0)}</strong></article>
<article class="metric-card"><span>Rejected</span><strong>${escapeHTML(totals.rejected ?? 0)}</strong></article>
<article class="metric-card"><span>Avg Score</span><strong>${escapeHTML(analytics?.average_match_score ?? 0)}%</strong></article>
`;
}

function renderShortlistEntries(entries){
const container = document.getElementById("shortlistEntries");
if(!container) return;
if(!entries.length){
container.innerHTML = renderOnboardingCard(
"Shortlist is ready for decisions.",
"Add candidates from Match Results, or let Auto-Shortlist build an operating queue once a job description is set.",
`<button onclick="switchView('matches')">Open Match Results</button>`
);
return;
}

container.innerHTML = entries.map(entry => {
const candidate = candidateById(entry.candidate_id);
const primary = candidatePrimaryLabel(candidate || {candidate_id: entry.candidate_id});
const secondary = candidateSecondaryLabel(candidate || {candidate_id: entry.candidate_id});
return `
<article class="match-card">
<div class="panel-header">
<div>
<h4>${escapeHTML(primary)}</h4>
${secondary ? `<div class="muted small">${escapeHTML(secondary)}</div>` : ""}
<p class="muted">${escapeHTML(entry.ai_reason || "")}</p>
</div>
<div class="row-actions">
<span class="status-pill ${entry.status === "approved" ? "status-top" : entry.status === "rejected" ? "status-low" : "status-average"}">${escapeHTML(entry.status || "shortlisted")}</span>
<span class="status-pill">${escapeHTML(entry.shortlist_score ?? 0)}%</span>
</div>
</div>
<div class="row-actions">
<button class="secondary-btn" onclick="approveShortlist('${escapeHTML(entry.candidate_id)}')">Approve</button>
<button class="secondary-btn" onclick="rejectShortlist('${escapeHTML(entry.candidate_id)}')">Reject</button>
<button class="danger-btn" onclick="removeFromShortlist('${escapeHTML(entry.candidate_id)}')">Remove</button>
</div>
${entry.recruiter_notes ? `<p class="muted">Notes: ${escapeHTML(entry.recruiter_notes)}</p>` : ""}
</article>
`;
}).join("");
}

function populateShortlistJobs(){
const select = document.getElementById("shortlistJobSelect");
if(!select) return;
const options = [`<option value=\"\">All / Latest</option>`].concat(
(jobs || []).map(job => `<option value=\"${escapeHTML(job.id)}\">${escapeHTML(job.title || `Job ${job.id}`)}</option>`)
);
select.innerHTML = options.join("");
if(shortlistJobId){
select.value = String(shortlistJobId);
}
}

async function refreshShortlist(quiet = false){
const status = document.getElementById("shortlistStatus");
const jobId = _selectedJobId();
shortlistJobId = jobId;

try {
if(!jobs.length) await loadJobs();
populateShortlistJobs();
if(status && !quiet) status.innerHTML = `<span class="status-pill status-average">Loading shortlist…</span>`;
const [listData, analytics] = await Promise.all([
apiGetShortlist(jobId),
apiShortlistAnalytics(jobId).catch(() => null)
]);
shortlistEntries = listData?.entries || [];
shortlist.clear();
shortlistEntries.forEach(item => shortlist.add(item.candidate_id));
renderShortlistEntries(shortlistEntries);
renderShortlistAnalytics(analytics);
if(status && !quiet) status.innerHTML = `<span class="status-pill status-top">Loaded ${shortlistEntries.length} entries</span>`;
} catch (err) {
if(status && !quiet) status.innerHTML = `<span class="status-pill status-low">${escapeHTML(err.message || "Unable to load shortlist")}</span>`;
}
}

async function runAutoShortlist(event){
const button = event?.target;
const status = document.getElementById("shortlistStatus");
const threshold = Number(document.getElementById("shortlistThreshold")?.value || 75);
rememberRecruiterPreference({threshold: Number.isFinite(threshold) ? threshold : 75});
const jobId = _selectedJobId();
const description = document.getElementById("matchJobDescription")?.value.trim() || jobs[0]?.description || "";

if(!description){
if(status) status.innerHTML = `<span class="status-pill status-low">Add a job description first.</span>`;
return;
}

setBusy(button, true, "Shortlisting...");
if(status) status.innerHTML = `<span class="status-pill status-average">Auto-shortlisting…</span>`;
try {
await apiAutoShortlist({job_id: jobId, job_description: description, threshold});
await refreshShortlist(true);
if(status) status.innerHTML = `<span class="status-pill status-top">Auto-shortlist complete</span>`;
updateShortlistCount();
scheduleOperationalSync("auto_shortlist_complete", 300);
} catch (err) {
if(status) status.innerHTML = `<span class="status-pill status-low">${escapeHTML(err.message || "Auto-shortlist failed")}</span>`;
} finally {
setBusy(button, false);
}
}

async function approveShortlist(candidateId){
try {
await apiApproveShortlist({candidate_id: candidateId, job_id: _selectedJobId()});
await refreshShortlist(true);
updateShortlistCount();
if(matches.length) renderMatchResults(matches);
scheduleOperationalSync("candidate_approved", 300);
showAlert("Decision recorded. WorkforceOS is refreshing pipeline intelligence.", "success");
} catch (err) {
showAlert(err.message || "Unable to approve candidate");
}
}

async function rejectShortlist(candidateId){
try {
await apiRejectShortlist({candidate_id: candidateId, job_id: _selectedJobId()});
await refreshShortlist(true);
updateShortlistCount();
if(matches.length) renderMatchResults(matches);
scheduleOperationalSync("candidate_rejected", 300);
showAlert("Decision recorded. Candidate intelligence and shortlist state are syncing.", "success");
} catch (err) {
showAlert(err.message || "Unable to reject candidate");
}
}

async function approveCandidateFromMatch(candidateId){
try {
await apiApproveShortlist({candidate_id: candidateId, job_id: _selectedJobId(), recruiter_notes: "Approved from match review."});
await refreshShortlist(true);
updateShortlistCount();
renderMatchResults(matches);
scheduleOperationalSync("candidate_approved_from_match", 300);
showAlert("Candidate approved for next step. WorkforceOS is recalculating priorities.", "success");
} catch (err) {
showAlert(err.message || "Unable to approve candidate");
}
}

async function rejectCandidateFromMatch(candidateId){
try {
await apiRejectShortlist({candidate_id: candidateId, job_id: _selectedJobId(), recruiter_notes: "Rejected from match review."});
await refreshShortlist(true);
updateShortlistCount();
renderMatchResults(matches);
scheduleOperationalSync("candidate_rejected_from_match", 300);
showAlert("Candidate moved to rejected. WorkforceOS is updating risk and conversion signals.", "success");
} catch (err) {
showAlert(err.message || "Unable to reject candidate");
}
}

async function removeFromShortlist(candidateId){
try {
await apiRemoveFromShortlist(candidateId, _selectedJobId());
await refreshShortlist(true);
updateShortlistCount();
scheduleOperationalSync("candidate_removed_from_shortlist", 300);
} catch (err) {
showAlert(err.message || "Unable to remove candidate");
}
}

function exportShortlistCsv(){
const rows = shortlistEntries || [];
if(!rows.length){
showAlert("No shortlisted candidates to export.", "info");
return;
}
const header = ["candidate_name", "candidate_id", "job_id", "status", "shortlist_score", "confidence", "ai_reason", "recruiter_notes"];
const lines = [header.join(",")].concat(
rows.map(item => header.map(key => {
let value = item[key] ?? "";
if(key === "candidate_name"){
const c = candidateById(item.candidate_id);
value = candidateDisplayName(c) || "Unnamed Candidate";
}
const text = String(value).replace(/\"/g, "\"\"");
return `"${text}"`;
}).join(","))
);
const blob = new Blob([lines.join("\n")], {type: "text/csv;charset=utf-8"});
const url = URL.createObjectURL(blob);
const a = document.createElement("a");
a.href = url;
a.download = `shortlist_${Date.now()}.csv`;
document.body.appendChild(a);
a.click();
a.remove();
URL.revokeObjectURL(url);
}

function renderFeedbackPanel(){
const skillsContainer = document.getElementById("feedbackSkills");
const scoreContainer = document.getElementById("feedbackScore");
const missingContainer = document.getElementById("feedbackMissing");
const strengthsContainer = document.getElementById("feedbackStrengths");
const suggestionsContainer = document.getElementById("feedbackSuggestions");
const rolesContainer = document.getElementById("feedbackRoles");
const modeContainer = document.getElementById("feedbackMode");

if(!skillsContainer || !missingContainer || !strengthsContainer || !suggestionsContainer){
return;
}

if(feedbackState === "loading"){
skillsContainer.innerHTML = `<span class="muted">Analyzing resume...</span>`;
if(scoreContainer) scoreContainer.innerHTML = `<span class="status-pill status-average">Calculating...</span>`;
missingContainer.innerHTML = `<span class="muted">Checking job gaps...</span>`;
strengthsContainer.innerHTML = `<li class="muted">Preparing strengths...</li>`;
suggestionsContainer.innerHTML = `<li class="muted">Generating suggestions...</li>`;
if(rolesContainer) rolesContainer.innerHTML = `<span class="muted">Preparing roles...</span>`;
if(modeContainer) modeContainer.innerHTML = `<span class="status-pill status-average">Loading</span>`;
return;
}

if(feedbackState === "error"){
skillsContainer.innerHTML = `<span class="muted">Feedback unavailable.</span>`;
if(scoreContainer) scoreContainer.innerHTML = `<span class="status-pill status-low">Unavailable</span>`;
missingContainer.innerHTML = `<span class="status-pill status-low">Error</span>`;
strengthsContainer.innerHTML = `<li class="muted">${escapeHTML(feedbackError || "Unable to load feedback.")}</li>`;
suggestionsContainer.innerHTML = `<li class="muted">Try refreshing feedback after upload or matching.</li>`;
if(rolesContainer) rolesContainer.innerHTML = `<span class="muted">Add role context to generate calibrated role suggestions.</span>`;
if(modeContainer) modeContainer.innerHTML = `<span class="status-pill status-low">Error</span>`;
return;
}

if(!feedbackData){
skillsContainer.innerHTML = `<span class="muted">Upload a resume or run a match to see AI feedback.</span>`;
if(scoreContainer) scoreContainer.innerHTML = `<span class="status-pill">Empty</span>`;
missingContainer.innerHTML = `<span class="muted">Upload a resume or run a match to see AI feedback.</span>`;
strengthsContainer.innerHTML = `<li class="muted">Upload a resume or run a match to see AI feedback.</li>`;
suggestionsContainer.innerHTML = `<li class="muted">Upload a resume or run a match to see AI feedback.</li>`;
if(rolesContainer) rolesContainer.innerHTML = `<span class="muted">Role suggestions appear after resume and job context are available.</span>`;
if(modeContainer) modeContainer.innerHTML = `<span class="status-pill">Empty</span>`;
return;
}

const skills = feedbackData.skills || [];
const score = feedbackData.score;
const missing = feedbackData.missing_skills || [];
const strengths = feedbackData.strengths || [];
const suggestions = feedbackData.suggestions || [];
const recommendedRoles = feedbackData.recommended_roles || [];
const mode = feedbackData.mode || "fallback";

skillsContainer.innerHTML = skills.length
? skills.map(skill => `<span>${escapeHTML(skill)}</span>`).join("")
: `<span class="muted">Upload a text-readable resume to extract skill evidence.</span>`;
if(scoreContainer){
scoreContainer.innerHTML = `<span class="status-pill status-top">${escapeHTML(score != null ? `${score}%` : "Awaiting score")}</span>`;
}

missingContainer.innerHTML = missing.length
? missing.map(skill => `<span class="missing-skill-tag">${escapeHTML(skill)}</span>`).join("")
: `<span class="muted">No missing job skills detected.</span>`;

strengthsContainer.innerHTML = strengths.length
? strengths.map(skill => `<li>${escapeHTML(skill)}</li>`).join("")
: `<li class="muted">Strengths appear after resume evidence is parsed.</li>`;

suggestionsContainer.innerHTML = suggestions.length
? suggestions.map(suggestion => `<li>${escapeHTML(suggestion)}</li>`).join("")
: `<li class="muted">Suggestions appear once resume and role context are available.</li>`;

if(rolesContainer){
rolesContainer.innerHTML = recommendedRoles.length
? recommendedRoles.map(role => `<span>${escapeHTML(role)}</span>`).join("")
: `<span class="muted">Recommended roles appear after matching context is available.</span>`;
}

if(modeContainer){
const modeClass = mode === "openai" ? "status-top" : "status-average";
modeContainer.innerHTML = `<span class="status-pill ${modeClass}">${escapeHTML(mode)}</span>`;
}
}

async function fetchAIFeedback(jobDescription = ""){
const status = document.getElementById("matchStatus");
if(!lastResumeText){
feedbackData = null;
feedbackState = "empty";
feedbackError = "";
renderFeedbackPanel();
return;
}

feedbackState = "loading";
feedbackError = "";
renderFeedbackPanel();
if(status) status.innerHTML = `<span class="status-pill status-average">Generating feedback…</span>`;

try {
const data = await apiAIFeedback({
resume_text: lastResumeText,
job_description: jobDescription || "",
});
feedbackData = data.feedback || null;
feedbackState = feedbackData ? "ready" : "empty";
renderFeedbackPanel();
if(status) status.innerHTML = `<span class="status-pill status-top">AI feedback generated</span>`;
} catch (err) {
if(status) status.innerHTML = `<span class="status-pill status-low">${escapeHTML(err.message || "Unable to get AI feedback")}</span>`;
feedbackData = null;
feedbackState = "error";
feedbackError = err.message || "Unable to get AI feedback";
renderFeedbackPanel();
}
}

function refreshAIFeedback(){
const description = document.getElementById("matchJobDescription")?.value.trim() || "";
fetchAIFeedback(description);
}

function appendChatMessage(text, role = "user"){
const container = document.getElementById("chatMessages");
if(!container) return null;

const className = role === "assistant" ? "chat-bubble assistant" : "chat-bubble user";
const content = role === "assistant" ? formatAssistantMarkdown(text) : escapeHTML(text);
const timestamp = new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
container.insertAdjacentHTML("beforeend", `
<div class="${className}" aria-live="${role === "assistant" ? "polite" : "off"}">
<div class="chat-content">${content}</div>
<time>${timestamp}</time>
</div>
`);
container.scrollTop = container.scrollHeight;
return container.lastElementChild;
}

function formatAssistantMarkdown(text){
const blocks = [];
const withTokens = String(text || "").replace(/```(\w+)?\n?([\s\S]*?)```/g, (_, language, code) => {
const token = `@@CODE_BLOCK_${blocks.length}@@`;
const lang = language ? `<span>${escapeHTML(language)}</span>` : "";
blocks.push(`<pre>${lang}<code>${highlightCode(code.trim())}</code></pre>`);
return token;
});

let html = escapeHTML(withTokens)
.replace(/`([^`]+)`/g, "<code>$1</code>")
.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
.replace(/^### (.*)$/gm, "<h4>$1</h4>")
.replace(/^## (.*)$/gm, "<h4>$1</h4>")
.replace(/^\- (.*)$/gm, "<div class=\"chat-list-item\">$1</div>")
.replace(/^(\d+)\. (.*)$/gm, "<div class=\"chat-list-item numbered\"><span>$1</span>$2</div>")
.replace(/\n/g, "<br>");

blocks.forEach((block, index) => {
html = html.replace(`@@CODE_BLOCK_${index}@@`, block);
});

return html;
}

function highlightCode(code){
return escapeHTML(code)
.replace(/\b(const|let|var|function|return|async|await|if|else|try|catch|for|while|class|def|import|from)\b/g, "<span class=\"code-keyword\">$1</span>")
.replace(/(&quot;.*?&quot;|'.*?')/g, "<span class=\"code-string\">$1</span>")
.replace(/\b(true|false|null|None)\b/g, "<span class=\"code-literal\">$1</span>");
}

function setAssistantTyping(bubble, label = "Reading the context"){
if(!bubble) return;
bubble.classList.add("thinking");
bubble.querySelector(".chat-content").innerHTML = `
<div class="typing-indicator" aria-label="${escapeHTML(label)}">
<span></span><span></span><span></span>
</div>
`;
}

function renderAssistantText(bubble, text){
if(!bubble) return;
bubble.classList.remove("thinking");
bubble.querySelector(".chat-content").innerHTML = formatAssistantMarkdown(text);
bubble.parentElement.scrollTo({top: bubble.parentElement.scrollHeight, behavior: "smooth"});
}

function queueAssistantToken(bubble, token){
if(!bubble) return;
let state = chatRenderState.get(bubble);
if(!state){
state = {visible: "", pending: "", timer: null};
chatRenderState.set(bubble, state);
}
state.pending += token || "";
if(state.timer) return;

const flush = () => {
const nextSize = Math.min(state.pending.length, Math.max(1, Math.round(Math.random() * 5)));
state.visible += state.pending.slice(0, nextSize);
state.pending = state.pending.slice(nextSize);
renderAssistantText(bubble, state.visible);
if(state.pending){
state.timer = setTimeout(flush, 14 + Math.random() * 34);
} else {
state.timer = null;
}
};
state.timer = setTimeout(flush, 80);
}

function completeAssistantStream(bubble, finalText){
const state = chatRenderState.get(bubble);
if(state?.timer) clearTimeout(state.timer);
chatRenderState.delete(bubble);
renderAssistantText(bubble, finalText || state?.visible || "");
}

function toggleChat(){
chatOpen = !chatOpen;
const widget = document.getElementById("chatWidget");
if(!widget) return;

widget.classList.toggle("hidden", !chatOpen);
}

function useChatShortcut(prompt){
const input = document.getElementById("chatInput");
if(!input) return;
if(!chatOpen) toggleChat();
input.value = prompt;
input.focus();
}

function chatPayload(message){
const topMatch = matches[0] || null;
const activeCandidateId =
document.getElementById("brainCandidateId")?.value.trim()
|| topMatch?.candidate_id
|| "";
const activeJobId =
document.getElementById("brainJobId")?.value.trim()
|| String(shortlistJobId || "")
|| "";
return {
message,
candidates: matches.slice(0, 8),
context: {
session_id: chatSessionId,
view: currentView,
candidate_count: candidates.length,
job_count: jobs.length,
match_count: matches.length,
top_candidate_id: topMatch?.candidate_id || "",
top_candidate_score: topMatch?.match_score ?? topMatch?.score ?? "",
active_job: document.getElementById("matchJobDescription")?.value.trim().slice(0, 260) || jobs[0]?.title || "",
active_candidate_id: activeCandidateId,
active_job_id: activeJobId,
shortlisted_candidates: [...shortlist].slice(0, 12),
shortlisted_count: shortlist.size,
workspace: workspace?.organization?.name || "Workspace",
viewed_candidate_ids: _loadSessionList("viewedCandidateIds", 12),
recruiter_preferences: (() => {
try { return JSON.parse(sessionStorage.getItem("recruiterPrefs") || "{}") || {}; } catch { return {}; }
})()
}
};
}

function websocketUrl(path){
return `${wsBaseUrl()}${path}`;
}

function streamChatMessage(payload, bubble, status){
return new Promise((resolve, reject) => {
const token = encodeURIComponent(getToken() || "");
const socket = new WebSocket(`${websocketUrl("/ai/chat/stream")}?token=${token}`);
let response = "";
let settled = false;
const timeout = setTimeout(() => {
if(!settled){
settled = true;
socket.close();
reject(new Error("Assistant stream timed out"));
}
}, 45000);

socket.onopen = () => {
socket.send(JSON.stringify(payload));
if(status) status.innerHTML = `<span class="status-pill status-average">Assistant is typing...</span>`;
};

socket.onmessage = event => {
let data = null;
try { data = JSON.parse(event.data || "{}"); } catch { data = null; }
if(!data || typeof data !== "object") return;
if(data.event === "token"){
response += data.token || "";
queueAssistantToken(bubble, data.token || "");
}
if(data.event === "error"){
settled = true;
clearTimeout(timeout);
socket.close();
reject(new Error(data.message || "Assistant stream failed"));
}
if(data.event === "done"){
settled = true;
clearTimeout(timeout);
socket.close();
completeAssistantStream(bubble, response);
resolve(response);
}
};

socket.onerror = () => {
if(!settled){
settled = true;
clearTimeout(timeout);
reject(new Error("Assistant stream unavailable"));
}
};

socket.onclose = () => {
clearTimeout(timeout);
};
});
}

async function fallbackChatMessage(payload, bubble){
const data = await apiChatAssistant(payload);
const response = data.response || data.reply || "I could not generate a response just now.";
completeAssistantStream(bubble, response);
return response;
}

async function sendChatMessage(){
const input = document.getElementById("chatInput");
const status = document.getElementById("chatStatus");
const button = document.getElementById("chatSendBtn");
const message = input?.value.trim();

if(!message){
if(status) status.textContent = "Please type a question for the assistant.";
return;
}

appendChatMessage(message, "user");
input.value = "";
if(status) status.innerHTML = `<span class="status-pill status-average">Reading context...</span>`;
const thinkingBubble = appendChatMessage("", "assistant");
setAssistantTyping(thinkingBubble, "Reading the context");
setBusy(button, true, "Thinking...");

try {
const payload = chatPayload(message);
try {
await streamChatMessage(payload, thinkingBubble, status);
} catch {
await fallbackChatMessage(payload, thinkingBubble);
}
if(status) status.innerHTML = `<span class="status-pill status-top">Response received</span>`;
} catch (err) {
if(thinkingBubble){
thinkingBubble.classList.remove("thinking");
thinkingBubble.querySelector(".chat-content").innerHTML = `
${formatAssistantMarkdown("The assistant stream needs a moment to reconnect. Your message is preserved, and you can retry once the workspace signal settles.")}
<button class="secondary-btn chat-retry" onclick="retryLastChatMessage(this)">Retry</button>
`;
thinkingBubble.dataset.retryMessage = message;
}
if(status) status.innerHTML = `<span class="status-pill status-low">${escapeHTML(err.message || "Chat failed")}</span>`;
} finally {
setBusy(button, false);
input?.focus();
}
}

function retryLastChatMessage(button){
const bubble = button.closest(".chat-bubble");
const message = bubble?.dataset.retryMessage;
if(!message) return;
const input = document.getElementById("chatInput");
if(input) input.value = message;
bubble.remove();
sendChatMessage();
}
