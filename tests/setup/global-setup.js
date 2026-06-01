const fs = require("node:fs");
const path = require("node:path");
const { request } = require("@playwright/test");

const FRONTEND_URL = process.env.FRONTEND_URL || "http://127.0.0.1:5500";
const API_BASE = process.env.API_BASE || "http://127.0.0.1:8001";
const AUTH_DIR = path.join(__dirname, "..", ".auth");
const AUTH_FILE = path.join(AUTH_DIR, "recruiter.json");
const AUTH_TIMEOUT_MS = Number(process.env.E2E_AUTH_TIMEOUT_MS || 10_000);
const AUTH_RETRIES = Number(process.env.E2E_AUTH_RETRIES || 3);

function logAuth(step, details = {}) {
  const suffix = Object.keys(details).length ? ` ${JSON.stringify(details)}` : "";
  console.log(`[auth-setup] ${step}${suffix}`);
}

async function unwrap(response, label) {
  const text = await response.text();
  let body = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { message: text };
  }

  if (!response.ok()) {
    throw new Error(`[auth-setup] ${label} failed: ${response.status()} ${response.url()} ${JSON.stringify(body).slice(0, 700)}`);
  }

  return body && body.success && body.data ? body.data : body;
}

async function apiStep(api, label, fn) {
  let lastError;
  for (let attempt = 1; attempt <= AUTH_RETRIES; attempt += 1) {
    try {
      if (attempt > 1) {
        logAuth(`${label} retry`, { attempt });
      }
      return await fn();
    } catch (error) {
      lastError = error;
      const retryable = /ECONNRESET|ECONNREFUSED|ETIMEDOUT|Timeout|socket hang up|503|502|500/.test(error.message || "");
      if (!retryable || attempt === AUTH_RETRIES) {
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 350 * attempt));
    }
  }
  throw new Error(`[auth-setup] ${label}: ${lastError.message}`);
}

async function globalSetup() {
  fs.mkdirSync(AUTH_DIR, { recursive: true });

  const email = process.env.E2E_EMAIL || `qa.recruiter.${Date.now()}@example.test`;
  const password = process.env.E2E_PASSWORD || "RecruitQA2026!";
  const api = await request.newContext({
    baseURL: API_BASE,
    timeout: AUTH_TIMEOUT_MS,
    extraHTTPHeaders: {
      "X-E2E-Test-Mode": "true"
    }
  });

  try {
    logAuth("login started", { api: API_BASE, email });

    if (!process.env.E2E_EMAIL) {
      const registration = await apiStep(api, "registration", async () => unwrap(await api.post("/auth/register", {
        data: {
          email,
          password,
          organization_name: "Playwright Enterprise QA"
        }
      }), "registration"));

      const verificationToken = registration.verification_token;
      if (verificationToken) {
        await apiStep(api, "email verification", async () => unwrap(await api.post("/auth/verify-email", {
          data: { email, code: verificationToken }
        }), "email verification"));
        logAuth("email verified");
      }
    }

    const login = await apiStep(api, "password login", async () => unwrap(await api.post("/auth/login", {
      data: { email, password }
    }), "password login"));

    const challenge = login.challenge;
    if (!login.mfa_required || !challenge?.challenge_token) {
      throw new Error("[auth-setup] password login did not return an MFA challenge");
    }
    logAuth("MFA challenge created", {
      channel: challenge.delivery_channel,
      expires_at: challenge.expires_at,
      test_otp_available: Boolean(challenge.test_otp_available)
    });

    const otpPayload = await apiStep(api, "test OTP retrieval", async () => unwrap(await api.get(
      `/auth/testing/otp/${encodeURIComponent(challenge.challenge_token)}`
    ), "test OTP retrieval"));

    if (!/^\d{6}$/.test(String(otpPayload.otp || ""))) {
      throw new Error("[auth-setup] test OTP endpoint returned an invalid OTP shape");
    }
    logAuth("OTP retrieved", { expires_at: otpPayload.expires_at });

    const session = await apiStep(api, "MFA verification", async () => unwrap(await api.post("/auth/mfa/verify", {
      data: {
        challenge_token: challenge.challenge_token,
        otp: otpPayload.otp
      }
    }), "MFA verification"));

    if (!session.access_token || !session.refresh_token) {
      throw new Error("[auth-setup] MFA verification did not return access and refresh tokens");
    }
    logAuth("OTP verified");

    const user = session.user || login.user || { email };
    const state = {
      cookies: [],
      origins: [
        {
          origin: FRONTEND_URL,
          localStorage: [
            { name: "token", value: session.access_token },
            { name: "refreshToken", value: session.refresh_token },
            { name: "currentUser", value: JSON.stringify(user) },
            { name: "qaUserEmail", value: email }
          ]
        }
      ]
    };

    fs.writeFileSync(AUTH_FILE, JSON.stringify(state, null, 2));
    logAuth("session stored", { file: AUTH_FILE, role: user.role || "unknown" });
    logAuth("auth complete");
  } finally {
    await api.dispose();
  }
}

module.exports = globalSetup;
