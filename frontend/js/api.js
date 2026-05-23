window.API_BASE = (window.API_BASE || "http://127.0.0.1:8000").replace(/\/$/, "");

const ENTERPRISE_PREFIX = "/enterprise";

const API_ROUTES = {
    WORKSPACE: `${ENTERPRISE_PREFIX}/workspace`,
    NOTIFICATIONS: `${ENTERPRISE_PREFIX}/notifications`,
    ANALYTICS: `${ENTERPRISE_PREFIX}/analytics/overview`,
    TEAM: `${ENTERPRISE_PREFIX}/team`,
    TEAM_INVITE: `${ENTERPRISE_PREFIX}/team/invite`,
    BILLING: `${ENTERPRISE_PREFIX}/billing`,
    BILLING_PLAN: `${ENTERPRISE_PREFIX}/billing/plan`,
    BILLING_CHECKOUT: `${ENTERPRISE_PREFIX}/billing/checkout`,
    BILLING_MPESA: `${ENTERPRISE_PREFIX}/billing/mpesa/stk-push`,
    BILLING_VERIFY: `${ENTERPRISE_PREFIX}/billing/verify`,
    BILLING_CANCEL: `${ENTERPRISE_PREFIX}/billing/cancel`,
    BILLING_INVOICE: `${ENTERPRISE_PREFIX}/billing/invoice`,
    SECURITY_OVERVIEW: `${ENTERPRISE_PREFIX}/security/overview`,
    SECURITY_ANALYZE: `${ENTERPRISE_PREFIX}/security/analyze`,
    SECURITY_INCIDENTS: `${ENTERPRISE_PREFIX}/security/incidents`,
    SECURITY_CONTROLS: `${ENTERPRISE_PREFIX}/security/controls`,
    SECURITY_AUTOMATION_RULES: `${ENTERPRISE_PREFIX}/security/automation/rules`,
    SCHEDULING: `${ENTERPRISE_PREFIX}/scheduling`,
    DECISION_INTELLIGENCE: `${ENTERPRISE_PREFIX}/decision/intelligence`,
    WORKFLOW_EVALUATE: `${ENTERPRISE_PREFIX}/workflows/evaluate`,
    SKILL_DEMAND: `${ENTERPRISE_PREFIX}/skills/demand`,

    SHORTLIST: "/shortlist",
    SHORTLIST_AUTO: "/shortlist/auto",
    SHORTLIST_ANALYTICS: "/shortlist/analytics",
    SHORTLIST_APPROVE: `${ENTERPRISE_PREFIX}/shortlist/approve`,
    SHORTLIST_REJECT: `${ENTERPRISE_PREFIX}/shortlist/reject`
};

const REQUEST_TIMEOUT = 30000;


// ======================================================
// AUTH STORAGE
// ======================================================

function getToken() {
    return localStorage.getItem("token") || sessionStorage.getItem("token");
}

function getRefreshToken() {
    return localStorage.getItem("refreshToken") || sessionStorage.getItem("refreshToken");
}

function storeAuthTokens(payload = {}, remember = true) {
    const primary = remember ? localStorage : sessionStorage;
    const secondary = remember ? sessionStorage : localStorage;

    if (payload.access_token) {
        primary.setItem("token", payload.access_token);
        secondary.removeItem("token");
    }

    if (payload.refresh_token) {
        primary.setItem("refreshToken", payload.refresh_token);
        secondary.removeItem("refreshToken");
    }

    if (payload.user) {
        primary.setItem("currentUser", JSON.stringify(payload.user));
        secondary.removeItem("currentUser");
    }
}

function clearAuth() {
    localStorage.removeItem("token");
    localStorage.removeItem("refreshToken");
    localStorage.removeItem("currentUser");
    sessionStorage.removeItem("token");
    sessionStorage.removeItem("refreshToken");
    sessionStorage.removeItem("currentUser");
}


// ======================================================
// REDIRECTS
// ======================================================

