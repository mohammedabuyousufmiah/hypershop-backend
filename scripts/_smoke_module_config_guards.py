"""Verify the 5 module-config import safety guards one by one."""
from __future__ import annotations

import asyncio
import json
import sys
import urllib.error
import urllib.request

from sqlalchemy import text

from app.core.db.session import get_sessionmaker


BASE = "http://127.0.0.1:8000/api/v1"


def req(method: str, path: str, headers: dict | None = None, body: object = None):
    headers = dict(headers or {})
    raw = None
    if body is not None:
        raw = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, headers=headers, data=raw, method=method)
    try:
        resp = urllib.request.urlopen(r)
        return resp.getcode(), json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


async def main() -> int:
    sm = get_sessionmaker()

    _, d = req("POST", "/auth/login", body={"email": "admin@hypershop.dev", "password": "adminlocal12"})
    T_admin = d["data"]["tokens"]["access_token"]
    H_admin = {"Authorization": "Bearer " + T_admin}

    _, d = req("POST", "/auth/login", body={"email": "audit@hypershop.dev", "password": "supervisorlocal12"})
    T_sup = d["data"]["tokens"]["access_token"]
    H_sup = {"Authorization": "Bearer " + T_sup}

    # ── 1. Redacted secret skip ──────────────────────────────────────
    print("=== 1. Redacted secret skip ===")
    req("PUT", "/admin/modules/voice-calls/settings/sbc_shared_secret", headers=H_admin,
        body={"value": "real-secret-value", "value_type": "string", "is_secret": True})
    _, exp = req("GET", "/admin/modules/_export", headers=H_admin)
    exp = exp["data"]
    secret_row = next(s for s in exp["settings"]
                      if s["module_key"] == "voice-calls" and s["setting_key"] == "sbc_shared_secret")
    print(f"  exported secret row value: {secret_row['value']!r} (expected '[secret]')")
    _, r = req("POST", "/admin/modules/_import", headers=H_admin,
               body={"settings": exp["settings"], "flags": exp["flags"]})
    r = r["data"]
    print(f"  import result: skipped_secret_placeholders={r['skipped_secret_placeholders']} (expected >= 1)")
    async with sm() as s:
        row = (await s.execute(text(
            "SELECT value FROM module_settings "
            "WHERE module_key='voice-calls' AND setting_key='sbc_shared_secret'"
        ))).scalar_one()
    print(f"  DB still has real value: {row!r}")
    assert row == "real-secret-value", "SECRET CLOBBERED"
    print("  PASS")

    # ── 2. Import is upsert-only ─────────────────────────────────────
    print()
    print("=== 2. Import is upsert-only (missing rows NOT deleted) ===")
    req("PUT", "/admin/modules/control-mod/settings/keep_me", headers=H_admin,
        body={"value": "still here", "value_type": "string"})
    req("POST", "/admin/modules/_import", headers=H_admin,
        body={"settings": [{"module_key": "other", "setting_key": "something", "value": 1, "value_type": "number"}], "flags": []})
    async with sm() as s:
        n = (await s.execute(text(
            "SELECT count(*) FROM module_settings "
            "WHERE module_key='control-mod' AND setting_key='keep_me'"
        ))).scalar_one()
    print(f"  control-mod.keep_me count after import: {n} (expected 1)")
    assert n == 1, "UPSERT-ONLY VIOLATED"
    print("  PASS")
    req("DELETE", "/admin/modules/control-mod/settings/keep_me", headers=H_admin)
    req("DELETE", "/admin/modules/other/settings/something", headers=H_admin)

    # ── 3. reveal_secrets gate ───────────────────────────────────────
    print()
    print("=== 3. reveal_secrets=true requires module.config.write ===")
    code, _ = req("GET", "/admin/modules/_export?reveal_secrets=1", headers=H_sup)
    print(f"  supervisor reveal=1 -> HTTP {code} (expected 403)")
    assert code == 403
    code, _ = req("GET", "/admin/modules/_export", headers=H_sup)
    print(f"  supervisor default -> HTTP {code} (expected 200)")
    assert code == 200
    code, d = req("GET", "/admin/modules/_export?reveal_secrets=1", headers=H_admin)
    secret = next(s for s in d["data"]["settings"] if s["setting_key"] == "sbc_shared_secret")
    print(f"  admin reveal=1 -> HTTP {code}, secret value: {secret['value']!r}")
    print("  PASS")

    # ── 4. Audit log row created ────────────────────────────────────
    print()
    print("=== 4. audit_logs row for bulk_import ===")
    async with sm() as s:
        before = (await s.execute(text(
            "SELECT count(*) FROM audit_logs WHERE action='module.config.bulk_import'"
        ))).scalar_one()
    req("POST", "/admin/modules/_import", headers=H_admin,
        body={"settings": [{"module_key": "audit-test", "setting_key": "k", "value": 1, "value_type": "number"}], "flags": []})
    async with sm() as s:
        after = (await s.execute(text(
            "SELECT count(*) FROM audit_logs WHERE action='module.config.bulk_import'"
        ))).scalar_one()
        latest = (await s.execute(text(
            "SELECT actor_id::text, metadata FROM audit_logs "
            "WHERE action='module.config.bulk_import' ORDER BY occurred_at DESC LIMIT 1"
        ))).first()
    delta = after - before
    print(f"  audit_logs delta: {delta} (expected >= 1)")
    print(f"  latest: actor={latest[0][:8]}... metadata={latest[1]}")
    assert delta >= 1, "NO AUDIT ROW"
    print("  PASS")
    req("DELETE", "/admin/modules/audit-test/settings/k", headers=H_admin)

    # ── 5. ONE outbox event per bulk import ─────────────────────────
    print()
    print("=== 5. ONE outbox event per bulk import (not N) ===")
    async with sm() as s:
        before = (await s.execute(text(
            "SELECT count(*) FROM outbox_messages WHERE type='module.config.changed'"
        ))).scalar_one()
    req("POST", "/admin/modules/_import", headers=H_admin, body={
        "settings": [{"module_key": "bulk-mod", "setting_key": f"k{i}", "value": i, "value_type": "number"} for i in range(5)],
        "flags": [{"module_key": "bulk-mod", "flag_key": f"f{i}", "enabled": True, "rollout_percent": 100} for i in range(3)],
    })
    async with sm() as s:
        after = (await s.execute(text(
            "SELECT count(*) FROM outbox_messages WHERE type='module.config.changed'"
        ))).scalar_one()
        latest = (await s.execute(text(
            "SELECT payload FROM outbox_messages "
            "WHERE type='module.config.changed' ORDER BY available_at DESC LIMIT 1"
        ))).scalar_one()
    delta = after - before
    print(f"  outbox events delta: {delta} (expected 1 for 8 rows)")
    print(f"  payload: {latest}")
    assert delta == 1, f"EXPECTED 1 outbox event, got {delta}"
    print("  PASS")
    for i in range(5):
        req("DELETE", f"/admin/modules/bulk-mod/settings/k{i}", headers=H_admin)
    for i in range(3):
        req("DELETE", f"/admin/modules/bulk-mod/flags/f{i}", headers=H_admin)

    print()
    print("=== ALL 5 GUARDS VERIFIED ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
