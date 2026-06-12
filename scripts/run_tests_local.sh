#!/usr/bin/env bash
# Run the full Hypershop test suite against your locally-installed
# Postgres + Redis (no Docker / testcontainers required).
#
# Required env:
#   PG_USER, PG_PASSWORD
# Optional env:
#   PG_HOST=localhost  PG_PORT=5432  PG_DBNAME=hypershop_test
#   REDIS_HOST=localhost  REDIS_PORT=6379  REDIS_DB=15
#
# Usage:
#   PG_USER=postgres PG_PASSWORD=secret bash scripts/run_tests_local.sh

set -euo pipefail

PG_HOST="${PG_HOST:-localhost}"
PG_PORT="${PG_PORT:-5432}"
PG_DBNAME="${PG_DBNAME:-hypershop_test}"
REDIS_HOST="${REDIS_HOST:-localhost}"
REDIS_PORT="${REDIS_PORT:-6379}"
REDIS_DB="${REDIS_DB:-15}"

: "${PG_USER:?PG_USER is required}"
# PG_PASSWORD may be empty if your Postgres uses trust / peer / ident auth.
PG_PASSWORD="${PG_PASSWORD:-}"

python - <<'PY'
import os, psycopg2
from psycopg2 import sql, extensions
host = os.environ['PG_HOST']
port = int(os.environ['PG_PORT'])
user = os.environ['PG_USER']
pw   = os.environ['PG_PASSWORD']
dbname = os.environ['PG_DBNAME']
conn = psycopg2.connect(host=host, port=port, user=user, password=pw, dbname='postgres')
conn.set_isolation_level(extensions.ISOLATION_LEVEL_AUTOCOMMIT)
cur = conn.cursor()
cur.execute('SELECT 1 FROM pg_database WHERE datname = %s', (dbname,))
if cur.fetchone() is None:
    cur.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(dbname)))
    print(f'created database {dbname}')
else:
    print(f'database {dbname} already exists')
cur.close(); conn.close()
PY

export HYPERSHOP_TEST_DATABASE_URL="postgresql+asyncpg://${PG_USER}:${PG_PASSWORD}@${PG_HOST}:${PG_PORT}/${PG_DBNAME}"
export HYPERSHOP_TEST_REDIS_URL="redis://${REDIS_HOST}:${REDIS_PORT}/${REDIS_DB}"

echo "running tests against ${PG_DBNAME} ..."
pytest "$@"