function redirectToLogin(
    message = "Your session expired. Please log in again."
) {
    clearAuth();

    sessionStorage.setItem("authMessage", message);

    if (!window.location.pathname.includes("login.html")) {
        window.location.href = window.location.pathname.includes("/pages/")
            ? "../login.html"
            : "login.html";
    }
}


// ======================================================
// HEADERS
// ======================================================

function buildHeaders(extra = {}) {

    const headers = {
        ...extra
    };

    const token = getToken();

    if (token) {
        headers.Authorization = `Bearer ${token}`;
    }

    const csrfToken = document.cookie
        .split("; ")
        .find((row) => row.startsWith("csrf_token="))
        ?.split("=")[1];

    if (csrfToken) {
        headers["X-CSRF-Token"] = decodeURIComponent(csrfToken);
    }

    return headers;
}

function buildJsonHeaders(extra = {}) {

    return buildHeaders({
        "Content-Type": "application/json",
        ...extra
    });
}


// ======================================================
// ERROR PARSING
// ======================================================

async function parseError(res) {

    try {

        const body = await parseJsonSafely(res);
        if (!body) {
            throw new Error("API returned an empty or invalid JSON response");
        }

        if (body?.error?.message) {
            let message = body.error.message;
            const meta = body.error.meta || null;
            if (meta?.guidance) {
                message = `${message} ${meta.guidance}`;
            }
            if (meta?.extraction?.is_scanned_pdf) {
                message = "The uploaded resume appears scanned or image-based. Upload a DOCX/TXT version, or export a text-based PDF. OCR can be enabled server-side.";
            }
            return message;
        }

        if (body?.detail) {
            return normalizeApiError(body.detail);
        }

        return normalizeApiError(body?.message || "API request failed");

    } catch {
        return "API request failed";
    }
}

async function parseJsonSafely(res) {
    const contentType = res.headers.get("content-type") || "";

    if (!contentType.includes("application/json")) {
        const text = await res.text().catch(() => "");
        return text ? { message: text } : null;
    }

    try {
        return await res.json();
    } catch {
        return null;
    }
}

function normalizeApiError(detail) {
    if (Array.isArray(detail)) {
        const messages = detail.map((item) => {
            const path = Array.isArray(item.loc)
                ? item.loc.filter((part) => part !== "body").join(".")
                : "";
            const label = path ? path.replaceAll("_", " ") : "Request";
            return `${label}: ${item.msg || "invalid value"}`;
        });
        return messages.slice(0, 3).join(" · ") || "Please review the highlighted fields.";
    }

    const message = typeof detail === "string" ? detail : JSON.stringify(detail || {});
    const lower = message.toLowerCase();
    if (lower.includes("validation failed") || lower.includes("field required")) {
        return "Please check the required fields and try again.";
    }
    if (lower.includes("invalid credentials")) {
        return "Email or password is incorrect.";
    }
    if (lower.includes("missing bearer token") || lower.includes("not authenticated")) {
        return "Your session has expired. Please log in again.";
    }
    if (lower.includes("not assigned to an organization")) {
        return "Workspace initialization is still in progress for your account. Refresh in a moment, or ask a company admin to add you to a workspace.";
    }
    if (lower.includes("insufficient permissions")) {
        return "This action is restricted in your workspace. If this is unexpected, ask a company admin to confirm your role, then refresh your session.";
    }
    if (lower.includes("confirmation required") || lower.includes("confirmation_required")) {
        return "This response action needs explicit confirmation because it may impact user access. Review the incident response plan, then confirm and re-run the action.";
    }
    return message || "API request failed";
}


// ======================================================
// SESSION REFRESH
// ======================================================

async function refreshSession() {

    const refreshToken = getRefreshToken();

    try {

        const res = await fetch(
            `${window.API_BASE}/auth/refresh`,
            {
                method: "POST",
                credentials: "include",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    refresh_token: refreshToken || "cookie-refresh-placeholder"
                })
            }
        );

        if (!res.ok) {
            clearAuth();
            return false;
        }

        const body = await parseJsonSafely(res);

        if (body?.success && body.data) {
            storeAuthTokens(body.data);
            return true;
        }

        clearAuth();
        return false;

    } catch {

        clearAuth();
        return false;
    }
}


