# Frontend QA Automation

This directory contains the enterprise browser QA layer for the AI Recruitment Platform.

## Commands

```powershell
npm install
npx playwright install
npm run lint:frontend
npm run test:frontend
npm run test:visual
npm run test:frontend:update-snapshots
```

The Playwright config starts the FastAPI backend on `127.0.0.1:8001` and the static frontend on `127.0.0.1:5500` unless those servers are already running locally.

## Environment

- `API_BASE`: backend origin, defaults to `http://127.0.0.1:8001`
- `FRONTEND_URL`: frontend origin, defaults to `http://127.0.0.1:5500`
- `E2E_EMAIL` and `E2E_PASSWORD`: optional existing QA user
- `E2E_MOCK_API=true`: deterministic browser-only mode for frontend infrastructure debugging
- `VISUAL_MAX_DIFF_RATIO`: visual snapshot tolerance

## Coverage

The suite includes authenticated E2E workflows, console/runtime auditing, visual regression, frontend security checks, AI interaction guardrails, performance budgets, screenshot/trace/video capture on failures, and CI reporting.
