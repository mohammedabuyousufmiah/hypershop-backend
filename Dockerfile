FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    pip install --prefix=/install \
      "fastapi==0.115.6" "uvicorn[standard]==0.34.0" "gunicorn==23.0.0" "starlette==0.41.3" \
      "pydantic==2.10.4" "pydantic-settings==2.7.1" "email-validator==2.2.0" \
      "sqlalchemy[asyncio]==2.0.36" "alembic==1.14.0" "asyncpg==0.30.0" "psycopg2-binary==2.9.10" \
      "redis==5.2.1" "arq==0.26.3" \
      "argon2-cffi==23.1.0" "pyjwt==2.10.1" "cryptography==44.0.0" \
      "structlog==24.4.0" "httpx==0.28.1" "h2==4.1.0" "tenacity==9.0.0" \
      "aiosmtplib==3.0.2" "jinja2==3.1.5" "typer==0.15.1" \
      "fpdf2==2.8.1" \
      "boto3>=1.34,<2" \
      "prometheus-client>=0.20,<1" \
      "opentelemetry-api>=1.27,<2" "opentelemetry-sdk>=1.27,<2" \
      "opentelemetry-exporter-otlp-proto-http>=1.27,<2" \
      "opentelemetry-instrumentation-fastapi>=0.48b0,<1" \
      "opentelemetry-instrumentation-sqlalchemy>=0.48b0,<1" \
      "opentelemetry-instrumentation-httpx>=0.48b0,<1" \
      "opentelemetry-instrumentation-redis>=0.48b0,<1" \
      "openai>=1.40,<2"

# python-multipart is required by FastAPI for Form/multipart endpoints
# (catalog admin uploads etc.) but was missing from the list above — the app
# raises at import without it. Separate layer keeps the big install cached.
RUN pip install --prefix=/install "python-multipart==0.0.20" "openpyxl==3.1.5" "passlib[bcrypt]==1.7.4"

# ---------- runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/install/lib/python3.12/site-packages \
    PATH=/install/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r app --gid 1000 \
    && useradd -r -g app --uid 1000 --home-dir /app --shell /sbin/nologin app \
    # Pre-create persistent-volume mountpoints with correct ownership.
    # Docker named volumes inherit the mountpoint's uid:gid on first mount,
    # so without this the read_only rootfs + uid 1000 process cannot write
    # uploaded prescriptions / generated PDFs / POD photos.
    && mkdir -p /var/hypershop/prescriptions \
                /var/hypershop/doctor_rx_pdfs \
                /var/hypershop/delivery_pod \
                /var/hypershop/reports \
                /var/hypershop/product_videos \
    && chown -R app:app /var/hypershop

COPY --from=builder /install /install

WORKDIR /app
COPY --chown=app:app app ./app
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app alembic.ini ./alembic.ini
COPY --chown=app:app pyproject.toml ./pyproject.toml
COPY --chown=app:app boot_preflight.py ./boot_preflight.py
COPY --chown=app:app scripts ./scripts

# Pre-compile to .pyc as the app user so first-request cold start is fast.
# We flip PYTHONDONTWRITEBYTECODE off only for this single step; the runtime
# env still has it set so no surprise .pyc churn at request time.
# Errors during compile are non-fatal (Python falls back to lazy compile on
# import) — we just lose the pre-warm benefit.
RUN PYTHONDONTWRITEBYTECODE= python -m compileall -q -j 0 /app/app /app/alembic \
    && chown -R app:app /app/app /app/alembic

USER app

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1

# Boot preflight runs BEFORE gunicorn so misconfigured deploys exit with
# a clear "set XYZ" message instead of booting half-broken. Hard checks:
# ENVIRONMENT=production, JWT_SECRET≥32, POSTGRES_PASSWORD≥16, DATABASE_URL,
# REDIS_URL, CORS_ORIGINS (HTTPS-only in prod). Warn-only: OTP/payment creds.
#
# Gunicorn flags chosen for prod containers:
#   --workers $WEB_CONCURRENCY     env-driven worker count (default 4).
#                                  Standard rule: (2 × CPU) + 1 per pod.
#                                  Override at deploy: `-e WEB_CONCURRENCY=9`.
#   --worker-tmp-dir /dev/shm      tmpfs heartbeat path — avoids slow disk
#                                  IO triggering false worker-timeout kills.
#   --max-requests 1000 + jitter   recycles workers after N served requests
#                                  with stagger, caps slow memory growth.
#   --graceful-timeout 30          give workers 30s to drain on SIGTERM.
ENV WEB_CONCURRENCY=4

CMD ["sh", "-c", "python boot_preflight.py && exec gunicorn app.main:app \
     --worker-class uvicorn.workers.UvicornWorker \
     --workers ${WEB_CONCURRENCY:-4} \
     --bind 0.0.0.0:8000 \
     --worker-tmp-dir /dev/shm \
     --access-logfile - \
     --error-logfile - \
     --graceful-timeout 30 \
     --timeout 60 \
     --max-requests 1000 \
     --max-requests-jitter 100"]
