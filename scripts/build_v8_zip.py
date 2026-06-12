"""Package the canonical Hypershop estate into a single GOLIVE V8 zip.

Includes (under their normal layout):
  backend/         — _serve_final/hypershop-backend (source only)
  frontend/        — _serve_final/frontend (3 apps + 7 packages)
  docs/            — top-level operator docs
  memory/          — session memory snapshots from .claude
  MANIFEST.txt     — generated index of every file in the zip

Excludes (heavy / regenerable):
  __pycache__, .pytest_cache, .mypy_cache, .ruff_cache
  .venv, venv
  node_modules
  .next, dist, build, out
  *.pyc, *.log, *.tmp
  .DS_Store, Thumbs.db
  .git
"""
from __future__ import annotations

import datetime
import os
import sys
import zipfile
from pathlib import Path


_ROOT = Path(
    "C:/Users/imyou/OneDrive/Desktop/Yousuf/"
    "E CIMMERCE MASTER DATA/E COMMERCEH MASTER BANDLE/BACKEND/_serve_final",
)
_MEMORY_ROOT = Path(
    "C:/Users/imyou/.claude/projects/"
    "C--Users-imyou-OneDrive-Desktop-Yousuf-E-CIMMERCE-MASTER-DATA-"
    "E-COMMERCE-PREMIUM-BANDLE/memory",
)
_OUTPUT = Path(f"D:/HYPERSHOP_GOLIVE_V8_{datetime.date.today().isoformat()}.zip")

_EXCLUDE_DIRS = {
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".venv", "venv", "node_modules",
    ".next", "dist", "build", "out",
    ".git", ".github_old", ".idea", ".vscode",
    "egg-info",  # match *.egg-info via suffix below
}
_EXCLUDE_SUFFIX = {".pyc", ".pyo", ".log", ".tmp", ".swp"}
_EXCLUDE_FILES = {".DS_Store", "Thumbs.db", ".env", ".env.local"}


def _is_excluded(path: Path) -> bool:
    """True when ANY path part is in the exclude set."""
    name = path.name
    if name in _EXCLUDE_FILES:
        return True
    if name.endswith(".egg-info") or any(
        p.endswith(".egg-info") for p in path.parts
    ):
        return True
    if any(part in _EXCLUDE_DIRS for part in path.parts):
        return True
    return path.suffix.lower() in _EXCLUDE_SUFFIX


def _walk(base: Path):
    """Walk a directory yielding (full_path, arcname) tuples."""
    for root, dirs, files in os.walk(base):
        root_p = Path(root)
        # Prune excluded dirs in-place so os.walk doesn't descend
        dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS
                    and not d.endswith(".egg-info")]
        for f in files:
            full = root_p / f
            if _is_excluded(full):
                continue
            arc = full.relative_to(base)
            yield full, arc


def main() -> int:
    if not _ROOT.exists():
        print(f"missing source: {_ROOT}", file=sys.stderr)
        return 2
    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    if _OUTPUT.exists():
        _OUTPUT.unlink()
    print(f"output: {_OUTPUT}")

    # Sections — (label, source_dir, arc_prefix)
    sections: list[tuple[str, Path, str]] = [
        ("backend", _ROOT / "hypershop-backend", "backend"),
        ("frontend", _ROOT / "frontend", "frontend"),
    ]
    if _MEMORY_ROOT.exists():
        sections.append(("memory", _MEMORY_ROOT, "memory"))

    n_files = 0
    n_bytes = 0
    manifest_rows: list[tuple[str, int]] = []

    with zipfile.ZipFile(
        _OUTPUT, "w",
        compression=zipfile.ZIP_DEFLATED, compresslevel=6,
    ) as zf:
        for label, src, prefix in sections:
            if not src.exists():
                print(f"  [skip] {label}: {src} missing")
                continue
            print(f"  [pack] {label}: {src}")
            local_n = 0
            local_b = 0
            for full, arc in _walk(src):
                try:
                    sz = full.stat().st_size
                except OSError:
                    continue
                arc_name = f"{prefix}/{arc.as_posix()}"
                zf.write(full, arcname=arc_name)
                manifest_rows.append((arc_name, sz))
                local_n += 1
                local_b += sz
                if local_n % 500 == 0:
                    print(f"      {local_n} files / "
                          f"{local_b / (1024 * 1024):.1f} MB...")
            print(f"      ✓ {local_n} files / {local_b / (1024 * 1024):.1f} MB")
            n_files += local_n
            n_bytes += local_b

        # MANIFEST.txt — text listing the zip's full contents
        manifest_rows.sort(key=lambda r: r[0])
        manifest_lines = [
            "Hypershop Go-Live V8 — manifest",
            f"Generated: {datetime.datetime.now().isoformat()}",
            f"Files: {len(manifest_rows)}",
            f"Total uncompressed: {n_bytes / (1024 * 1024):.1f} MB",
            "",
            "path                                                  size_bytes",
            "-" * 80,
        ]
        for arc_name, sz in manifest_rows:
            manifest_lines.append(f"{arc_name:<70} {sz:>10}")
        zf.writestr("MANIFEST.txt", "\n".join(manifest_lines) + "\n")

        # README — quick-start instructions
        zf.writestr(
            "README.md",
            _README_TEXT.replace(
                "__GENERATED__", datetime.datetime.now().isoformat(),
            ).replace(
                "__FILES__", str(len(manifest_rows)),
            ).replace(
                "__SIZE__", f"{n_bytes / (1024 * 1024):.1f} MB",
            ),
        )

    out_mb = _OUTPUT.stat().st_size / (1024 * 1024)
    print()
    print(f"DONE → {_OUTPUT}")
    print(f"  source files     : {n_files}")
    print(f"  source bytes     : {n_bytes / (1024 * 1024):.1f} MB")
    print(f"  zip file size    : {out_mb:.1f} MB")
    print(f"  compression ratio: {(1 - out_mb / (n_bytes / (1024 * 1024))) * 100:.0f}%")
    return 0


