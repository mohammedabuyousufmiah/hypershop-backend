# Module governance — the 7-contract rule

> **No new module ships unless it provides all 7 contracts.**

## The 7 contracts

| # | Contract | Lives in | Why |
|---|---|---|---|
| 1 | API routes | `app/modules/<name>/api/*.py` or `router.py` | Module is reachable |
| 2 | Permission list | dotted perm strings in `app/modules/iam/permissions.py` | Module is gated |
| 3 | Dashboard config | `app/modules/<name>/dashboard_config.py` with `WIDGETS` tuple (empty allowed) | Module surfaces on dashboard |
| 4 | Sidebar config | entry in `app/core/registry/admin_modules.py` | Module is discoverable in nav |
| 5 | Audit log actions | writes reference `audit_log` / `emit_audit` / `AuditLog` | Module is traceable |
| 6 | Tests | at least one `tests/test_*.py` mentioning the module | Module is verifiable |
| 7 | OpenAPI tags | routes annotated with `tags=[...]` | Module is documented |

## Workflow

### When scaffolding a new module

```bash
python -m scripts.check_module_contracts --module <new_name>
```

If any contract is missing, the script prints which one(s). Fix them
before merging. **A new module is never "done" at less than 7/7.**

### CI gate

`pyproject.toml`'s test job (or a separate workflow) should run:

```bash
python -m scripts.check_module_contracts --strict --skip-grandfather
```

- Exits **0** when every non-grandfathered module is 7/7.
- Exits **1** when any non-grandfathered module has gaps.
- The grandfather list (`scripts/module_grandfather.txt`) carries
  pre-2026-05-17 modules with known gaps as accepted legacy debt.

### When you bring a grandfathered module up to 7/7

1. Run `--module <name>` to confirm it scores 7/7.
2. Remove the name from `scripts/module_grandfather.txt`.
3. From now on the strict gate will fail if anyone regresses any
   contract on that module.

## What the checker does NOT verify

These are intentionally outside the scope — they need human judgment:

- **Permission *correctness*** — the checker only verifies *a* perm
  string with the module namespace exists. It does not check whether
  every write endpoint actually gates on that perm. Code review or
  a separate "gate coverage" linter handles that.
- **Audit *completeness*** — the checker verifies *some* audit
  emission exists. It does not check that *every* state-changing
  endpoint emits an audit row.
- **Test *adequacy*** — the checker verifies a test file mentions
  the module name. It does not verify the test actually exercises
  the right paths.
- **OpenAPI *quality*** — the checker verifies `tags=` is present.
  It does not verify summaries, descriptions, or response models
  are populated.

These deeper checks are recommended manual gates at PR review time.

## Snapshot — 2026-05-17

| Status | Count |
|---|---|
| Fully compliant (7/7) | 4 — `orders`, `inventory`, `payments`, `reporting` |
| With gaps (grandfathered) | 52 |
| Total modules | 56 |

The 4 compliant modules above are NOT in the grandfather list. They
must stay at 7/7 — any regression will break CI.

## Common gaps and how to close them

### Missing dashboard_config.py

Create the file even if you don't surface widgets:

```python
# app/modules/<name>/dashboard_config.py
"""Dashboard widgets owned by the <name> module."""
from __future__ import annotations

from app.core.registry.dashboard_widgets import DashboardWidget

# Explicit opt-out — this module doesn't surface dashboard widgets.
WIDGETS: tuple[DashboardWidget, ...] = ()
```

### Missing sidebar entry

Add to `app/core/registry/admin_modules.py`:

```python
AdminModule(
    code="<hyphen-name>",
    label_en="Display Name",
    label_bn="বাংলা নাম",
    group="Operations",          # one of the WidgetGroup constants
    href="/admin/<hyphen-name>",
    required_perm="<name>.read",
    api_prefix="/api/v1/<name>",
    icon="icon-name",
    order=100,
),
```

### Missing permission

Add to `app/modules/iam/permissions.py` under the right tier list, e.g.:

```python
"<name>.read",
"<name>.write",
```

### Missing audit

In any state-changing endpoint:

```python
from app.modules.iam.audit import emit_audit
await emit_audit(session, actor_id=principal.user_id,
                 action=f"<name>.create", resource_type="<thing>",
                 resource_id=str(new_id), outcome="success")
```

### Missing tests

```python
# tests/test_<name>_smoke.py
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

async def test_<name>_read_path(api_client: AsyncClient, admin_token: str) -> None:
    r = await api_client.get("/api/v1/<name>",
                              headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
```

### Missing OpenAPI tag

```python
router = APIRouter(prefix="/<name>", tags=["<name>"])
```
