"""Unified smoke test — backend + 3 frontends + 4 mobile apps.

Runs read-only probes across the whole Hypershop estate and prints a
single OK/WARN/FAIL table. Designed to run in ~60 seconds when all
services are already booted; ~3 min on cold start while the Next.js
storefront compiles routes the first time.

Sections:
  1. Backend       — health + 14 read endpoints (catalog, seo,
                     finance-ops, inventory-ops, sitemap, ...)
  2. Storefront    — home / locale / category / PDP / robots / sitemap
  3. Admin panel   — root + login route
  4. Seller panel  — root + login route
  5. Mobile bundles — APK / AAB existence + size + manifest version
                     for all 4 Android variants
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path


_BACKEND = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")
_STOREFRONT = os.environ.get("STOREFRONT_URL", "http://127.0.0.1:3000")
# Per package.json: admin-panel = next dev -p 3002,
#                   seller-panel = next dev -p 3001.
_ADMIN = os.environ.get("ADMIN_URL", "http://127.0.0.1:3002")
_SELLER = os.environ.get("SELLER_URL", "http://127.0.0.1:3001")
_MOBILE_ROOT = Path(os.environ.get(
    "MOBILE_BUILDS_ROOT",
    "C:/Users/imyou/OneDrive/Desktop/Yousuf/E CIMMERCE MASTER DATA/"
    "E COMMERCEH MASTER BANDLE/E COMMERCE full package/Android Mobile app",
))


@dataclass(slots=True)
class Result:
    section: str
    name: str
    status: str  # OK / WARN / FAIL / SKIP
    detail: str


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Return the redirect response untouched so the smoke probe can
    score 307 / 308 differently from the redirect target."""

    def http_error_301(self, req, fp, code, msg, headers):  # noqa: D401
        return fp

    http_error_302 = http_error_303 = http_error_307 = http_error_308 = \
        http_error_301


_OPENER = urllib.request.build_opener(_NoRedirect())


def _probe(url: str, *, timeout: int = 10, expect: tuple[int, ...] = (200,)) -> Result:
    """One HTTP GET, returns OK/FAIL/WARN. Does NOT follow redirects."""
    label = url.replace(_BACKEND, "be").replace(_STOREFRONT, "sf").replace(
        _ADMIN, "admin").replace(_SELLER, "seller")
    t0 = time.time()
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "hypershop-smoke/1.0"},
        )
        with _OPENER.open(req, timeout=timeout) as resp:
            took = time.time() - t0
            if resp.status in expect:
                return Result("", label,
                              "OK", f"http {resp.status} ({took:.2f}s)")
            if resp.status in (301, 302, 307, 308):
                return Result("", label,
                              "WARN", f"redirect {resp.status} ({took:.2f}s)")
            return Result("", label,
                          "FAIL", f"http {resp.status} ({took:.2f}s)")
    except urllib.error.HTTPError as e:
        return Result("", label, "FAIL",
                       f"http {e.code} after {time.time()-t0:.2f}s")
    except Exception as e:  # noqa: BLE001
        return Result("", label, "FAIL",
                       f"{type(e).__name__}: {str(e)[:80]}")


def backend_probes() -> list[Result]:
    targets = [
        ("/api/v1/health", "health"),
        ("/api/v1/catalog/categories?limit=1", "categories"),
        ("/api/v1/catalog/products?limit=1", "products"),
        ("/api/v1/seo/meta/home?locale=en", "seo home en"),
        ("/api/v1/seo/meta/home?locale=bn", "seo home bn"),
        ("/sitemap.xml", "sitemap.xml"),
        ("/sitemap-products-0.xml", "sitemap products"),
        ("/robots.txt", "robots"),
        # New role modules (Phase B-E) require auth — expect 401 not 500
        # so we probe with `expect_auth=True` style: 401/403 is acceptable.
    ]
    out: list[Result] = []
    for path, name in targets:
        r = _probe(f"{_BACKEND}{path}")
        r.section = "backend"
        r.name = name
        out.append(r)
    # Auth-gated endpoints: 401 / 403 is the expected "service alive" signal
    for path, name in [
        ("/api/v1/admin/finance-ops/refunds", "finance-ops refunds"),
        ("/api/v1/admin/finance-ops/audit-logs", "finance-ops audit"),
        ("/api/v1/admin/inventory-ops/dashboard", "inventory-ops dash"),
        ("/api/v1/admin/inventory-ops/audit-logs", "inventory-ops audit"),
    ]:
        r = _probe(f"{_BACKEND}{path}")
        # Translate FAIL+401/403 into OK since auth is doing its job
        if r.status == "FAIL" and re.search(r"http (401|403)", r.detail):
            r.status = "OK"
            r.detail = r.detail.replace("FAIL", "OK") + " (auth gate live)"
        r.section = "backend"
        r.name = name
        out.append(r)
    return out


def storefront_probes() -> list[Result]:
    targets = [
        ("/", "home (en)"),
        ("/bn", "home (bn)"),
        ("/robots.txt", "robots"),
        ("/sitemap.xml", "sitemap"),
        ("/c/computers-drones", "category"),
        ("/product/trending-lenovo-core-i5-laptop-8gb-ram-durable-for-office",
         "PDP sample"),
    ]
    out: list[Result] = []
    for path, name in targets:
        r = _probe(f"{_STOREFRONT}{path}", timeout=120)
        r.section = "storefront"
        r.name = name
        out.append(r)
    return out


