"""Module governance checker — enforces the 7-contract rule.

Walks ``app/modules/<name>/`` and verifies each module declares all
seven required contracts before it can be considered operational:

  1. API routes           → ``api/*.py`` or ``router.py`` exists
  2. Permission list      → at least one perm referenced in
                            ``app/modules/iam/permissions.py``
  3. Dashboard config     → ``dashboard_config.py`` with ``WIDGETS``
                            (empty tuple is allowed — explicit opt-out)
  4. Sidebar config       → entry in
                            ``app/core/registry/admin_modules.py``
  5. Audit log actions    → write paths reference the shared audit
                            helper (or explicit ``# audit:none``
                            comment somewhere in the module)
  6. Tests                → at least one ``tests/test_*<name>*.py``
                            file mentions the module
  7. OpenAPI tags         → routes annotated with ``tags=[...]``
                            (FastAPI's `summary` autopopulates from
                            handler docstring)

Run modes::

  # Audit-only — exit 0 always, just print the matrix
  python -m scripts.check_module_contracts

  # CI gate — exit 1 if any module has gaps (use with --skip-grandfather
  # to ignore pre-existing modules listed in scripts/module_grandfather.txt)
  python -m scripts.check_module_contracts --strict [--skip-grandfather]

  # Single-module check — for use right after scaffolding a new module
  python -m scripts.check_module_contracts --module returns
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULES_DIR = ROOT / "app" / "modules"
PERMISSIONS_FILE = ROOT / "app" / "modules" / "iam" / "permissions.py"
SIDEBAR_FILE = ROOT / "app" / "core" / "registry" / "admin_modules.py"
TESTS_DIR = ROOT / "tests"
GRANDFATHER_FILE = ROOT / "scripts" / "module_grandfather.txt"


@dataclass
class ModuleReport:
    name: str
    api: bool = False
    permissions: bool = False
    dashboard: bool = False
    sidebar: bool = False
    audit: bool = False
    tests: bool = False
    openapi: bool = False
    notes: list[str] = field(default_factory=list)

    def missing(self) -> list[str]:
        out = []
        if not self.api:         out.append("api")
        if not self.permissions: out.append("permissions")
        if not self.dashboard:   out.append("dashboard")
        if not self.sidebar:     out.append("sidebar")
        if not self.audit:       out.append("audit")
        if not self.tests:       out.append("tests")
        if not self.openapi:     out.append("openapi")
        return out

    def score(self) -> str:
        present = 7 - len(self.missing())
        return f"{present}/7"


def _file_contains(path: pathlib.Path, needle: str) -> bool:
    try:
        return needle in path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def _module_dir_contains(mod_dir: pathlib.Path, needle: str) -> bool:
    for p in mod_dir.rglob("*.py"):
        if _file_contains(p, needle):
            return True
    return False


def _load_grandfather() -> set[str]:
    if not GRANDFATHER_FILE.exists():
        return set()
    return {
        line.strip()
        for line in GRANDFATHER_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


# Module-name → permission-namespace aliases. Many modules use a perm
# namespace that doesn't match their dir name (``rider_routing`` →
# ``rider.dispatch`` etc.). Extend when adding new modules.
_PERM_NAMESPACE_ALIASES: dict[str, tuple[str, ...]] = {
    "rider_routing":   ("rider", "riders"),
    "rider_wallet":    ("rider", "riders"),
    "supplier_payments": ("supplier", "finance"),
    "customer_care":   ("ai_care", "customercare", "support"),
    "support_tickets": ("support", "ticket"),
    "product_videos":  ("video", "videos"),
    "product_qa":      ("product_qa", "qa"),
    "rider_hardening": ("rider",),
    "rider_wallet_hardening": ("rider",),
    "kpi_dashboard":   ("dashboard", "kpi"),
    "admin_v3_stubs":  ("admin",),
    "whatsapp_webhook": ("whatsapp",),
}


def _check_module(name: str, sidebar_text: str, perms_text: str) -> ModuleReport:
    rep = ModuleReport(name=name)
    mod_dir = MODULES_DIR / name

    # 1. API
    api_dir = mod_dir / "api"
    router_py = mod_dir / "router.py"
    rep.api = (api_dir.exists() and any(api_dir.glob("*.py"))) or router_py.exists()

    # 2. Permissions — module name (or alias) appears as a dotted perm
    #    namespace in iam/permissions.py. Aliases handle the case where
    #    a module dir is named differently from its perm namespace.
    candidates = {name, name.replace("_", "-"), name.replace("_", "")}
    candidates.update(_PERM_NAMESPACE_ALIASES.get(name, ()))
    rep.permissions = any(
        f'"{cand}.' in perms_text or f"'{cand}." in perms_text
        for cand in candidates
    )

    # 3. Dashboard config — even an empty `WIDGETS = ()` counts as
    #    explicit opt-out. Missing file fails this gate.
    dash = mod_dir / "dashboard_config.py"
    rep.dashboard = dash.exists() and _file_contains(dash, "WIDGETS")

    # 4. Sidebar config — module's code appears in the central registry.
    hyphen = name.replace("_", "-")
    rep.sidebar = any(
        f'code="{c}"' in sidebar_text or f"code='{c}'" in sidebar_text
        for c in {name, hyphen}
    )

    # 5. Audit — module references audit (table name, model class, or
    #    helper function), or has an explicit opt-out comment.
    audit_patterns = (
        "audit_log",        # raw table name
        "AuditLog",         # model class
        "audit_logs",       # plural variant
        "emit_audit",       # shared helper prefix
        "record_audit",     # alternate helper
        "# audit:none",     # explicit opt-out marker
    )
    rep.audit = any(_module_dir_contains(mod_dir, p) for p in audit_patterns)

    # 6. Tests — any test file mentioning the module name.
    rep.tests = False
    if TESTS_DIR.exists():
        for p in TESTS_DIR.rglob("test_*.py"):
            if _file_contains(p, name) or _file_contains(p, hyphen):
                rep.tests = True
                break

    # 7. OpenAPI — at least one route handler annotated with tags=.
    rep.openapi = _module_dir_contains(mod_dir, "tags=")
    return rep


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 if any non-grandfathered module has gaps")
    parser.add_argument("--skip-grandfather", action="store_true",
                        help="ignore modules listed in module_grandfather.txt")
    parser.add_argument("--module", type=str,
                        help="check only one module by name")
    args = parser.parse_args()

    if not MODULES_DIR.exists():
        print(f"!! modules dir not found: {MODULES_DIR}", file=sys.stderr)
        return 2

    perms_text = (PERMISSIONS_FILE.read_text(encoding="utf-8", errors="ignore")
                  if PERMISSIONS_FILE.exists() else "")
    sidebar_text = (SIDEBAR_FILE.read_text(encoding="utf-8", errors="ignore")
                    if SIDEBAR_FILE.exists() else "")
    grandfather = _load_grandfather() if args.skip_grandfather else set()

    mod_names: list[str] = []
    if args.module:
        mod_names = [args.module]
    else:
        mod_names = sorted(
            p.name for p in MODULES_DIR.iterdir()
            if p.is_dir() and (p / "__init__.py").exists()
            and not p.name.startswith("_")
        )

    reports = [_check_module(n, sidebar_text, perms_text) for n in mod_names]

    # ---- print matrix
    print(f"{'module':<28} api perm dash side audi test oapi  score")
    print("-" * 78)
    bad: list[ModuleReport] = []
    for r in reports:
        cells = [
            "Y" if r.api else ".",
            "Y" if r.permissions else ".",
            "Y" if r.dashboard else ".",
            "Y" if r.sidebar else ".",
            "Y" if r.audit else ".",
            "Y" if r.tests else ".",
            "Y" if r.openapi else ".",
        ]
        skip = r.name in grandfather
        marker = " (grandfathered)" if skip else ""
        print(f"{r.name:<28} {'    '.join(cells)}   {r.score()}{marker}")
        if r.missing() and not skip:
            bad.append(r)

    print()
    print(f"Total modules: {len(reports)}")
    print(f"Fully compliant (7/7): {sum(1 for r in reports if not r.missing())}")
    print(f"With gaps: {len(reports) - sum(1 for r in reports if not r.missing())}")
    if grandfather:
        print(f"Grandfathered (skipped): {len(grandfather)}")

    if args.strict and bad:
        print()
        print("!! STRICT MODE: modules with gaps")
        for r in bad:
            print(f"  {r.name}: missing {', '.join(r.missing())}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
