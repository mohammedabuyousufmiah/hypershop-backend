"""Stub routers covering every v3 admin path the FE api-client exercises.

Why this exists: the M10-M26 admin UIs were shipped against the
``admin-v3.ts`` namespace contract before the backend implementations
landed. Every panel ended up showing ``404: [object Object]`` because
the routes simply weren't mounted. This module mounts them with
deterministic-but-empty responses so the UI surfaces work end-to-end
in dev / smoke environments. Real implementations replace the stubs
endpoint-by-endpoint without UI changes.

Response conventions
--------------------
* **List** → ``{"items": [], "total": 0}`` so AdminV3Shell's
  ``EmptyState`` branch renders cleanly.
* **Get-by-id** → minimal entity with the path-bound id, a status,
  and ``created_at`` so JsonPanel formats nicely.
* **POST / PATCH / PUT** → ``{"ok": true, "echo": body}`` so the
  action panel's "Got response" badge fires.
* **DELETE** → 204 No Content.

Permission gating
-----------------
All routes require the existing ``catalog.product.write`` permission
because the admin panel sits behind it everywhere already. The
production replacements should narrow this — but the stub keeps the
gate consistent with the v1 admin surface so non-staff still get
denied.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Body, Depends
from sqlalchemy import text

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission

_PERM = "catalog.product.write"
_GUARD = [Depends(requires_permission(_PERM))]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_list() -> dict[str, Any]:
    return {"items": [], "total": 0}


def _ack(body: Any = None) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True, "id": str(uuid4()), "created_at": _now()}
    if body is not None:
        out["echo"] = body
    return out


# ════════════════════════════════════════════════════════════════════════
# Top-level router that aggregates every namespace below.
# ════════════════════════════════════════════════════════════════════════
admin_v3_stubs_router = APIRouter(tags=["admin-v3-stubs"])


# ────────────────────────────────────────────────────────────────────────
# finance-hardening
# ────────────────────────────────────────────────────────────────────────
_finance = APIRouter(prefix="/admin/finance-hardening", dependencies=_GUARD)


@_finance.get("/fx/rates")
async def list_fx_rates() -> dict[str, Any]:
    return _empty_list()


@_finance.post("/fx/rates")
async def upsert_fx_rate(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return _ack(body)


@_finance.post("/vat/compute")
async def compute_vat(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {
        "period": body.get("from_date", "")[:7],
        "input_vat_minor": 0,
        "output_vat_minor": 0,
        "net_vat_minor": 0,
        "lines": [],
    }


@_finance.post("/vat/filing-runs")
async def vat_filing_run(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {"id": 1, "status": "draft", "filed_at": None, **body}


@_finance.get("/vat/filing-runs")
async def list_vat_filings() -> dict[str, Any]:
    return _empty_list()


@_finance.post("/tds/compute")
async def compute_tds(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {
        "vendor_id": body.get("vendor_id"),
        "gross_amount": body.get("gross_amount", "0"),
        "tds_amount": "0",
        "net_amount": body.get("gross_amount", "0"),
        "section": body.get("section", ""),
    }


@_finance.post("/bank/statements/import")
async def import_bank_statement(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {"id": 1, "imported_at": _now(), "rows": 0, "matched": 0, "unmatched": 0, **body}


@_finance.get("/bank/statements")
async def list_bank_statements() -> dict[str, Any]:
    return _empty_list()


@_finance.post("/bank/match")
async def match_bank_statements(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {"matched": 0, "unmatched": 0, "details": [], **body}


@_finance.post("/periods/close")
async def close_period_hardened(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {"status": "closed", "closed_at": _now(), **body}


@_finance.post("/journal/reverse")
async def reverse_entry(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {"reversed_entry_id": body.get("entry_id"), **body}


@_finance.post("/intercompany/eliminate")
async def intercompany_eliminate(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {"entries_posted": 0, **body}


@_finance.get("/reports/trial-balance")
async def trial_balance_hardened() -> dict[str, Any]:
    return {
        "as_of": _now(),
        "rows": [],
        "totals": {"debit_minor": 0, "credit_minor": 0},
    }


@_finance.get("/reports/profit-and-loss")
async def p_and_l() -> dict[str, Any]:
    return {
        "period": _now()[:7],
        "revenue_minor": 0,
        "cogs_minor": 0,
        "gross_profit_minor": 0,
        "opex_minor": 0,
        "net_income_minor": 0,
    }


admin_v3_stubs_router.include_router(_finance)


# ────────────────────────────────────────────────────────────────────────
# security-hardening
# ────────────────────────────────────────────────────────────────────────
_security = APIRouter(prefix="/admin/security-hardening", dependencies=_GUARD)


_AV = "app.modules.admin_v3_stubs"


@_security.get("/headers")
async def get_security_headers(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.get_singleton(uow, "security_headers", {
        "csp": "default-src 'self'", "hsts_max_age": 31536000,
        "x_frame_options": "DENY", "x_content_type_options": "nosniff",
        "referrer_policy": "strict-origin-when-cross-origin"})


@_security.post("/headers")
async def set_security_headers(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.upsert_singleton(uow, "security_headers", body)


@_security.get("/csrf/status")
async def csrf_status() -> dict[str, Any]:
    return {"enabled": True, "double_submit": True, "window_seconds": 3600}


@_security.get("/vault/secrets")
async def list_secrets(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "vault_secret")


@_security.post("/vault/secrets")
async def store_secret(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    # SECURITY: never persist the raw secret value — only a name + masked hint.
    from app.modules.admin_v3_stubs import store
    name = str(body.get("name") or "unnamed")
    raw = str(body.get("value") or "")
    masked = (raw[:2] + "***" + raw[-2:]) if len(raw) >= 4 else "***"
    return await store.create(uow, "vault_secret",
                              {"name": name, "masked": masked}, ref=name)


@_security.post("/vault/secrets/{secret_id}/rotate")
async def rotate_secret(
    secret_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    r = await store.patch(uow, "vault_secret", secret_id, {"rotated_at": _now()})
    return r or {"error": "secret not found", "id": secret_id}


@_security.post("/hmac/sign")
async def hmac_sign(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import realize
    return realize.hmac_sign(body)


@_security.post("/webhook/verify")
async def webhook_verify(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import realize
    return realize.webhook_verify(body)


@_security.post("/dlp/scan")
async def dlp_scan(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import realize
    return realize.dlp_scan(body)


@_security.get("/tls/observations")
async def list_tls(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "tls_observation")


@_security.post("/tls/observations")
async def record_tls(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "tls_observation", body)


@_security.get("/vulnerabilities")
async def list_vulns(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "vulnerability")


@_security.post("/vulnerabilities")
async def record_vuln(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "vulnerability", body, status="open")


@_security.get("/incidents")
async def list_incidents(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "security_incident")


@_security.post("/incidents")
async def create_incident(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "security_incident", body, status="open")


@_security.patch("/incidents/{incident_id}")
async def update_incident(
    incident_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    status = body.pop("status", None) if isinstance(body, dict) else None
    r = await store.patch(uow, "security_incident", incident_id, body, status=status)
    return r or {"error": "incident not found", "id": incident_id}


@_security.get("/dependencies/scans")
async def list_dep_scans(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "dependency_scan")


@_security.post("/dependencies/scans")
async def record_dep_scan(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "dependency_scan", body)


admin_v3_stubs_router.include_router(_security)


# ────────────────────────────────────────────────────────────────────────
# infra-hardening
# ────────────────────────────────────────────────────────────────────────
_infra = APIRouter(prefix="/admin/infra-hardening", dependencies=_GUARD)


@_infra.post("/queue/enqueue")
async def queue_enqueue(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return _ack(body)


@_infra.get("/queue/dlq")
async def queue_dlq() -> dict[str, Any]:
    return _empty_list()


@_infra.post("/queue/requeue")
async def queue_requeue(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return _ack(body)


@_infra.get("/workers/heartbeats")
async def worker_heartbeats() -> dict[str, Any]:
    return _empty_list()


@_infra.post("/storage/put")
async def storage_put(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return _ack(body)


@_infra.get("/storage/{key}")
async def storage_get(key: str) -> dict[str, Any]:
    return {"key": key, "exists": False}


@_infra.delete("/storage/{key}")
async def storage_delete(key: str) -> dict[str, Any]:
    return {"key": key, "deleted": True}


@_infra.get("/metrics")
async def metrics_json() -> dict[str, Any]:
    return {"counters": {}, "gauges": {}, "histograms": {}}


@_infra.get("/metrics/prometheus")
async def metrics_prom() -> dict[str, Any]:
    return {"format": "prometheus", "text": "# stub\n"}


@_infra.get("/health")
async def health_checks() -> list[dict[str, Any]]:
    return [
        {"service": "db", "status": "ok", "details": None},
        {"service": "redis", "status": "ok", "details": None},
    ]


@_infra.get("/circuit-breakers")
async def breaker_status() -> dict[str, Any]:
    return _empty_list()


@_infra.post("/circuit-breakers/{name}/reset")
async def breaker_reset(name: str) -> dict[str, Any]:
    return {"name": name, "state": "closed", "failure_count": 0, "last_failure_at": None}


@_infra.get("/scheduled-tasks")
async def list_scheduled_tasks() -> dict[str, Any]:
    return _empty_list()


@_infra.get("/lifecycle-policies")
async def list_lifecycle_policies() -> dict[str, Any]:
    return _empty_list()


@_infra.post("/lifecycle-policies")
async def upsert_lifecycle_policy(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return _ack(body)


@_infra.post("/tracing/export")
async def tracing_export(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return _ack(body)


@_infra.get("/logs")
async def structured_logs() -> dict[str, Any]:
    return _empty_list()


admin_v3_stubs_router.include_router(_infra)


# ────────────────────────────────────────────────────────────────────────
# fraud-analytics-hardening
# ────────────────────────────────────────────────────────────────────────
_fraud = APIRouter(prefix="/admin/fraud-analytics-hardening", dependencies=_GUARD)


@_fraud.post("/rules")
async def fa_create_rule(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return _ack(body)


@_fraud.get("/rules")
async def fa_list_rules() -> dict[str, Any]:
    from app.modules.admin_v3_stubs import fraud_real
    rules = fraud_real.list_rules()
    return {"items": rules, "total": len(rules)}


@_fraud.patch("/rules/{rule_id}")
async def fa_update_rule(rule_id: int, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {"id": rule_id, **body}


@_fraud.post("/evaluate")
async def fa_evaluate(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import fraud_real
    return await fraud_real.evaluate(uow, body)


@_fraud.get("/velocity")
async def fa_velocity(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: int = 20,
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import fraud_real
    return await fraud_real.velocity(uow, limit=limit)


@_fraud.post("/cohorts/compute")
async def fa_cohort_compute(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {"id": 1, "rows": [], **body}


@_fraud.get("/anomalies")
async def fa_list_anomalies(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: int = 30,
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import fraud_real
    return await fraud_real.anomalies(uow, limit=limit)


@_fraud.post("/anomalies")
async def fa_record_anomaly(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return _ack(body)


@_fraud.post("/dwh/export")
async def fa_dwh_export(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return _ack(body)


@_fraud.post("/funnels/compute")
async def fa_funnel_compute(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {"id": 1, "stages": body.get("stages", []), "conversion": []}


@_fraud.get("/cache/stats")
async def fa_cache_stats() -> dict[str, Any]:
    return {"hits": 0, "misses": 0, "size": 0}


@_fraud.post("/events/enrich")
async def fa_enrich(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return _ack(body)


admin_v3_stubs_router.include_router(_fraud)


# ────────────────────────────────────────────────────────────────────────
# automation
# ────────────────────────────────────────────────────────────────────────
_automation = APIRouter(prefix="/admin/automation", dependencies=_GUARD)


@_automation.get("/runs")
async def auto_runs(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import automation_real
    return await automation_real.list_runs(uow)


@_automation.get("/decisions")
async def auto_decisions(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import automation_real
    return await automation_real.list_decisions(uow)


@_automation.post("/decisions/{decision_id}/override")
async def auto_override(
    decision_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import automation_real
    return await automation_real.override_decision(uow, decision_id, body)


@_automation.get("/fraud-proposals")
async def auto_fraud_proposals(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import automation_real
    return await automation_real.list_proposals(uow, "fraud")


@_automation.post("/fraud-proposals/{proposal_id}/review")
async def auto_review_fraud(
    proposal_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import automation_real
    return await automation_real.review_proposal(uow, "fraud", proposal_id, body)


@_automation.get("/alert-proposals")
async def auto_alert_proposals(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import automation_real
    return await automation_real.list_proposals(uow, "alert")


@_automation.post("/alert-proposals/{proposal_id}/review")
async def auto_review_alert(
    proposal_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import automation_real
    return await automation_real.review_proposal(uow, "alert", proposal_id, body)


@_automation.post("/drills/run")
async def auto_drill_run(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import automation_real
    return await automation_real.run_drill(uow, body)


@_automation.get("/drills")
async def auto_drill_results(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import automation_real
    return await automation_real.list_drills(uow)


admin_v3_stubs_router.include_router(_automation)


# ────────────────────────────────────────────────────────────────────────
# workflows
# ────────────────────────────────────────────────────────────────────────
_workflows = APIRouter(prefix="/admin/workflows", dependencies=_GUARD)


@_workflows.get("/definitions")
async def wf_defs(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import workflows_real
    return await workflows_real.list_definitions(uow)


@_workflows.get("/runs")
async def wf_runs(
    uow: Annotated[UnitOfWork, Depends(get_uow)], limit: int = 50,
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import workflows_real
    return await workflows_real.list_runs(uow, limit=limit)


@_workflows.get("/runs/{run_id}")
async def wf_run(
    run_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import workflows_real
    return await workflows_real.get_run(uow, run_id)


@_workflows.post("/{workflow_code}/trigger")
async def wf_trigger(
    workflow_code: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import workflows_real
    return await workflows_real.trigger(uow, workflow_code, body)


@_workflows.post("/{workflow_code}/kill-switch")
async def wf_kill(
    workflow_code: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import workflows_real
    enabled = bool((body or {}).get("enabled", (body or {}).get("kill_switch", True)))
    return await workflows_real.kill_switch(uow, workflow_code, enabled)


@_workflows.post("/runs/{run_id}/resolve-gate")
async def wf_resolve(
    run_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import workflows_real
    return await workflows_real.resolve_gate(uow, run_id, body)


@_workflows.post("/runs/{run_id}/steps/{step_id}/retry")
async def wf_retry(
    run_id: int, step_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import workflows_real
    return await workflows_real.retry_step(uow, run_id, step_id)


@_workflows.post("/runs/{run_id}/cancel")
async def wf_cancel(
    run_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import workflows_real
    from sqlalchemy import text as _t
    async with uow.transactional() as s:
        await s.execute(_t(
            "UPDATE workflow_runs SET status='cancelled', finished_at=now() "
            "WHERE id=:id AND status NOT IN ('succeeded','failed')"), {"id": run_id})
    return await workflows_real.get_run(uow, run_id)


admin_v3_stubs_router.include_router(_workflows)


# ────────────────────────────────────────────────────────────────────────
# bi
# ────────────────────────────────────────────────────────────────────────
_bi = APIRouter(prefix="/admin/bi", dependencies=_GUARD)


@_bi.get("/kpis/executive")
async def bi_exec(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> list[dict[str, Any]]:
    from app.modules.admin_v3_stubs import bi_real
    return await bi_real.executive_kpis(uow)


@_bi.get("/kpis/sparkline")
async def bi_sparkline(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    metric_code: str = "orders",
    days: int = 14,
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import bi_real
    return await bi_real.sparkline(uow, metric_code=metric_code, days=days)


@_bi.get("/facts/daily")
async def bi_facts(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    # Real daily fact series derived from orders (events + gmv per day).
    from app.modules.admin_v3_stubs import bi_real
    sp = await bi_real.sparkline(uow, metric_code="orders", days=30)
    return {"items": sp["points"], "total": len(sp["points"])}


@_bi.post("/facts/daily")
async def bi_record_fact(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "bi_fact", body)


@_bi.post("/cohorts/compute")
async def bi_cohort_compute(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    # Real monthly new-buyer cohorts from first-order month.
    from sqlalchemy import text as _t
    from app.modules.admin_v3_stubs import store
    async with uow.transactional() as s:
        try:
            rows = list((await s.execute(_t(
                "SELECT to_char(first_m,'YYYY-MM') AS cohort, count(*) AS buyers FROM ("
                "  SELECT customer_user_id, min(created_at)::date AS first_m "
                "  FROM orders GROUP BY customer_user_id) t "
                "GROUP BY cohort ORDER BY cohort"))).all())
        except Exception:
            rows = []
    cohorts = [{"cohort": r[0], "buyers": int(r[1])} for r in rows]
    saved = await store.create(uow, "bi_cohort", {"rows": cohorts, "label": body.get("label", "new_buyers")})
    return {"id": saved["id"], "rows": cohorts}


@_bi.get("/cohorts")
async def bi_cohorts(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "bi_cohort")


@_bi.post("/funnels/compute")
async def bi_funnel_compute(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    # Real order-status funnel from the orders table.
    from sqlalchemy import text as _t
    from app.modules.admin_v3_stubs import store
    stages = ["pending_payment", "approved", "out_for_delivery", "completed"]
    async with uow.transactional() as s:
        counts = {}
        for st in stages:
            try:
                counts[st] = int((await s.execute(_t(
                    "SELECT count(*) FROM orders WHERE status=:st"), {"st": st})).scalar() or 0)
            except Exception:
                counts[st] = 0
    top = max(1, counts.get(stages[0], 0) or max(counts.values() or [1]))
    conversion = [{"stage": st, "count": counts[st],
                   "pct": round(100.0 * counts[st] / top, 1)} for st in stages]
    saved = await store.create(uow, "bi_funnel", {"stages": stages, "conversion": conversion})
    return {"id": saved["id"], "stages": stages, "conversion": conversion}


@_bi.get("/funnels")
async def bi_funnels(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "bi_funnel")


@_bi.post("/reports/saved")
async def bi_save_report(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "bi_saved_report", body)


@_bi.get("/reports/saved")
async def bi_saved_reports(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "bi_saved_report")


@_bi.delete("/reports/saved/{report_id}")
async def bi_delete_report(
    report_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return {"id": report_id, "deleted": await store.remove(uow, "bi_saved_report", report_id)}


@_bi.get("/fraud-cases")
async def bi_fraud_cases(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "bi_fraud_case")


@_bi.get("/fraud-cases/{case_id}")
async def bi_get_case(
    case_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.get(uow, "bi_fraud_case", case_id) or {"error": "not found", "id": case_id}


@_bi.patch("/fraud-cases/{case_id}")
async def bi_update_case(
    case_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    status = body.pop("status", None) if isinstance(body, dict) else None
    return await store.patch(uow, "bi_fraud_case", case_id, body, status=status) or {"error": "not found", "id": case_id}


@_bi.post("/fraud-cases/{case_id}/close")
async def bi_close_case(
    case_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.set_status(uow, "bi_fraud_case", case_id, "closed", body) or {"error": "not found", "id": case_id}


@_bi.post("/fraud-cases/{case_id}/reopen")
async def bi_reopen_case(
    case_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.set_status(uow, "bi_fraud_case", case_id, "open", body) or {"error": "not found", "id": case_id}


@_bi.get("/facts/export")
async def bi_export_facts() -> dict[str, Any]:
    return {"download_url": None, "note": "export runs async; not enabled in demo"}


@_bi.get("/kpis/derived")
async def bi_derived(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> list[dict[str, Any]]:
    from app.modules.admin_v3_stubs import bi_real
    return await bi_real.derived_kpis(uow)


admin_v3_stubs_router.include_router(_bi)


# ────────────────────────────────────────────────────────────────────────
# ops
# ────────────────────────────────────────────────────────────────────────
_ops = APIRouter(prefix="/admin/ops", dependencies=_GUARD)


@_ops.get("/policy")
async def ops_policy(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "ops_policy")


@_ops.post("/requests")
async def ops_propose(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "ops_request", body, status="open")


@_ops.get("/requests")
async def ops_requests(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "ops_request")


@_ops.get("/requests/{request_id}")
async def ops_request(
    request_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.get(uow, "ops_request", request_id) or {"error": "not found", "id": request_id}


@_ops.post("/requests/{request_id}/approve")
async def ops_approve(
    request_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.set_status(uow, "ops_request", request_id, "approved", body) or {"error": "not found", "id": request_id}


@_ops.post("/requests/{request_id}/reject")
async def ops_reject(
    request_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.set_status(uow, "ops_request", request_id, "rejected", body) or {"error": "not found", "id": request_id}


@_ops.post("/requests/{request_id}/cancel")
async def ops_cancel(
    request_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.set_status(uow, "ops_request", request_id, "cancelled", body) or {"error": "not found", "id": request_id}


@_ops.post("/audit/search")
async def ops_audit_search(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    # Search the real audit_log table defensively.
    q = str((body or {}).get("q") or "")
    async with uow.transactional() as s:
        try:
            rows = list((await s.execute(text(
                "SELECT action, actor_user_id::text, created_at FROM audit_log "
                "WHERE (:q = '' OR action ILIKE '%'||:q||'%') "
                "ORDER BY created_at DESC LIMIT 50"), {"q": q})).all())
        except Exception:
            rows = []
    items = [{"action": r[0], "actor": r[1],
              "created_at": r[2].isoformat() if r[2] else None} for r in rows]
    return {"items": items, "total": len(items)}


@_ops.post("/audit/export")
async def ops_audit_export(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return {"download_url": None, "note": "export runs async; not enabled in demo"}


@_ops.post("/break-glass/open")
async def ops_bg_open(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "break_glass", {"opened_at": _now(), **body}, status="open")


@_ops.post("/break-glass/{event_id}/close")
async def ops_bg_close(
    event_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.set_status(uow, "break_glass", event_id, "closed", {"closed_at": _now(), **body}) or {"error": "not found", "id": event_id}


@_ops.get("/break-glass/events")
async def ops_bg_events(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "break_glass")


@_ops.get("/velocity/tripped")
async def ops_velocity(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "ops_velocity_trip")


admin_v3_stubs_router.include_router(_ops)


# ────────────────────────────────────────────────────────────────────────
# order-trust
# ────────────────────────────────────────────────────────────────────────
_otrust = APIRouter(prefix="/admin/order-trust", dependencies=_GUARD)


@_otrust.post("/risk/{customer_id}")
async def ot_upsert_risk(
    customer_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.upsert_singleton(uow, f"customer_risk:{customer_id}", body)


@_otrust.get("/risk/{customer_id}")
async def ot_get_risk(
    customer_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import realize
    return await realize.customer_risk(uow, str(customer_id))


@_otrust.post("/blacklist")
async def ot_add_blacklist(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "order_blacklist", body, ref=str(body.get("value") or ""))


@_otrust.get("/blacklist")
async def ot_list_blacklist(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "order_blacklist")


@_otrust.delete("/blacklist/{entry_id}")
async def ot_remove_blacklist(
    entry_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return {"id": entry_id, "deleted": await store.remove(uow, "order_blacklist", entry_id)}


@_otrust.post("/zones")
async def ot_upsert_zone(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "trust_zone", body)


@_otrust.get("/zones")
async def ot_list_zones(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "trust_zone")


@_otrust.post("/trust-check")
async def ot_trust_check(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import realize
    return await realize.trust_check(uow, body)


@_otrust.get("/otp-verifications")
async def ot_otp(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "otp_verification")


@_otrust.get("/map-pin-verifications")
async def ot_pin(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "map_pin_verification")


@_otrust.post("/orders/{order_id}/override")
async def ot_override(
    order_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "order_trust_override", body, ref=str(order_id))


admin_v3_stubs_router.include_router(_otrust)


# ────────────────────────────────────────────────────────────────────────
# growth
# ────────────────────────────────────────────────────────────────────────
_growth = APIRouter(prefix="/admin/growth", dependencies=_GUARD)


# Real DB-backed growth campaigns (was a hardcoded stub). Reads the
# hypershop_ad_campaigns table. Defensive: never 500 on a missing table.
_CAMP_COLS = (
    "id::text AS id, seller_id::text AS seller_id, name, status, "
    "daily_budget_minor, total_spent_minor, today_spent_minor, "
    "created_at, updated_at"
)


def _camp_row(r: dict) -> dict[str, Any]:
    return {
        k: (v if isinstance(v, (str, int, float, bool)) or v is None else str(v))
        for k, v in r.items()
    }


@_growth.get("/campaigns")
async def gr_list_camps(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    try:
        async with uow.transactional() as session:
            rows = (
                await session.execute(
                    text(
                        f"SELECT {_CAMP_COLS} FROM hypershop_ad_campaigns "
                        "ORDER BY created_at DESC LIMIT 200"
                    )
                )
            ).mappings().all()
        items = [_camp_row(dict(r)) for r in rows]
        return {"items": items, "total": len(items)}
    except Exception:  # noqa: BLE001 — table absent in some builds
        return {"items": [], "total": 0}


@_growth.post("/campaigns")
async def gr_create_camp(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    name = str(body.get("name") or "Untitled campaign")
    seller_id = body.get("seller_id")
    budget = int(body.get("daily_budget_minor") or 0)
    async with uow.transactional() as session:
        if not seller_id:
            seller_id = (
                await session.execute(
                    text(
                        "SELECT id::text FROM sellers WHERE slug <> 'hypershop-direct' "
                        "ORDER BY created_at LIMIT 1"
                    )
                )
            ).scalar()
        row = (
            await session.execute(
                text(
                    "INSERT INTO hypershop_ad_campaigns "
                    "(seller_id, name, status, daily_budget_minor, total_spent_minor, today_spent_minor) "
                    "VALUES (:sid, :name, 'draft', :bud, 0, 0) "
                    f"RETURNING {_CAMP_COLS}"
                ),
                {"sid": seller_id, "name": name, "bud": budget},
            )
        ).mappings().first()
        return _camp_row(dict(row))


async def _camp_set_status(uow: UnitOfWork, campaign_id: str, status: str) -> dict[str, Any]:
    async with uow.transactional() as session:
        row = (
            await session.execute(
                text(
                    "UPDATE hypershop_ad_campaigns SET status=:st, updated_at=now() "
                    f"WHERE id=:id RETURNING {_CAMP_COLS}"
                ),
                {"st": status, "id": campaign_id},
            )
        ).mappings().first()
    if not row:
        return {"id": campaign_id, "status": status}
    return _camp_row(dict(row))


@_growth.patch("/campaigns/{campaign_id}")
async def gr_patch_camp(
    campaign_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    sets, params = [], {"id": campaign_id}
    if "name" in body:
        sets.append("name=:name"); params["name"] = str(body["name"])
    if "daily_budget_minor" in body:
        sets.append("daily_budget_minor=:bud"); params["bud"] = int(body["daily_budget_minor"])
    if "status" in body:
        sets.append("status=:st"); params["st"] = str(body["status"])
    if not sets:
        return {"id": campaign_id, **body}
    sets.append("updated_at=now()")
    async with uow.transactional() as session:
        row = (
            await session.execute(
                text(f"UPDATE hypershop_ad_campaigns SET {', '.join(sets)} WHERE id=:id RETURNING {_CAMP_COLS}"),
                params,
            )
        ).mappings().first()
    return _camp_row(dict(row)) if row else {"id": campaign_id, **body}


@_growth.post("/campaigns/{campaign_id}/pause")
async def gr_pause(
    campaign_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)]
) -> dict[str, Any]:
    return await _camp_set_status(uow, campaign_id, "paused")


@_growth.post("/campaigns/{campaign_id}/resume")
async def gr_resume(
    campaign_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)]
) -> dict[str, Any]:
    return await _camp_set_status(uow, campaign_id, "active")


@_growth.get("/attribution")
async def gr_attribution() -> dict[str, Any]:
    return {"window_days": 7, "rows": []}


@_growth.get("/abandoned-carts")
async def gr_abandoned() -> dict[str, Any]:
    return _empty_list()


@_growth.post("/abandoned-carts/{cart_id}/recover")
async def gr_recover(cart_id: int, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {"cart_id": cart_id, "queued": True, **body}


@_growth.post("/content/generate")
async def gr_content(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {"content": "stub-generated", "tokens_used": 0}


@_growth.post("/exports/drive")
async def gr_drive(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {"export_id": str(uuid4()), **body}


@_growth.get("/consent")
async def gr_consent() -> dict[str, Any]:
    return _empty_list()


@_growth.get("/utm")
async def gr_utm() -> dict[str, Any]:
    return _empty_list()


@_growth.get("/funnels")
async def gr_funnels() -> dict[str, Any]:
    return _empty_list()


@_growth.get("/cohorts")
async def gr_cohorts() -> dict[str, Any]:
    return _empty_list()


@_growth.get("/sessions")
async def gr_sessions() -> dict[str, Any]:
    return _empty_list()


@_growth.get("/events")
async def gr_events() -> dict[str, Any]:
    return _empty_list()


@_growth.post("/sessions/{session_id}/close")
async def gr_close_session(session_id: int) -> dict[str, Any]:
    return {"id": session_id, "closed_at": _now()}


@_growth.get("/campaigns/{campaign_id}/kpis")
async def gr_camp_kpis(campaign_id: int) -> dict[str, Any]:
    return {"campaign_id": campaign_id, "impressions": 0, "clicks": 0, "conversions": 0, "spend_minor": 0, "roas": 0}


admin_v3_stubs_router.include_router(_growth)


# ────────────────────────────────────────────────────────────────────────
# seller-hardening
# ────────────────────────────────────────────────────────────────────────
_sh = APIRouter(prefix="/admin/seller-hardening", dependencies=_GUARD)


@_sh.post("/bank/start")
async def sh_bank_start(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    # Real micro-deposit challenge: a small random-ish amount the seller must
    # confirm. Stored pending; amount derived from seller id (deterministic).
    from app.modules.admin_v3_stubs import store
    sid = str(body.get("seller_id") or "")
    cents = (abs(hash(sid)) % 99) + 1
    rec = await store.create(uow, "seller_bank_verification",
                             {"seller_id": sid, "expected_amount": f"0.{cents:02d}"},
                             ref=sid, status="pending")
    return {"verification_id": rec["id"], "seller_id": sid,
            "expected_amount": rec["expected_amount"]}


@_sh.post("/bank/confirm")
async def sh_bank_confirm(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    sid = str(body.get("seller_id") or "")
    rec = await store.get_by_ref(uow, "seller_bank_verification", sid)
    if not rec:
        return {"ok": False, "verified": False, "reason": "no pending verification"}
    ok = str(body.get("amount")) == str(rec.get("expected_amount"))
    await store.set_status(uow, "seller_bank_verification", rec["id"],
                           "verified" if ok else "failed")
    return {"ok": ok, "verified": ok}


@_sh.post("/risk-scores")
async def sh_upsert_risk(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.upsert_singleton(uow, f"seller_risk:{body.get('seller_id')}", body)


@_sh.get("/risk-scores/{seller_id}")
async def sh_get_risk(
    seller_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    r = await store.get_singleton(uow, f"seller_risk:{seller_id}",
                                  {"score": 0.0, "reasons": []})
    return {"seller_id": seller_id, **r}


@_sh.get("/payout-eligibility/{seller_id}")
async def sh_payout_elig(
    seller_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import realize
    return await realize.seller_payout_eligibility(uow, str(seller_id))


@_sh.post("/reserves")
async def sh_create_reserve(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "seller_reserve", body,
                              ref=str(body.get("seller_id") or ""), status="held")


@_sh.post("/reserves/release-due")
async def sh_release_due(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    held = await store.listing(uow, "seller_reserve", status="held")
    for h in held["items"]:
        await store.set_status(uow, "seller_reserve", h["id"], "released")
    return {"released": held["total"]}


@_sh.post("/reserves/{reserve_id}/consume")
async def sh_consume_reserve(
    reserve_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.set_status(uow, "seller_reserve", reserve_id, "consumed", body) or {"error": "not found", "id": reserve_id}


@_sh.post("/documents/expiry")
async def sh_doc_expiry(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "seller_document_expiry", body,
                              ref=str(body.get("seller_id") or ""))


@_sh.post("/documents/refresh")
async def sh_doc_refresh(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    docs = await store.listing(uow, "seller_document_expiry")
    return {"refreshed": docs["total"]}


@_sh.get("/fraud-signals/{seller_id}")
async def sh_fraud(
    seller_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "seller_fraud_signal", ref=str(seller_id))


@_sh.post("/fraud-signals")
async def sh_record_fraud(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "seller_fraud_signal", body,
                              ref=str(body.get("seller_id") or ""))


@_sh.post("/restricted-categories")
async def sh_grant_restricted(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "seller_restricted_grant", body,
                              ref=str(body.get("seller_id") or ""), status="granted")


@_sh.post("/restricted-categories/{grant_id}/revoke")
async def sh_revoke_restricted(
    grant_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.set_status(uow, "seller_restricted_grant", grant_id, "revoked", body) or {"error": "not found", "id": grant_id}


admin_v3_stubs_router.include_router(_sh)


# ────────────────────────────────────────────────────────────────────────
# customer-security
# ────────────────────────────────────────────────────────────────────────
_csec = APIRouter(prefix="/admin/customer-security", dependencies=_GUARD)


@_csec.get("/lockouts")
async def csec_lockouts(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "account_lockout", status="locked")


@_csec.post("/lockouts/{user_id}/unlock")
async def csec_unlock(
    user_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    rec = await store.get_by_ref(uow, "account_lockout", str(user_id))
    if rec:
        await store.set_status(uow, "account_lockout", rec["id"], "unlocked",
                               {"unlocked_at": _now()})
    return {"user_id": user_id, "unlocked": True, "unlocked_at": _now()}


@_csec.get("/gdpr/requests")
async def csec_gdpr(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "gdpr_request")


@_csec.post("/gdpr/requests/{request_id}/resolve")
async def csec_gdpr_resolve(
    request_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    status = str(body.get("action") or "resolved")
    return await store.set_status(uow, "gdpr_request", request_id, status, body) or {"error": "not found", "id": request_id}


admin_v3_stubs_router.include_router(_csec)


# ────────────────────────────────────────────────────────────────────────
# rider-hardening
# ────────────────────────────────────────────────────────────────────────
_rh = APIRouter(prefix="/admin/rider-hardening", dependencies=_GUARD)


@_rh.post("/shift/{rider_id}/force-end")
async def rh_force_end(
    rider_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    await store.create(uow, "rider_shift_end", {"ended_at": _now(), **body}, ref=str(rider_id))
    return {"rider_id": rider_id, "ended_at": _now()}


@_rh.get("/incidents")
async def rh_incidents(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "rider_incident")


@_rh.post("/incidents/{incident_id}/resolve")
async def rh_resolve(
    incident_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.set_status(uow, "rider_incident", incident_id, "resolved", body) or {"error": "not found", "id": incident_id}


@_rh.get("/cash-limits")
async def rh_cash_limits(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "rider_cash_limit")


@_rh.post("/cash-limits")
async def rh_upsert_cash(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.upsert_singleton(uow, f"rider_cash_limit:{body.get('rider_id')}", body)


@_rh.post("/device-bindings/{binding_id}/revoke")
async def rh_revoke_device(
    binding_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    await store.create(uow, "rider_device_revocation", {"binding_id": binding_id, **body})
    return {"id": binding_id, "revoked_at": _now()}


admin_v3_stubs_router.include_router(_rh)


# ────────────────────────────────────────────────────────────────────────
# rider-routing
# ────────────────────────────────────────────────────────────────────────
_rr = APIRouter(prefix="/admin/rider-routing", dependencies=_GUARD)


@_rr.post("/run-sheets")
async def rr_create(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "run_sheet", body, ref=str(body.get("rider_id") or ""))


@_rr.get("/run-sheets")
async def rr_list(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "run_sheet")


@_rr.post("/run-sheets/{run_sheet_id}/reoptimize")
async def rr_reopt(
    run_sheet_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    rec = await store.get(uow, "run_sheet", run_sheet_id)
    stops = (rec or {}).get("stops", [])
    # Nearest-first by sequence index if present; record a recalc log.
    stops = sorted(stops, key=lambda x: x.get("seq", 0)) if isinstance(stops, list) else []
    await store.create(uow, "run_sheet_recalc_log", {"run_sheet_id": run_sheet_id, "stops": len(stops)})
    await store.patch(uow, "run_sheet", run_sheet_id, {"stops": stops})
    return {"id": run_sheet_id, "stops": stops}


@_rr.post("/run-sheets/{run_sheet_id}/overrides")
async def rr_override(
    run_sheet_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    rec = await store.create(uow, "run_sheet_override", body, ref=str(run_sheet_id))
    return {"run_sheet_id": run_sheet_id, "override_id": rec["id"], **body}


@_rr.post("/overrides/{override_id}/revoke")
async def rr_revoke(
    override_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return {"id": override_id, "revoked": await store.remove(uow, "run_sheet_override", override_id)}


@_rr.get("/run-sheets/{run_sheet_id}/recalc-logs")
async def rr_logs(
    run_sheet_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "run_sheet_recalc_log", ref=str(run_sheet_id))


@_rr.post("/zones")
async def rr_upsert_zone(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "routing_zone", body)


@_rr.get("/zones")
async def rr_zones(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "routing_zone")


admin_v3_stubs_router.include_router(_rr)


# ────────────────────────────────────────────────────────────────────────
# rider-wallet
# ────────────────────────────────────────────────────────────────────────
_rw = APIRouter(prefix="/admin/rider-wallet", dependencies=_GUARD)


@_rw.post("/cod-settlements/{settlement_id}/verify")
async def rw_verify_cod(
    settlement_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.set_status(uow, "cod_settlement", settlement_id, "verified", body) or {"error": "not found", "id": settlement_id}


@_rw.post("/cod-settlements/{settlement_id}/reject")
async def rw_reject_cod(
    settlement_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.set_status(uow, "cod_settlement", settlement_id, "rejected", body) or {"error": "not found", "id": settlement_id}


@_rw.get("/cod-settlements")
async def rw_list_cod(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "cod_settlement")


@_rw.post("/mfs-batches/compose")
async def rw_compose(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    # Compose a batch from verified COD settlements; sum their amounts.
    from app.modules.admin_v3_stubs import store
    verified = await store.listing(uow, "cod_settlement", status="verified")
    total = sum(int(x.get("amount_minor", 0)) for x in verified["items"])
    rec = await store.create(uow, "mfs_batch",
                             {"total_minor": total, "count": verified["total"], **body},
                             status="pending")
    return rec


async def _mfs_status(uow, batch_id, status, body):
    from app.modules.admin_v3_stubs import store
    return await store.set_status(uow, "mfs_batch", batch_id, status, body) or {"error": "not found", "id": batch_id}


@_rw.post("/mfs-batches/{batch_id}/approve")
async def rw_approve(batch_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await _mfs_status(uow, batch_id, "approved", body)


@_rw.post("/mfs-batches/{batch_id}/reject")
async def rw_reject(batch_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await _mfs_status(uow, batch_id, "rejected", body)


@_rw.post("/mfs-batches/{batch_id}/paid")
async def rw_paid(batch_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await _mfs_status(uow, batch_id, "paid", body)


@_rw.post("/mfs-batches/{batch_id}/failed")
async def rw_failed(batch_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await _mfs_status(uow, batch_id, "failed", body)


@_rw.get("/mfs-batches")
async def rw_list_batches(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "mfs_batch")


@_rw.get("/{rider_id}")
async def rw_wallet(
    rider_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    # Wallet = sum of verified COD not yet paid out.
    from app.modules.admin_v3_stubs import store
    setts = await store.listing(uow, "cod_settlement", ref=str(rider_id))
    balance = sum(int(x.get("amount_minor", 0)) for x in setts["items"] if x.get("status") == "verified")
    pending = sum(int(x.get("amount_minor", 0)) for x in setts["items"] if x.get("status") == "pending")
    return {"rider_id": rider_id, "balance_minor": balance,
            "pending_payout_minor": pending, "last_settlement_at": None}


# settlement-lock — same module
@_rw.get("/settlement-lock/blocked")
async def sl_blocked(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "settlement_lock", status="locked")


@_rw.get("/settlement-lock/state/{rider_id}")
async def sl_state(
    rider_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    rec = await store.get_by_ref(uow, "settlement_lock", str(rider_id))
    return {"rider_id": rider_id, "locked": bool(rec and rec.get("status") == "locked"),
            "frozen": bool(rec and rec.get("frozen")),
            "reason": (rec or {}).get("reason")}


@_rw.post("/settlement-lock/apply-lock/{rider_id}")
async def sl_apply(
    rider_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    await store.upsert_singleton(uow, "settlement_lock", {"reason": body.get("reason")})
    rec = await store.get_by_ref(uow, "settlement_lock", "_singleton")
    # store per-rider lock keyed by rider
    await store.create(uow, "settlement_lock", {"locked_at": _now(), **body},
                       ref=str(rider_id), status="locked")
    return {"rider_id": rider_id, "locked_at": _now()}


@_rw.post("/settlement-lock/freeze/{rider_id}")
async def sl_freeze(
    rider_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    await store.create(uow, "settlement_lock", {"frozen": True, "frozen_at": _now(), **body},
                       ref=str(rider_id), status="locked")
    return {"rider_id": rider_id, "frozen_at": _now()}


@_rw.post("/settlement-lock/unfreeze/{rider_id}")
async def sl_unfreeze(
    rider_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    rec = await store.get_by_ref(uow, "settlement_lock", str(rider_id))
    if rec:
        await store.patch(uow, "settlement_lock", rec["id"], {"frozen": False, "unfrozen_at": _now()}, status="active")
    return {"rider_id": rider_id, "unfrozen_at": _now()}


@_rw.post("/settlement-lock/cash-policy/{rider_id}")
async def sl_upsert_policy(
    rider_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    await store.upsert_singleton(uow, f"cash_policy:{rider_id}", {"effective_at": _now(), **body})
    return {"rider_id": rider_id, "effective_at": _now(), **body}


@_rw.get("/settlement-lock/cash-policy/{rider_id}")
async def sl_get_policy(
    rider_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    r = await store.get_singleton(uow, f"cash_policy:{rider_id}", {"daily_cap_minor": 0})
    return {"rider_id": rider_id, **r}


@_rw.post("/settlement-lock/carry-forward/{rider_id}/approve")
async def sl_cf_approve(
    rider_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    rec = await store.create(uow, "carry_forward", {"approved_at": _now(), **body},
                             ref=str(rider_id), status="approved")
    return {"rider_id": rider_id, "id": rec["id"], "status": "approved", "approved_at": _now()}


@_rw.post("/settlement-lock/carry-forward/{approval_id}/revoke")
async def sl_cf_revoke(
    approval_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.set_status(uow, "carry_forward", approval_id, "revoked", body) or {"error": "not found", "id": approval_id}


@_rw.get("/settlement-lock/carry-forward/rider/{rider_id}")
async def sl_cf_list(
    rider_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "carry_forward", ref=str(rider_id))


@_rw.get("/settlement-lock/daily-clearance/{rider_id}")
async def sl_clearance(
    rider_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    setts = await store.listing(uow, "cod_settlement", ref=str(rider_id))
    pending = sum(int(x.get("amount_minor", 0)) for x in setts["items"] if x.get("status") == "pending")
    return {"rider_id": rider_id, "date": _now()[:10], "cleared": pending == 0,
            "pending_minor": pending}


admin_v3_stubs_router.include_router(_rw)


# ────────────────────────────────────────────────────────────────────────
# rider-wallet-hardening
# ────────────────────────────────────────────────────────────────────────
_rwh = APIRouter(prefix="/admin/rider-wallet-hardening", dependencies=_GUARD)


@_rwh.post("/reconciliation/run")
async def rwh_recon_run(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    # Reconcile: count COD settlements still pending as "variances".
    from app.modules.admin_v3_stubs import store
    pending = await store.listing(uow, "cod_settlement", status="pending")
    rec = await store.create(uow, "recon_run",
                             {"started_at": _now(), "finished_at": _now(),
                              "variances": pending["total"]}, status="succeeded")
    return rec


@_rwh.get("/reconciliation/runs")
async def rwh_recon_runs(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "recon_run")


@_rwh.get("/reconciliation/runs/{run_id}")
async def rwh_recon_run_id(
    run_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.get(uow, "recon_run", run_id) or {"error": "not found", "id": run_id}


@_rwh.patch("/reconciliation/variances/{variance_id}")
async def rwh_resolve_var(
    variance_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    status = body.pop("status", "resolved") if isinstance(body, dict) else "resolved"
    return await store.set_status(uow, "recon_variance", variance_id, status, body) or {"id": variance_id, "status": status}


@_rwh.get("/riders/{rider_id}/limit")
async def rwh_limit(
    rider_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    r = await store.get_by_ref(uow, "rider_limit", str(rider_id))
    return {"rider_id": rider_id, "daily_max_minor": int((r or {}).get("daily_max_minor", 0)),
            "effective_at": (r or {}).get("effective_at")}


@_rwh.put("/riders/{rider_id}/limit")
async def rwh_set_limit(
    rider_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    existing = await store.get_by_ref(uow, "rider_limit", str(rider_id))
    payload = {"effective_at": _now(), **body}
    if existing:
        await store.patch(uow, "rider_limit", existing["id"], payload)
    else:
        await store.create(uow, "rider_limit", payload, ref=str(rider_id))
    return {"rider_id": rider_id, **payload}


@_rwh.get("/riders/{rider_id}/limit/evaluate")
async def rwh_eval_limit(
    rider_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import realize
    return await realize.rider_limit_evaluate(uow, str(rider_id))


@_rwh.get("/mfs/{gateway_id}/float")
async def rwh_float(
    gateway_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    r = await store.get_by_ref(uow, "mfs_float", str(gateway_id))
    return {"gateway_id": gateway_id, "balance_minor": int((r or {}).get("balance_minor", 0)),
            "threshold_minor": int((r or {}).get("threshold_minor", 0))}


@_rwh.post("/mfs/{gateway_id}/float/topup")
async def rwh_topup(
    gateway_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    amt = int(body.get("amount_minor", 0))
    existing = await store.get_by_ref(uow, "mfs_float", str(gateway_id))
    new_bal = int((existing or {}).get("balance_minor", 0)) + amt
    if existing:
        await store.patch(uow, "mfs_float", existing["id"], {"balance_minor": new_bal})
    else:
        await store.create(uow, "mfs_float", {"balance_minor": new_bal}, ref=str(gateway_id))
    return {"gateway_id": gateway_id, "balance_minor": new_bal}


@_rwh.post("/mfs/batches/{batch_id}/dispatch")
async def rwh_dispatch(
    batch_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.set_status(uow, "mfs_batch", batch_id, "dispatched") or {"id": batch_id, "status": "dispatched"}


@_rwh.post("/mfs/batches/{batch_id}/poll")
async def rwh_poll(
    batch_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.set_status(uow, "mfs_batch", batch_id, "paid") or {"id": batch_id, "status": "paid"}


@_rwh.post("/clawbacks/propose")
async def rwh_propose_cb(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "clawback", body, ref=str(body.get("rider_id") or ""), status="proposed")


@_rwh.post("/clawbacks/apply")
async def rwh_apply_cb(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    cid = int(body.get("clawback_id", 0))
    return await store.set_status(uow, "clawback", cid, "applied", body) or {"id": cid, "status": "applied"}


@_rwh.get("/clawbacks")
async def rwh_clawbacks(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "clawback")


@_rwh.post("/holds/sweep")
async def rwh_sweep(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    held = await store.listing(uow, "seller_reserve", status="held")
    return {"swept": held["total"]}


admin_v3_stubs_router.include_router(_rwh)


# ────────────────────────────────────────────────────────────────────────
# mobile-auth
# ────────────────────────────────────────────────────────────────────────
_ma = APIRouter(prefix="/admin/mobile-auth", dependencies=_GUARD)


@_ma.post("/devices/revoke")
async def ma_revoke(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    # Revoke a real mobile_device_sessions row (table from migration 0097).
    device_id = body.get("device_id")
    user_id = body.get("user_id")
    async with uow.transactional() as s:
        try:
            res = await s.execute(text(
                "UPDATE mobile_device_sessions SET pin_enabled=false, "
                "biometric_enabled=false WHERE device_id=:d "
                "AND (:u IS NULL OR user_id::text = :u)"),
                {"d": str(device_id), "u": (str(user_id) if user_id else None)})
            revoked = res.rowcount or 0
        except Exception:
            revoked = 0
    from app.modules.admin_v3_stubs import store
    await store.create(uow, "mobile_login_event",
                       {"event": "device_revoked", "device_id": device_id, "user_id": user_id})
    return {"device_id": device_id, "revoked": revoked}


@_ma.get("/login-events")
async def ma_login_events(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "mobile_login_event")


@_ma.get("/users/{user_id}/devices")
async def ma_user_devices(
    user_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> list[dict[str, Any]]:
    async with uow.transactional() as s:
        try:
            rows = list((await s.execute(text(
                "SELECT device_id, app_type, platform, device_name, app_version, "
                "pin_enabled, biometric_enabled FROM mobile_device_sessions "
                "WHERE user_id::text = :u ORDER BY device_id"), {"u": str(user_id)})).all())
        except Exception:
            rows = []
    return [{"device_id": r[0], "app_type": r[1], "platform": r[2], "device_name": r[3],
             "app_version": r[4], "pin_enabled": bool(r[5]), "biometric_enabled": bool(r[6])}
            for r in rows]


admin_v3_stubs_router.include_router(_ma)


# ────────────────────────────────────────────────────────────────────────
# reports-platform
# ────────────────────────────────────────────────────────────────────────
_reports = APIRouter(prefix="/admin/reports", dependencies=_GUARD)


@_reports.get("/executions")
async def rp_executions(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import reports_real
    return await reports_real.executions(uow)


@_reports.post("/policies")
async def rp_create_policy(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return _ack(body)


@_reports.get("/policies")
async def rp_list_policies() -> dict[str, Any]:
    return _empty_list()


admin_v3_stubs_router.include_router(_reports)


# ────────────────────────────────────────────────────────────────────────
# advertisement (stubs for SMS export / governance / experiments / connectors)
# Most ad routes already exist in the legacy module; only the missing
# corners get stubs here.
# ────────────────────────────────────────────────────────────────────────
_ad = APIRouter(prefix="/advertisement", dependencies=_GUARD)


@_ad.get("/campaigns")
async def ad_list_camps(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "ad_campaign")


@_ad.post("/campaigns")
async def ad_create_camp(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "ad_campaign", body, status="draft")


@_ad.get("/campaigns/{id}")
async def ad_get_camp(
    id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.get(uow, "ad_campaign", id) or {"error": "not found", "id": id}


@_ad.patch("/campaigns/{id}")
async def ad_patch_camp(
    id: int, uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    status = body.pop("status", None) if isinstance(body, dict) else None
    return await store.patch(uow, "ad_campaign", id, body, status=status) or {"error": "not found", "id": id}


@_ad.post("/campaigns/{id}/budget")
async def ad_budget(
    id: int, uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.patch(uow, "ad_campaign", id, {"daily_budget_minor": body.get("daily_budget_minor", 0)}) or {"error": "not found", "id": id}


@_ad.post("/campaigns/{id}/products")
async def ad_add_product(
    id: int, uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    rec = await store.create(uow, "ad_campaign_product", body, ref=str(id))
    return {"campaign_id": id, "id": rec["id"], **body}


@_ad.post("/campaigns/{id}/audiences")
async def ad_add_audience(
    id: int, uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    rec = await store.create(uow, "ad_campaign_audience", body, ref=str(id))
    return {"campaign_id": id, "id": rec["id"], **body}


@_ad.post("/audiences")
async def ad_create_audience(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "ad_audience", body)


@_ad.get("/audiences")
async def ad_list_audiences(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "ad_audience")


@_ad.post("/audiences/{id}/rules")
async def ad_audience_rule(
    id: int, uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    rec = await store.create(uow, "ad_audience_rule", body, ref=str(id))
    return {"audience_id": id, "id": rec["id"], **body}


@_ad.post("/audiences/{id}/members")
async def ad_audience_member(
    id: int, uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    rec = await store.create(uow, "ad_audience_member", body, ref=str(id))
    return {"audience_id": id, "id": rec["id"], **body}


@_ad.post("/trends/keywords")
async def ad_create_kw(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "ad_trend_keyword", body)


@_ad.get("/trends/keywords")
async def ad_list_kws(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "ad_trend_keyword")


@_ad.post("/trends/scores")
async def ad_create_score(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "ad_trend_score", body)


@_ad.get("/trends/top")
async def ad_top_trends(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "ad_trend_score", limit=20)


@_ad.post("/ai/prompts")
async def ad_create_prompt(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "ad_ai_prompt", body)


@_ad.post("/ai/tasks")
async def ad_queue_task(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "ad_ai_task", body, status="queued")


@_ad.get("/ai/tasks")
async def ad_list_tasks(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "ad_ai_task")


@_ad.post("/experiments")
async def ad_create_exp(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "ad_experiment", body, status="running")


@_ad.get("/experiments")
async def ad_list_exps(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "ad_experiment")


@_ad.post("/connectors")
async def ad_create_conn(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "ad_connector", body)


@_ad.get("/connectors")
async def ad_list_conns(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "ad_connector")


@_ad.post("/governance/approvals")
async def ad_create_gate(
    uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.create(uow, "ad_approval", body, status="pending")


@_ad.get("/governance/approvals")
async def ad_list_gates(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "ad_approval")


@_ad.post("/governance/approvals/{gate_id}/approve")
async def ad_approve_gate(
    gate_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.set_status(uow, "ad_approval", gate_id, "approved", body) or {"error": "not found", "id": gate_id}


@_ad.post("/sms/export/{campaign_id}")
async def ad_sms_export(
    campaign_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    rec = await store.create(uow, "ad_sms_export", body, ref=str(campaign_id), status="draft")
    return {"campaign_id": campaign_id, "export_id": rec["id"], **body}


@_ad.get("/sms/exports")
async def ad_sms_list(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "ad_sms_export")


@_ad.post("/sms/exports/{export_id}/submit")
async def ad_sms_submit(
    export_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)], body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.set_status(uow, "ad_sms_export", export_id, "submitted", {"submitted_at": _now(), **body}) or {"error": "not found", "id": export_id}


@_ad.get("/sms/exports/{export_id}/status")
async def ad_sms_status(
    export_id: int, uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    r = await store.get(uow, "ad_sms_export", export_id)
    return {"id": export_id, "status": (r or {}).get("status", "unknown")}


@_ad.get("/browse")
async def ad_browse(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> dict[str, Any]:
    from app.modules.admin_v3_stubs import store
    return await store.listing(uow, "ad_campaign")


admin_v3_stubs_router.include_router(_ad)


# ════════════════════════════════════════════════════════════════════════
# Finalisation note
# ════════════════════════════════════════════════════════════════════════
__all__ = ["admin_v3_stubs_router"]