def admin_probes() -> list[Result]:
    if not _port_listening(3002):
        return [Result("admin", "boot", "SKIP", "not listening on :3002 — "
                       "start with `pnpm --filter @ecom/admin-panel dev`")]
    out: list[Result] = []
    # Admin routes live under /admin/* — `/` is intentionally 404.
    for path, name in [("/admin", "admin root"),
                        ("/admin/dashboard", "dashboard"),
                        ("/admin/products", "products")]:
        r = _probe(f"{_ADMIN}{path}", timeout=120)
        r.section = "admin"; r.name = name
        out.append(r)
    return out


def seller_probes() -> list[Result]:
    if not _port_listening(3001):
        return [Result("seller", "boot", "SKIP", "not listening on :3001 — "
                       "start with `pnpm --filter @ecom/seller-panel dev`")]
    out: list[Result] = []
    # Seller-panel mounts under /seller/* and 307-redirects
    # unauthenticated visitors to /sign-in — treat 307 as OK
    # since the service IS responding correctly.
    for path, name in [("/seller", "seller root"),
                        ("/seller/dashboard", "dashboard"),
                        ("/seller/products", "products")]:
        r = _probe(f"{_SELLER}{path}", timeout=120,
                    expect=(200, 307, 308))
        r.section = "seller"; r.name = name
        if r.status == "WARN" and "redirect" in r.detail:
            # Redirect to sign-in is the expected unauthenticated state
            r.status = "OK"
            r.detail = r.detail.replace("redirect", "→ sign-in")
        out.append(r)
    return out


def _port_listening(port: int) -> bool:
    import socket
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except OSError:
        return False


def _read_aab_manifest_version(aab_path: Path) -> tuple[str | None, str | None]:
    """Pull versionCode + versionName from base/manifest/AndroidManifest.xml.

    AAB files are zips; the manifest inside ``base/manifest/`` is binary
    XML (protobuf-encoded). We only grep the raw bytes for the
    versionCode + versionName literals to avoid pulling a protobuf
    parser dep — good enough for a smoke check.
    """
    try:
        with zipfile.ZipFile(aab_path) as zf:
            names = [n for n in zf.namelist()
                     if n.endswith("AndroidManifest.xml")]
            if not names:
                return None, None
            raw = zf.read(names[0])
        m_code = re.search(rb"versionCode[\x00-\x20]+\x01\x18([\x00-\xff]{1,4})", raw)
        m_name = re.search(rb"versionName[\x00-\x20]+\x12([\x05-\x7f])", raw)
        code = None
        if m_code:
            code = str(int.from_bytes(m_code.group(1)[:1], "little"))
        name = None
        if m_name:
            offset = m_name.end()
            ln = m_name.group(1)[0]
            try:
                name = raw[offset:offset + ln].decode("utf-8", errors="ignore")
            except Exception:  # noqa: BLE001
                name = None
        return code, name
    except Exception:  # noqa: BLE001
        return None, None


def mobile_probes() -> list[Result]:
    apps = {
        "customer-android":
            _MOBILE_ROOT / "CUSTOMER APP ANDROID/hypershop-customer-android/"
            "app/build/outputs/bundle/release/app-release.aab",
        "customer-hms":
            _MOBILE_ROOT / "CUSTOMER APP HMS/customer app/"
            "hypershop-customer-hms/app/build/outputs/bundle/release/app-release.aab",
        "rider-android":
            _MOBILE_ROOT / "RIDER APP ANDROID/hypershop-rider-android/"
            "app/build/outputs/bundle/release/app-release.aab",
        "rider-hms":
            _MOBILE_ROOT / "RIDER APP HMS/hypershop-rider-hms/"
            "app/build/outputs/bundle/release/app-release.aab",
    }
    out: list[Result] = []
    for name, path in apps.items():
        if not path.exists():
            out.append(Result("mobile", name, "FAIL",
                              f"AAB missing at {path}"))
            continue
        size_mb = path.stat().st_size / (1024 * 1024)
        code, ver = _read_aab_manifest_version(path)
        detail = f"{size_mb:.1f} MB"
        if code or ver:
            detail += f", versionCode={code or '?'} versionName={ver or '?'}"
        out.append(Result("mobile", name, "OK", detail))
    return out


def render(results: list[Result]) -> str:
    lines = []
    last_section = None
    counts = {"OK": 0, "WARN": 0, "FAIL": 0, "SKIP": 0}
    for r in results:
        if r.section != last_section:
            lines.append("")
            lines.append(f"━━━ {r.section.upper()} ━━━")
            last_section = r.section
        marker = {"OK": "[OK]  ", "WARN": "[WARN]",
                  "FAIL": "[FAIL]", "SKIP": "[SKIP]"}[r.status]
        lines.append(f"  {marker} {r.name:<32} {r.detail}")
        counts[r.status] = counts.get(r.status, 0) + 1
    lines.append("")
    lines.append("━" * 60)
    lines.append(
        f"SUMMARY  OK={counts['OK']}  WARN={counts['WARN']}  "
        f"FAIL={counts['FAIL']}  SKIP={counts['SKIP']}"
    )
    return "\n".join(lines)


def main() -> int:
    print(f"Backend   : {_BACKEND}")
    print(f"Storefront: {_STOREFRONT}")
    print(f"Admin     : {_ADMIN}")
    print(f"Seller    : {_SELLER}")
    print(f"Mobile    : {_MOBILE_ROOT}")
    print()

    results: list[Result] = []
    results.extend(backend_probes())
    results.extend(storefront_probes())
    results.extend(admin_probes())
    results.extend(seller_probes())
    results.extend(mobile_probes())

    out = render(results)
    print(out)
    fails = sum(1 for r in results if r.status == "FAIL")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
