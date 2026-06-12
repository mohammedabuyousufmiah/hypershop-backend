"""Permission decision tests — pure function, no DB."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.core.security.principal import Principal, SystemPrincipal
from app.modules.reporting import permission


def _def(allowed_roles: list[str], active: bool = True):
    """Tiny stub mimicking ReportDefinition's read-only attrs."""
    return SimpleNamespace(
        id=uuid4(),
        is_active=active,
        allowed_roles_json=list(allowed_roles),
        default_filters_json={},
    )


def _policy(role: str, *, view: bool = True, export: bool = False,
            schedule: bool = False, scope: str = "self"):
    return SimpleNamespace(
        role_label=role,
        scope_type=scope,
        can_view=view,
        can_export=export,
        can_schedule=schedule,
    )


def _principal(roles: list[str]) -> Principal:
    return Principal(
        user_id=uuid4(),
        session_id=uuid4(),
        roles=frozenset(roles),
        permissions=frozenset(),
    )


def test_inactive_definition_denies_everyone():
    d = _def(["admin"], active=False)
    decision = permission.evaluate(
        principal=_principal(["admin"]),
        definition=d,
        policies=[_policy("admin", view=True, export=True, schedule=True)],
    )
    assert decision.allowed is False
    assert "inactive" in decision.reason.lower()


def test_role_not_in_allowed_roles_denies():
    d = _def(["admin"])
    decision = permission.evaluate(
        principal=_principal(["customer"]),
        definition=d,
        policies=[_policy("customer", view=True)],
    )
    assert decision.allowed is False


def test_systemprincipal_always_allowed_global_scope():
    d = _def(["admin"])
    decision = permission.evaluate(
        principal=SystemPrincipal(),
        definition=d,
        policies=[],
    )
    assert decision.allowed is True
    assert decision.scope_type == "global"
    assert decision.can_view and decision.can_export and decision.can_schedule


def test_super_admin_role_bypasses_policies():
    d = _def(["super_admin"])
    decision = permission.evaluate(
        principal=_principal(["super_admin"]),
        definition=d,
        policies=[],  # no policy rows — bypass still works
    )
    assert decision.allowed is True
    assert decision.scope_type == "global"


def test_widest_scope_wins_across_multiple_roles():
    d = _def(["ops", "finance"])
    decision = permission.evaluate(
        principal=_principal(["ops", "finance"]),
        definition=d,
        policies=[
            _policy("ops", view=True, scope="self"),
            _policy("finance", view=True, scope="branch"),
        ],
    )
    assert decision.allowed is True
    assert decision.scope_type == "branch"


def test_union_of_capabilities_across_roles():
    d = _def(["ops", "finance"])
    decision = permission.evaluate(
        principal=_principal(["ops", "finance"]),
        definition=d,
        policies=[
            _policy("ops", view=True, export=False, schedule=False),
            _policy("finance", view=False, export=True, schedule=True),
        ],
    )
    assert decision.allowed is True
    assert decision.can_view is True
    assert decision.can_export is True
    assert decision.can_schedule is True


def test_no_matching_policy_denies():
    d = _def(["admin"])
    # User has the role but no policy row exists.
    decision = permission.evaluate(
        principal=_principal(["admin"]),
        definition=d,
        policies=[_policy("ops", view=True)],  # different role
    )
    assert decision.allowed is False


def test_merge_filters_request_overrides_default():
    d = SimpleNamespace(default_filters_json={"days": 30, "branch": "MAIN"})
    out = permission.merge_filters(
        request_filters={"days": 7}, definition=d,
    )
    assert out == {"days": 7, "branch": "MAIN"}
