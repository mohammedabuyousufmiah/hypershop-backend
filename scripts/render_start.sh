#!/bin/sh
# Render free-tier start command. Free instances have no pre-deploy hook and
# no Shell, so we run migrations + IAM bootstrap + demo seed here on boot,
# then exec gunicorn. Every step is idempotent, so re-running on each
# cold-start wake is safe (just a little slower).
set -e

echo "[render-start] alembic upgrade head"
alembic upgrade head

echo "[render-start] iam-bootstrap (roles + permissions)"
python -m app.cli iam-bootstrap

if [ -n "${INITIAL_ADMIN_EMAIL:-}" ] && [ -n "${INITIAL_ADMIN_PASSWORD:-}" ]; then
  echo "[render-start] ensure admin ${INITIAL_ADMIN_EMAIL}"
  python -m app.cli create-superuser \
    --email "${INITIAL_ADMIN_EMAIL}" \
    --password "${INITIAL_ADMIN_PASSWORD}" || true
fi

echo "[render-start] seed demo mobile logins (customer + rider)"
python -m scripts.seed_mobile_logins || true

echo "[render-start] boot preflight"
python boot_preflight.py

echo "[render-start] launch gunicorn (${WEB_CONCURRENCY:-1} worker)"
exec gunicorn app.main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers "${WEB_CONCURRENCY:-1}" \
  --bind "0.0.0.0:${PORT:-8000}" \
  --worker-tmp-dir /dev/shm \
  --access-logfile - \
  --error-logfile - \
  --graceful-timeout 30 \
  --timeout 120 \
  --max-requests 1000 \
  --max-requests-jitter 100
