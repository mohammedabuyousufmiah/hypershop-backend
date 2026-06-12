# Run the full Hypershop test suite against your locally-installed
# Postgres + Redis (no Docker / testcontainers required).
#
# Prerequisites:
#   - PostgreSQL is running on localhost:5432
#   - Redis is running on localhost:6379
#   - You have psql access as a user that can CREATE DATABASE
#
# Usage:
#   .\scripts\run_tests_local.ps1 -DbUser postgres -DbPassword <yourpw>
#
# What it does:
#   1. Creates database `hypershop_test` if missing (idempotent).
#   2. Sets HYPERSHOP_TEST_DATABASE_URL + HYPERSHOP_TEST_REDIS_URL.
#   3. Runs pytest. The conftest detects the env vars and skips testcontainers.

[CmdletBinding()]
param(
    [string]$DbHost = "localhost",
    [int]$DbPort = 5432,
    [Parameter(Mandatory = $true)] [string]$DbUser,
    [Parameter(Mandatory = $true)] [string]$DbPassword,
    [string]$DbName = "hypershop_test",
    [string]$RedisHost = "localhost",
    [int]$RedisPort = 6379,
    [int]$RedisDb = 15
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$createSql = "CREATE DATABASE $DbName;"

# Use python+psycopg2 to ensure the test DB exists. We avoid relying on `psql`
# being on PATH (it usually isn't on a default Windows install).
$createPyScript = @"
import os, sys
import psycopg2
from psycopg2 import sql, extensions
host = os.environ['_PGHOST']
port = int(os.environ['_PGPORT'])
user = os.environ['_PGUSER']
pw   = os.environ['_PGPASSWORD']
dbname = os.environ['_DBNAME']
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
"@

$env:_PGHOST = $DbHost
$env:_PGPORT = $DbPort
$env:_PGUSER = $DbUser
$env:_PGPASSWORD = $DbPassword
$env:_DBNAME = $DbName

python -c $createPyScript
if ($LASTEXITCODE -ne 0) { throw "failed to create test database" }

$env:HYPERSHOP_TEST_DATABASE_URL =
    "postgresql+asyncpg://${DbUser}:${DbPassword}@${DbHost}:${DbPort}/${DbName}"
$env:HYPERSHOP_TEST_REDIS_URL = "redis://${RedisHost}:${RedisPort}/${RedisDb}"

Write-Host "running tests against $DbName ..." -ForegroundColor Cyan
pytest @args
exit $LASTEXITCODE
