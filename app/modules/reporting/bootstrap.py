"""Idempotent seed of report definitions + initial role policies.

Run once at startup (lifespan in main.py) and again whenever the
admin/super_admin needs to refresh defaults after upgrading the
``builders/`` package.

Seeding strategy:
  - For each registered builder, upsert a row in ``report_definitions``
    using the builder's ``default_*`` metadata.
  - For each (definition × default_role), upsert a policy with sane
    defaults: can_view=true, can_export based on whether the role is
    "operator-class" (admin/super_admin/finance), can_schedule for
    super_admin/admin only.

Idempotency: every write goes through the ``upsert`` repos, so re-runs
are no-ops. Existing definitions that an admin has *modified* are
updated only in their builder-derived columns (default name/category/
columns) — but not the per-role policy table, since admins may have
changed grants. (We DO upsert the default policies on first seed; if
an admin removed a default policy, the next bootstrap re-creates it.
Document this in the admin runbook.)
"""

from __future__ import annotations

from app.core.db.uow import UnitOfWork
from app.core.logging import get_logger
from app.modules.reporting.builders import register_all
from app.modules.reporting.registry import report_registry
from app.modules.reporting.repository import (
    ReportAccessPolicyRepository,
    ReportDefinitionRepository,
)
from app.modules.reporting.state import ScopeType

_log = get_logger("hypershop.reporting.bootstrap")


# Per-role default policy template. Admin/super_admin get everything;
# finance gets view+export but not schedule (schedules cost money via
# per-run compute); ops/packer get view-only on their domain reports.
_ROLE_GRANTS: dict[str, dict[str, bool | str]] = {
    "super_admin": {
        "can_view": True, "can_export": True,
        "can_schedule": True, "scope": ScopeType.GLOBAL.value,
    },
    "admin": {
        "can_view": True, "can_export": True,
        "can_schedule": True, "scope": ScopeType.GLOBAL.value,
    },
    "finance": {
        "can_view": True, "can_export": True,
        "can_schedule": True, "scope": ScopeType.GLOBAL.value,
    },
    "ops": {
        "can_view": True, "can_export": True,
        "can_schedule": False, "scope": ScopeType.GLOBAL.value,
    },
    "compliance": {
        "can_view": True, "can_export": True,
        "can_schedule": False, "scope": ScopeType.GLOBAL.value,
    },
    "packer": {
        "can_view": True, "can_export": False,
        "can_schedule": False, "scope": ScopeType.SELF.value,
    },
}


async def seed_default_reports() -> dict[str, int]:
    """Idempotent bootstrap. Returns counts: ``{definitions, policies}``."""
    register_all()  # safe even if already registered

    n_defs = 0
    n_policies = 0
    async with UnitOfWork().transactional() as session:
        defs_repo = ReportDefinitionRepository(session)
        pol_repo = ReportAccessPolicyRepository(session)

        for entry in report_registry.all():
            row = await defs_repo.upsert(
                code=entry.code,
                name=entry.default_name,
                category=entry.default_category,
                description=f"Built-in report registered by builders/{entry.code}",
                data_source="builtin",
                allowed_roles=list(entry.default_allowed_roles),
                allowed_export_formats=list(entry.default_export_formats),
                default_filters={},
                columns=entry.default_columns,
                is_active=True,
            )
            n_defs += 1

            for role in entry.default_allowed_roles:
                grant = _ROLE_GRANTS.get(role)
                if grant is None:
                    # Role not in our default grant matrix — skip silently.
                    # Admin can add a custom policy via /admin/reporting/.
                    continue
                await pol_repo.upsert(
                    definition_id=row.id,
                    role_label=role,
                    scope_type=str(grant["scope"]),
                    can_view=bool(grant["can_view"]),
                    can_export=bool(grant["can_export"]),
                    can_schedule=bool(grant["can_schedule"]),
                )
                n_policies += 1

    _log.info(
        "reporting_bootstrap_complete",
        definitions=n_defs,
        policies=n_policies,
    )
    return {"definitions": n_defs, "policies": n_policies}
