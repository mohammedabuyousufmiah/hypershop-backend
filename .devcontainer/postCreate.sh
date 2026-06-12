#!/usr/bin/env bash
# Codespaces post-create bootstrap. Runs ONCE per Codespace creation.
#
# Side effects:
#   - Installs the project + dev deps via pip (hot-runnable: pytest, ruff,
#     mypy, alembic CLI all resolvable from PATH).
#   - Seeds a sane local .env from .env.example so `make run` works
#     without manual editing.
#   - Pre-pulls the postgres + redis images that testcontainers will
#     spin up at first test invocation, so `make test-int` doesn't
#     pay the image-pull cost during the test run.

set -euo pipefail

cd "$(dirname "$0")/.."

echo "[postCreate] installing project + dev deps via pip..."
pip install --upgrade pip
pip install -e ".[dev]"

# Make sure ruff + mypy + alembic + pytest are on PATH (pip installs
# scripts to ~/.local/bin in some images; --user is the default for
# vscode user).
echo "[postCreate] confirming dev tool versions:"
ruff --version
mypy --version
alembic --version
pytest --version | head -1
docker --version
docker compose version
make --version | head -1

if [ ! -f .env ]; then
    echo "[postCreate] seeding .env from .env.example..."
    cp .env.example .env
    # The dev .env.example uses 'change-me' placeholders — replace
    # JWT_SECRET with a fresh random one so `python -m app.main`
    # doesn't refuse to start.
    JWT=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
    # Use printf to avoid sed escape issues with the random alphanum.
    sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$JWT|" .env
    echo "[postCreate] .env created with a fresh JWT_SECRET."
fi

echo "[postCreate] pre-pulling postgres + redis images for testcontainers..."
docker pull postgres:16-alpine || true
docker pull redis:7-alpine || true

echo ""
echo "════════════════════════════════════════════════════════════════"
echo " Codespace ready. Common commands:"
echo "   make help                  list all targets"
echo "   make test-int              full pytest suite incl. testcontainers"
echo "   make prod-up               build + start the prod stack (port 8000)"
echo "   make prod-ps               health snapshot of prod stack"
echo "   make prod-logs             tail api + worker logs"
echo "   make prod-down             stop + clean up"
echo "   make lint type audit       static checks (mirrors CI)"
echo "════════════════════════════════════════════════════════════════"
