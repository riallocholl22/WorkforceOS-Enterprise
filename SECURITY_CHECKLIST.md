# Security Checklist

- Secrets are never committed and never exposed to frontend JavaScript.
- Development bootstrap is disabled outside local development.
- Passwords use Argon2-compatible hashing and password length limits.
- Refresh tokens rotate and logout revokes sessions.
- RBAC remains enabled for enterprise, billing, team, audit, and security routes.
- CORS allows only known frontend origins.
- Trusted hosts are explicit in production.
- Webhooks validate provider signatures where configured.
- Uploads validate file name, extension, and size before processing.
- Audit logs capture auth, billing, security, AI, and admin actions.
- AI assistant refuses malicious, credential theft, malware, and illegal requests.