// ======================================================
// CORE REQUEST ENGINE
// ======================================================

async function request(
    endpoint,
    method = "GET",
    data = null,
    retrying = false,
    isFormData = false
) {

    if (
        !getToken() &&
        !endpoint.startsWith("/auth/")
    ) {

        redirectToLogin(
            "Please log in to continue."
        );

        throw new Error(
            "Please log in to continue."
        );
    }

    const controller = new AbortController();

    const timeout = setTimeout(() => {
        controller.abort();
    }, REQUEST_TIMEOUT);

    try {

        const options = {
            method,
            signal: controller.signal,
            credentials: "include",
            headers: isFormData
                ? buildHeaders()
                : buildJsonHeaders()
        };

        if (data) {

            options.body = isFormData
                ? data
                : JSON.stringify(data);
        }

        let res;
        try {
            res = await fetch(
                `${window.API_BASE}${endpoint}`,
                options
            );
        } catch (err) {
            if (!retrying && (err instanceof TypeError || err.name === "AbortError")) {
                await new Promise((resolve) => setTimeout(resolve, 350));
                return request(endpoint, method, data, true, isFormData);
            }
            throw err;
        }

        clearTimeout(timeout);

        if (
            res.status === 401 &&
            !retrying &&
            !endpoint.startsWith("/auth/")
        ) {

            const refreshed = await refreshSession();

            if (refreshed) {

                return request(
                    endpoint,
                    method,
                    data,
                    true,
                    isFormData
                );
            }
        }

        if (res.status === 401 && !endpoint.startsWith("/auth/")) {

            redirectToLogin();

            throw new Error(
                "Authentication required"
            );
        }

        if (!res.ok) {

            throw new Error(
                await parseError(res)
            );
        }

        if (res.status === 204) {
            return null;
        }

        const contentType =
            res.headers.get("content-type") || "";

        if (
            contentType.includes("application/pdf")
        ) {
            return res;
        }

        const body = await res.json();

        if (
            body &&
            typeof body === "object" &&
            "success" in body
        ) {

            if (!body.success) {

                throw new Error(
                    body.error?.message ||
                    "Request failed"
                );
            }

            return body.data;
        }

        return body || {};

    } catch (err) {

        if (err.name === "AbortError") {

            throw new Error(
                "Request timeout exceeded"
            );
        }

        if (err instanceof TypeError && /fetch|network|failed/i.test(err.message || "")) {
            throw new Error(
                `Unable to reach API at ${window.API_BASE}. Start the FastAPI server on port 8000 and verify CORS is configured.`
            );
        }

        console.warn(
            "[API ERROR]",
            endpoint,
            err
        );

        throw err;

    } finally {

        clearTimeout(timeout);
    }
}


// ======================================================
// AUTH
// ======================================================

async function apiGetMe() {
    return request("/auth/me");
}

async function apiLogout() {

    const refreshToken = getRefreshToken();

    try {

        return await request(
            "/auth/logout",
            "POST",
            refreshToken
                ? { refresh_token: refreshToken }
                : null
        );

    } finally {

        clearAuth();
    }
}

async function apiLogin(email, password) {
    return request("/auth/login", "POST", { email, password });
}

async function apiRegister(email, password, organizationName = "") {
    return request(
        "/auth/register",
        "POST",
        {
            email,
            password,
            organization_name: organizationName || null
        }
    );
}

async function apiForgotPassword(email) {
    return request("/auth/forgot-password", "POST", { email });
}

async function apiSendVerification(email) {
    return request("/auth/send-verification", "POST", { email });
}

async function apiVerifyEmail(token) {
    return request("/auth/verify-email", "POST", { token });
}

async function apiResetPassword(token, newPassword) {
    return request(
        "/auth/reset-password",
        "POST",
        {
            token,
            new_password: newPassword
        }
    );
}