_README_TEXT = """\
# Hypershop Go-Live V8 — 2026-05-26

Generated: __GENERATED__
Files: __FILES__
Size: __SIZE__ uncompressed

## What's inside

```
backend/      — FastAPI service (V7 base + Phase A-E role modules + SEO autogen)
frontend/     — Next.js monorepo
  apps/
    customer-web/   ⭐ canonical V7 marketplace storefront
    admin-panel/    admin dashboard (Phase B/C ops UIs landing here)
    seller-panel/   seller portal
  packages/         7 shared workspace packages
memory/       — session memory snapshots (canonical_hypershop_storefront.md
                 is the lock-in record for the V7 customer-web)
```

## Quick-start (cold boot, ~3 min)

```bash
# 1. Postgres
docker compose -f backend/docker-compose.yml up -d db redis

# 2. Backend
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # or pyproject.toml
.venv/Scripts/python -m alembic upgrade head
nohup .venv/Scripts/python -m uvicorn app.main:app \\
    --host 127.0.0.1 --port 8000 >/tmp/backend.log 2>&1 &

# 3. Frontends (each in own terminal OR background)
cd ../frontend
pnpm install
pnpm --filter @ecom/customer-web dev &     # :3000 storefront
pnpm --filter @ecom/seller-panel dev &     # :3001 seller
pnpm --filter @ecom/admin-panel dev &      # :3002 admin
```

Wait for "Ready in Xs" in each log → http://localhost:3000

## Smoke test

```bash
cd backend
.venv/Scripts/python -m scripts.smoke_full_project   # 28 liveness
.venv/Scripts/python -m scripts.smoke_deep_flows     # 28 workflow
```

Both should report `0 FAIL`.

## What's new vs V7

| Phase | Module | Tables | Verbs |
|---|---|---|---|
| A | authority matrix + 4 new roles | (lib) | 41 |
| B | Finance Manager ops | 7 | 38 |
| C | Inventory Manager ops | 8 | 26 |
| D | Supervisor + Last-Mile Mgr | 9 | 17 |
| E | Mother-QR workflow (12-state FSM) | 3 | 27 |
| — | Bulk SEO ingest (12k products) | 0 | — |
| — | 349 R2 product photos | 0 | — |
| — | 220 category FAQs | 0 | — |
| — | IndexNow live (key + ARQ cron) | 0 | — |

Total new alembic migrations: 0090 → 0093 (over V7's 0080 head).

## Canonical storefront

`frontend/apps/customer-web/` is the V7 marketplace storefront.
Title signature: `Hypershop — Bangladesh's Marketplace · Hypershop BD`

Never replace with the "fresh standalone" rebuild — see
`memory/canonical_hypershop_storefront.md`.

## Excluded from this zip

```
__pycache__ / .pytest_cache / .mypy_cache / .ruff_cache
.venv / venv
node_modules
.next / dist / build / out
.git
*.egg-info
*.pyc / *.pyo / *.log / *.tmp / *.swp
.DS_Store / Thumbs.db / .env / .env.local
```

Run `pnpm install` (frontend) and `pip install` (backend) on first
boot to regenerate the dependencies.
"""


if __name__ == "__main__":
    sys.exit(main())
