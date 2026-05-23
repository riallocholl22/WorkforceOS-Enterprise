const fs = require("node:fs");
const path = require("node:path");
const { request } = require("@playwright/test");

const FRONTEND_URL = process.env.FRONTEND_URL || "http://127.0.0.1:5500";
const API_BASE = process.env.API_BASE || "http://127.0.0.1:8000";
const AUTH_DIR = path.join(__dirname, "..", ".auth");
const AUTH_FILE = path.join(AUTH_DIR, "recruiter.json");

async function unwrap(response) {
  const text = await response.text();
  let body = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { message: text };
  }

  if (!response.ok()) {
    throw new Error(`${response.status()} ${response.url()} ${JSON.stringify(body).slice(0, 500)}`);
  }

  return body && body.success && body.data ? body.data : body;
}

async function globalSetup() {
  fs.mkdirSync(AUTH_DIR, { recursive: true });

  const email = process.env.E2E_EMAIL || `qa.recruiter.${Date.now()}@example.test`;
  const password = process.env.E2E_PASSWORD || "RecruitQA2026!";
  const api = await request.newContext({ baseURL: API_BASE });

  if (!process.env.E2E_EMAIL) {
    const registration = await unwrap(await api.post("/auth/register", {
      data: {
        email,
        password,
        organization_name: "Playwright Enterprise QA"
      }
    }));

    const verificationToken = registration.verification_token;
    if (verificationToken) {
      await unwrap(await api.post("/auth/verify-email", { data: { token: verificationToken } }));
    }
  }

  const login = await unwrap(await api.post("/auth/login", { data: { email, password } }));
  const user = login.user || { email };

  await api.dispose();

  const state = {
    cookies: [],
    origins: [
      {
        origin: FRONTEND_URL,
        localStorage: [
          { name: "token", value: login.access_token },
          { name: "refreshToken", value: login.refresh_token },
          { name: "currentUser", value: JSON.stringify(user) },
          { name: "qaUserEmail", value: email }
        ]
      }
    ]
  };

  fs.writeFileSync(AUTH_FILE, JSON.stringify(state, null, 2));
}

module.exports = globalSetup;