async function apiMicrosoftUrl() {
    return request("/auth/microsoft/url");
}

async function apiMicrosoftCallback(email, displayName) {
    return request(
        "/auth/microsoft/callback",
        "POST",
        {
            email,
            display_name: displayName
        }
    );
}

async function apiGoogleUrl() {
    return request("/auth/google/url");
}

async function apiGoogleCallback(email, displayName) {
    return request("/auth/google/callback", "POST", { email, display_name: displayName });
}

async function apiGithubUrl() {
    return request("/auth/github/url");
}

async function apiGithubCallback(email, displayName) {
    return request("/auth/github/callback", "POST", { email, display_name: displayName });
}


// ======================================================
// DASHBOARD
// ======================================================

async function apiGetDashboardSummary() {
    return request("/dashboard/summary");
}

async function apiGetWorkspace() {
    return request(API_ROUTES.WORKSPACE);
}

async function apiGetNotifications() {
    return request(API_ROUTES.NOTIFICATIONS);
}

async function apiGetAnalyticsOverview() {
    return request(API_ROUTES.ANALYTICS);
}


// ======================================================
// TEAM
// ======================================================

async function apiGetTeam() {
    return request(API_ROUTES.TEAM);
}

async function apiInviteTeamMember(email, role) {

    return request(
        API_ROUTES.TEAM_INVITE,
        "POST",
        {
            email,
            role
        }
    );
}


// ======================================================
// BILLING
// ======================================================

async function apiGetBilling() {
    return request(API_ROUTES.BILLING);
}

async function apiChangeBillingPlan(plan, billingCycle = "monthly", currency = "KES") {

    return request(
        API_ROUTES.BILLING_PLAN,
        "POST",
        { plan, billing_cycle: billingCycle, currency }
    );
}

async function apiGenerateInvoice() {

    return request(
        API_ROUTES.BILLING_INVOICE,
        "POST",
        {}
    );
}

async function apiCreateCheckout(provider, plan, billingCycle = "monthly", currency = "KES") {
    return request(
        API_ROUTES.BILLING_CHECKOUT,
        "POST",
        {
            provider,
            plan,
            billing_cycle: billingCycle,
            currency,
            idempotency_key: `checkout-${provider}-${plan}-${Date.now()}`
        }
    );
}

async function apiInitiateMpesa(phone, plan, billingCycle = "monthly") {
    return request(
        API_ROUTES.BILLING_MPESA,
        "POST",
        {
            phone,
            plan,
            billing_cycle: billingCycle,
            currency: "KES",
            idempotency_key: `mpesa-${plan}-${phone}-${Date.now()}`
        }
    );
}

async function apiVerifyPayment(reference) {
    return request(API_ROUTES.BILLING_VERIFY, "POST", { reference });
}

async function apiCancelSubscription() {
    return request(API_ROUTES.BILLING_CANCEL, "POST", {});
}


// ======================================================
// SECURITY
// ======================================================

async function apiGetSecurityOverview() {

    return request(
        API_ROUTES.SECURITY_OVERVIEW
    );
}

async function apiAnalyzeSecurityEvent(data) {

    return request(
        API_ROUTES.SECURITY_ANALYZE,
        "POST",
        data
    );
}

async function apiGetSecurityIncidents(status = "") {
    const query = status ? `?status=${encodeURIComponent(status)}` : "";
    const data = await request(`${API_ROUTES.SECURITY_INCIDENTS}${query}`);
    return data?.incidents || [];
}

async function apiGetSecurityIncident(incidentId) {
    return request(`${API_ROUTES.SECURITY_INCIDENTS}/${encodeURIComponent(String(incidentId))}`);
}

async function apiGetSecurityIncidentActions(incidentId) {
    const data = await request(`${API_ROUTES.SECURITY_INCIDENTS}/${encodeURIComponent(String(incidentId))}/actions`);
    return data?.actions || [];
}

