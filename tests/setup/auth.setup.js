const fs = require("node:fs");
const { test, expect } = require("../fixtures/enterprise-test");

const API_BASE = process.env.API_BASE || "http://127.0.0.1:8001";
const AUTH_FILE = "tests/.auth/recruiter.json";

test.use({ storageState: AUTH_FILE });

test("authenticated recruiter storage state is available", async ({ request }) => {
  test.setTimeout(10_000);

  const state = JSON.parse(fs.readFileSync(AUTH_FILE, "utf8"));
  const storage = state.origins?.[0]?.localStorage || [];
  const token = storage.find((item) => item.name === "token")?.value;
  const user = JSON.parse(storage.find((item) => item.name === "currentUser")?.value || "{}");

  expect(token, "global setup must store an authenticated access token").toBeTruthy();
  expect(user.email, "global setup must store the authenticated user").toBeTruthy();

  const response = await request.get(`${API_BASE}/auth/me`, {
    headers: {
      Authorization: `Bearer ${token}`
    },
    timeout: 5_000
  });
  expect(response.ok(), `stored token should authenticate against ${API_BASE}/auth/me`).toBeTruthy();

  const body = await response.json();
  expect(body.data?.email).toBe(user.email);
});
