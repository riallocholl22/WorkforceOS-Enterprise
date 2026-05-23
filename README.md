# AI Recruitment Enterprise Platform

Investor/demo-ready FastAPI + HTML/CSS/JavaScript SaaS workspace for AI recruiting, candidate intelligence, AI matching, interviews, enterprise billing, analytics, security monitoring, and recruiter copilot workflows.

## Quick Start

1. Create and activate a Python environment.
2. Install backend dependencies:

```powershell
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill secrets locally. Keep real keys out of Git.
4. Start the backend:

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

5. Serve the frontend from `frontend/`:

```powershell
python -m http.server 5500 -d frontend
```

6. Open `http://127.0.0.1:5500/login.html`.

## Development Bootstrap

Development mode can seed a default enterprise workspace automatically:

```env
ENVIRONMENT=development
DEV_BOOTSTRAP_ENABLED=true
DEV_ADMIN_EMAIL=
```

The first user is assigned to the default organization as `super_admin`. Production mode never uses this shortcut.

## Core URLs

- Backend health: `http://127.0.0.1:8000/health`
- API docs: `http://127.0.0.1:8000/docs`
- OpenAPI: `http://127.0.0.1:8000/openapi.json`
- Frontend: `http://127.0.0.1:5500`

## QA Commands

```powershell
python -m py_compile main.py
python -m unittest test_auth.py test_enterprise_platform.py test_backend_consolidation.py test_dashboard_integration.py test_ai_pipeline.py test_resume.py
```

## Security Notes

- API keys remain backend-only in `.env`.
- CORS and trusted hosts must be explicit in production.
- Set `DEV_BOOTSTRAP_ENABLED=false` in production.
- Rotate any secret that was pasted into logs, screenshots, or shared files.