async function apiExecuteSecurityIncidentAction(incidentId, payload) {
    return request(
        `${API_ROUTES.SECURITY_INCIDENTS}/${encodeURIComponent(String(incidentId))}/actions`,
        "POST",
        payload || {}
    );
}

async function apiContainSecurityIncident(incidentId, note = "") {
    const body = note ? { note } : null;
    return request(`${API_ROUTES.SECURITY_INCIDENTS}/${encodeURIComponent(String(incidentId))}/contain`, "POST", body);
}

async function apiResolveSecurityIncident(incidentId, note = "") {
    const body = note ? { note } : null;
    return request(`${API_ROUTES.SECURITY_INCIDENTS}/${encodeURIComponent(String(incidentId))}/resolve`, "POST", body);
}

async function apiAddSecurityIncidentNote(incidentId, note) {
    return request(`${API_ROUTES.SECURITY_INCIDENTS}/${encodeURIComponent(String(incidentId))}/notes`, "POST", { note });
}

async function apiGetSecurityControls() {
    return request(API_ROUTES.SECURITY_CONTROLS);
}

async function apiGetSecurityAutomationRules() {
    const data = await request(API_ROUTES.SECURITY_AUTOMATION_RULES);
    return data?.rules || [];
}

async function apiCreateSecurityAutomationRule(rule) {
    return request(API_ROUTES.SECURITY_AUTOMATION_RULES, "POST", rule || {});
}

async function apiPatchSecurityAutomationRule(ruleId, patch) {
    return request(`${API_ROUTES.SECURITY_AUTOMATION_RULES}/${encodeURIComponent(String(ruleId))}`, "PATCH", patch || {});
}

async function apiDeleteSecurityAutomationRule(ruleId) {
    return request(`${API_ROUTES.SECURITY_AUTOMATION_RULES}/${encodeURIComponent(String(ruleId))}`, "DELETE");
}


// ======================================================
// SCHEDULING
// ======================================================

async function apiGetSchedules() {

    return request(
        API_ROUTES.SCHEDULING
    );
}

async function apiCreateSchedule(data) {

    return request(
        API_ROUTES.SCHEDULING,
        "POST",
        data
    );
}


// ======================================================
// AI + MATCHING
// ======================================================

async function apiGetRanking(
    jobDescription = ""
) {

    const query =
        jobDescription
            ? `?job_description=${encodeURIComponent(jobDescription)}`
            : "";

    const data = await request(
        `/rank-candidates${query}`
    );

    return data.candidates || [];
}

async function apiMatchCandidates(
    jobDescription = ""
) {

    const data = await request(
        "/match",
        "POST",
        {
            job_description:
                jobDescription || null
        }
    );

    return data.candidates || [];
}

async function apiDecisionIntelligence(data) {

    return request(
        API_ROUTES.DECISION_INTELLIGENCE,
        "POST",
        data
    );
}

async function apiEvaluateWorkflow(data) {

    return request(
        API_ROUTES.WORKFLOW_EVALUATE,
        "POST",
        data
    );
}

async function apiCandidateIntelligence(candidateId) {
    return request(
        `${ENTERPRISE_PREFIX}/candidates/${encodeURIComponent(candidateId)}/intelligence`
    );
}

async function apiCandidate360(data) {
    return request(
        `${ENTERPRISE_PREFIX}/brain/candidates/360`,
        "POST",
        data || {}
    );
}

async function apiExecutiveBrief(days = 14) {
    const safeDays = Math.max(1, Math.min(90, Number(days) || 14));
    return request(
        `${ENTERPRISE_PREFIX}/brain/executive-brief?days=${encodeURIComponent(String(safeDays))}`
    );
}

async function apiWorkforceOS(days = 14) {
    const safeDays = Math.max(1, Math.min(90, Number(days) || 14));
    return request(
        `${ENTERPRISE_PREFIX}/brain/workforce-os?days=${encodeURIComponent(String(safeDays))}`
    );
}

async function apiSeedDemoEnvironment() {
    return request(`${ENTERPRISE_PREFIX}/brain/demo-environment`, "POST", {});
}

