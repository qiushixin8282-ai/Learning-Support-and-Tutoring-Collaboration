#!/usr/bin/env sh
set -eu
test "${APP_ENV:-}" = "production" || { echo "Set APP_ENV=production before deployment."; exit 1; }
test "${#JWT_SECRET}" -ge 32 || { echo "JWT_SECRET must be at least 32 characters."; exit 1; }
test -n "${TRUSTED_HOSTS:-}" || { echo "Set TRUSTED_HOSTS before deployment."; exit 1; }
docker compose up --build -d
echo "Deployment started. Verify http://<host>:8000/health and complete the runbook checks."
