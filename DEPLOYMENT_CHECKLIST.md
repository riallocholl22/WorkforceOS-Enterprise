# Deployment Checklist

- Set `ENVIRONMENT=production` and `DEBUG=false`.
- Set strong `JWT_SECRET`, production `DATABASE_URL`, `FRONTEND_URL`, `CORS_ORIGINS`, and `TRUSTED_HOSTS`.
- Set `DEV_BOOTSTRAP_ENABLED=false`.
- Configure HTTPS at the reverse proxy/load balancer.
- Run `python -m unittest test_auth.py test_enterprise_platform.py test_backend_consolidation.py test_dashboard_integration.py test_ai_pipeline.py test_resume.py`.
- Verify `/health`, `/docs`, and `/openapi.json`.
- Configure OpenAI and payment provider secrets only on the backend.
- Configure database backups and log retention.
- Review CSP, CORS, trusted hosts, secure cookies, and rate-limit policy.
- Smoke test login, dashboard, chat, upload, billing, security, and interview workflows.