async function apiGetAutomationRules() {
    return request(`${ENTERPRISE_PREFIX}/brain/automation/rules`);
}

async function apiCreateAutomationRule(payload) {
    return request(`${ENTERPRISE_PREFIX}/brain/automation/rules`, "POST", payload || {});
}

async function apiSkillDemand() {
    return request(API_ROUTES.SKILL_DEMAND);
}

async function apiCreateAssessment(data) {
    return request(`${ENTERPRISE_PREFIX}/assessments`, "POST", data);
}

async function apiBiasCheck(text) {
    return request(`${ENTERPRISE_PREFIX}/compliance/bias-check`, "POST", { text });
}


// ======================================================
// RESUME UPLOAD
// ======================================================

async function apiUploadResume(
    file,
    candidateId = "",
    jobDescription = ""
) {

    if (!getToken()) {

        redirectToLogin(
            "Please log in before uploading resumes."
        );

        throw new Error(
            "Authentication required"
        );
    }

    const formData = new FormData();

    formData.append("file", file);

    if (candidateId) {
        formData.append(
            "candidate_id",
            candidateId
        );
    }

    if (jobDescription) {
        formData.append(
            "job_description",
            jobDescription
        );
    }

    return request(
        "/upload-resume",
        "POST",
        formData,
        false,
        true
    );
}


// ======================================================
// SHORTLIST
// ======================================================

async function apiGetShortlist(jobId = null) {
    const query = jobId ? `?job_id=${encodeURIComponent(jobId)}` : "";
    return request(`${API_ROUTES.SHORTLIST}${query}`);
}

async function apiShortlistAnalytics(jobId = null) {
    const query = jobId ? `?job_id=${encodeURIComponent(jobId)}` : "";
    return request(`${API_ROUTES.SHORTLIST_ANALYTICS}${query}`);
}

async function apiAddToShortlist(data) {
    return request(API_ROUTES.SHORTLIST, "POST", data);
}

async function apiRemoveFromShortlist(candidateId, jobId = null) {
    const query = jobId ? `?job_id=${encodeURIComponent(jobId)}` : "";
    return request(`${API_ROUTES.SHORTLIST}/${encodeURIComponent(candidateId)}${query}`, "DELETE");
}

async function apiAutoShortlist(data) {
    return request(API_ROUTES.SHORTLIST_AUTO, "POST", data);
}

async function apiApproveShortlist(data) {
    return request(API_ROUTES.SHORTLIST_APPROVE, "POST", data);
}

async function apiRejectShortlist(data) {
    return request(API_ROUTES.SHORTLIST_REJECT, "POST", data);
}

async function apiGetCandidates() {
    return request("/candidates");
}

async function apiGetJobs() {
    return request("/jobs");
}

async function apiCreateJob(data) {
    return request("/jobs/create", "POST", data);
}

async function apiAIFeedback(data) {
    return request("/ai/feedback", "POST", data);
}

async function apiChatAssistant(data) {
    return request("/api/chat", "POST", {
        message: data?.message || "",
        candidates: data?.candidates || [],
        context: data?.context || {}
    });
}

async function apiDecision(data) {
    return request("/ai/decision", "POST", data);
}

async function apiInterviewRealtimeScore(data) {
    return request("/interview/realtime-score", "POST", data);
}

async function apiInterviewStart(data) {
    return request("/interview/start", "POST", data);
}

async function apiInterviewLive(data) {
    return request("/ai/interview/live", "POST", data);
}

async function apiInterviewProctor(data) {
    return request("/interview/proctor", "POST", data);
}

async function apiFaceVerify(data) {
    return request("/face/verify", "POST", data);
}

async function apiInterviewAnswer(data) {
    return request("/interview/answer", "POST", data);
}

async function apiInterviewResult(sessionId) {
    const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    return request(`/interview/result${query}`);
}


// ======================================================
// GENERIC API
// ======================================================

async function apiCall(
    endpoint,
    method = "GET",
    data = null
) {

    return request(
        endpoint,
        method,
        data
    );
}
