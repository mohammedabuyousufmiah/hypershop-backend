"""ARQ jobs for the SEO module.

One job today:

- ``indexnow_ping_job`` (polls every 60s) — picks unsent IndexNow
  notifications from a small in-process queue + submits them in batches
  to ``api.indexnow.org``. IndexNow is a free protocol (Bing + Yandex +
  Naver + Seznam read it; Google ignores it but still benefits via
  Bing's index). One HTTP call per host per minute is plenty —
  publishing a new product immediately makes the URL crawlable.

Trigger path:

  - ``catalog.product.activated`` outbox event → enqueue
    ``indexnow_submit_url(/product/<slug>)``
  - ``seo.blog_post.published`` outbox event → enqueue
    ``indexnow_submit_url(/blog/<slug>)``

Both hooks call ``enqueue_indexnow_url(url)`` directly from the same
transaction that flips the status, so the URL is in the queue before
the row is visible to the public.

Configuration (``settings.seo_indexnow_*``):

  - ``seo_indexnow_enabled``       — master kill switch (default False
    until a key is provisioned)
  - ``seo_indexnow_key``           — the 8-128 hex key Bing assigns; we
    also serve ``/{key}.txt`` at the public root for verification
  - ``seo_indexnow_host``          — host the URLs belong to; usually
    derived from ``seo_site_url`` so no separate setting needed

Soft-fail everywhere — IndexNow being down must NEVER break a
publish flow.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

_log = get_logger("hypershop.seo.indexnow")

# In-process FIFO of (host, url) tuples. The cron job drains it every
# 60s; a process restart drops anything pending — fine for an SEO
# nice-to-have where the next nightly sitemap ping covers the gap.
_QUEUE: deque[str] = deque()
_QUEUE_MAX = 5000

# Observability state (task #168, 2026-05-25). Module-level so the
# admin status endpoint can read without going through Redis. Lost on
# process restart — acceptable for an SEO ops dial; the next publish
# rebuilds it.
_METRICS: dict[str, Any] = {
    "last_enqueue_at": None,        # ISO-8601 UTC of last enqueue_indexnow_url call
    "last_enqueue_url": None,       # URL most recently enqueued
    "enqueue_total": 0,             # cumulative since process start
    "drop_total": 0,                # bumped when queue cap hits
    "last_ping_at": None,           # ISO-8601 UTC of last drain attempt
    "last_ping_status": None,       # int HTTP status from IndexNow (or 0 on exc)
    "last_ping_url_count": 0,       # URLs sent in last successful POST
    "last_ping_error": None,        # short string of last exception
    "ping_total": 0,                # cumulative drains attempted
    "ping_success_total": 0,        # 2xx responses
}


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def get_indexnow_metrics() -> dict[str, Any]:
    """Snapshot of IndexNow runtime metrics for the admin status panel.

    Includes the live queue depth + cap so ops can see backlog before it
    triggers drops. ``lag_seconds`` is computed from the gap between the
    last enqueue and the last successful ping — if a publish landed but
    no ping has fired since, this surfaces the cron lag.
    """
    from datetime import datetime, timezone

    out = dict(_METRICS)
    out["queue_depth"] = len(_QUEUE)
    out["queue_cap"] = _QUEUE_MAX
    out["queue_fill_pct"] = round(100.0 * len(_QUEUE) / _QUEUE_MAX, 2)

    lag = None
    if _METRICS["last_enqueue_at"] and _METRICS["last_ping_at"]:
        try:
            enq = datetime.fromisoformat(_METRICS["last_enqueue_at"])
            png = datetime.fromisoformat(_METRICS["last_ping_at"])
            if enq > png:
                lag = (datetime.now(timezone.utc) - enq).total_seconds()
            else:
                lag = 0.0
        except Exception:  # noqa: BLE001
            lag = None
    elif _METRICS["last_enqueue_at"] and not _METRICS["last_ping_at"]:
        try:
            enq = datetime.fromisoformat(_METRICS["last_enqueue_at"])
            lag = (datetime.now(timezone.utc) - enq).total_seconds()
        except Exception:  # noqa: BLE001
            lag = None
    out["lag_seconds"] = lag
    return out


def enqueue_indexnow_url(url: str) -> None:
    """Add a URL to the IndexNow submission queue.

    Called from publish hooks (catalog activate, blog publish, etc.).
    No-ops when the queue is at capacity — better to drop the newest
    submission than to grow memory unbounded.
    """
    if not url or not url.startswith(("http://", "https://")):
        return
    if len(_QUEUE) >= _QUEUE_MAX:
        _METRICS["drop_total"] += 1
        return
    _QUEUE.append(url)
    _METRICS["last_enqueue_at"] = _now_iso()
    _METRICS["last_enqueue_url"] = url
    _METRICS["enqueue_total"] += 1


def _drain_queue(max_urls: int = 100) -> list[str]:
    """Pop up to ``max_urls`` from the queue. Deduplicates on the way
    out so a chatty publisher doesn't waste IndexNow's per-key budget.
    """
    seen: set[str] = set()
    out: list[str] = []
    while _QUEUE and len(out) < max_urls:
        url = _QUEUE.popleft()
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


async def indexnow_ping_job(ctx: dict[str, Any]) -> None:
    """ARQ cron — drains the queue + posts to IndexNow.

    Schedule: every 60s (configured in the ARQ worker settings module).
    """
    s = get_settings()
    if not getattr(s, "seo_indexnow_enabled", False):
        return
    key = (getattr(s, "seo_indexnow_key", "") or "").strip()
    if not key:
        return

    urls = _drain_queue()
    if not urls:
        return

    # Derive the host the URLs belong to from the configured public
    # site URL. IndexNow rejects submissions whose host doesn't match.
    site_url = (getattr(s, "seo_site_url", "") or "").rstrip("/")
    if not site_url.startswith(("http://", "https://")):
        return
    host = site_url.split("://", 1)[1].split("/", 1)[0]

    # IndexNow accepts up to 10_000 URLs per POST per host, but we cap
    # at 100 per tick so the in-process job stays sub-second even when
    # the catalog flushes a backlog.
    payload = {
        "host": host,
        "key": key,
        "keyLocation": f"{site_url}/{key}.txt",
        "urlList": urls,
    }
    _METRICS["ping_total"] += 1
    _METRICS["last_ping_at"] = _now_iso()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                "https://api.indexnow.org/IndexNow",
                json=payload,
                headers={"content-type": "application/json"},
            )
        _METRICS["last_ping_status"] = res.status_code
        _METRICS["last_ping_error"] = None
        # 200 / 202 = accepted; 400 = invalid payload; 403 = key not
        # validated yet (operator needs to hit /{key}.txt at least once
        # from the IndexNow side). All non-fatal — log + move on.
        if res.status_code >= 400:
            _log.warning(
                "indexnow.submit_failed status=%s body=%s urls=%d",
                res.status_code,
                res.text[:200],
                len(urls),
            )
        else:
            _METRICS["ping_success_total"] += 1
            _METRICS["last_ping_url_count"] = len(urls)
            _log.info(
                "indexnow.submitted status=%s urls=%d",
                res.status_code,
                len(urls),
            )
    except Exception as exc:  # noqa: BLE001
        # Never let an IndexNow blip take the worker down. Re-enqueue
        # the URLs so the next tick retries — bounded by the queue cap.
        _METRICS["last_ping_status"] = 0
        _METRICS["last_ping_error"] = str(exc)[:200]
        for u in urls:
            enqueue_indexnow_url(u)
        _log.warning("indexnow.exception err=%s urls=%d", exc, len(urls))


# Hooks for the publish flows — import + call from the service layer
# right after the status flip (still inside the same DB transaction so
# the queue + DB stay in lockstep).

def enqueue_product_url(slug: str) -> None:
    """Hook from catalog.product.activate."""
    s = get_settings()
    site = (getattr(s, "seo_site_url", "") or "").rstrip("/")
    if not site or not slug:
        return
    enqueue_indexnow_url(f"{site}/product/{slug}")


def enqueue_blog_url(slug: str) -> None:
    """Hook from seo.blog_post.publish."""
    s = get_settings()
    site = (getattr(s, "seo_site_url", "") or "").rstrip("/")
    if not site or not slug:
        return
    enqueue_indexnow_url(f"{site}/blog/{slug}")


def enqueue_category_url(slug: str) -> None:
    """Hook from catalog.category create/update."""
    s = get_settings()
    site = (getattr(s, "seo_site_url", "") or "").rstrip("/")
    if not site or not slug:
        return
    enqueue_indexnow_url(f"{site}/c/{slug}")


async def indexnow_bulk_publish_job(ctx: dict[str, Any]) -> None:
    """ARQ job — flood the IndexNow queue with every live URL.

    Schedule: weekly Sunday 04:00 UTC (configure in worker settings).

    The per-publish hooks (catalog activate, blog publish) handle the
    everyday case where a single URL needs immediate crawl. This job
    fills the gap for:

    - bulk catalog imports that activate hundreds of SKUs in a single
      transaction (each fires enqueue_product_url, but a backlog can
      drop entries when the queue hits its 5000 cap)
    - URLs that existed before IndexNow was enabled (everything in the
      sitemap pre-rollout has no submission history)
    - the weekly "remind the crawler we're still here" signal Bing
      explicitly recommends for sites with daily content churn

    Pulls every active product / category / brand slug from the catalog,
    every published blog slug from the seo module, and enqueues each
    via the same per-URL hook so the 60s ping job drains them in
    batches of 100. Bing's per-key per-host rate limit is 10,000 URLs
    per day — well above our likely throughput.
    """
    s = get_settings()
    if not getattr(s, "seo_indexnow_enabled", False):
        return
    site = (getattr(s, "seo_site_url", "") or "").rstrip("/")
    if not site or site.endswith(".example"):
        return

    # Lazy-imported so the unit-test rig doesn't need to stub the full
    # catalog/seo orm at import time of jobs.py.
    from sqlalchemy import select
    from app.core.db.session import get_sessionmaker
    from app.modules.catalog.models import Brand, Category, Product
    from app.modules.seo.models import BlogPost
    from app.modules.seo.state import BlogPostStatus

    Session = get_sessionmaker()
    enqueued = 0
    async with Session() as session:
        # Products — active only, capped at 5000 per run to stay under
        # Bing's daily budget when paired with the per-publish hooks.
        product_slugs = (
            await session.execute(
                select(Product.slug)
                .where(Product.status == "active")
                .order_by(Product.updated_at.desc())
                .limit(5000),
            )
        ).scalars().all()
        for slug in product_slugs:
            enqueue_product_url(slug)
            enqueued += 1

        # Categories — active only.
        cat_slugs = (
            await session.execute(
                select(Category.slug).where(Category.is_active.is_(True)),
            )
        ).scalars().all()
        for slug in cat_slugs:
            enqueue_category_url(slug)
            enqueued += 1

        # Brands — active only.
        brand_slugs = (
            await session.execute(
                select(Brand.slug).where(Brand.is_active.is_(True)),
            )
        ).scalars().all()
        for slug in brand_slugs:
            enqueue_indexnow_url(f"{site}/brand/{slug}")
            enqueued += 1

        # Blog — published only.
        blog_slugs = (
            await session.execute(
                select(BlogPost.slug).where(
                    BlogPost.status == BlogPostStatus.PUBLISHED.value,
                ),
            )
        ).scalars().all()
        for slug in blog_slugs:
            enqueue_blog_url(slug)
            enqueued += 1

    _log.info("indexnow_bulk_publish.enqueued count=%d", enqueued)


async def sitemap_submit_job(ctx: dict[str, Any]) -> None:
    """ARQ daily cron — pings the search-engine sitemap-submit URLs.

    Schedule: daily at 03:30 UTC (configure in worker settings).

    Targets:

    - **Bing** ``https://www.bing.com/ping?sitemap=...`` — still works
      + still recommended by Bing Webmaster Tools.
    - **Google** ``https://www.google.com/ping?sitemap=...`` — Google
      deprecated this in June 2023, but the endpoint still returns
      200 and some operators report it nudges crawl scheduling on
      sites without GSC verification. Cheap insurance.
    - **Yandex** ``https://webmaster.yandex.com/ping?sitemap=...`` —
      Yandex still honours sitemap pings.

    All three soft-fail independently; one outage doesn't block the
    others. The job is idempotent — re-running mid-day is a no-op
    as far as the search engines are concerned.
    """
    s = get_settings()
    site = (getattr(s, "seo_site_url", "") or "").rstrip("/")
    if not site or not site.startswith("https://"):
        # Refuse to ping the placeholder hostname — engines blacklist
        # repeat 404 referrers, and shipping ``hypershop.example`` is
        # an embarrassment we don't want repeated.
        if site and not site.endswith(".example"):
            return
        if site.endswith(".example"):
            _log.info("sitemap_submit.skipped reason=placeholder_host")
            return
        return
    sitemap_url = f"{site}/sitemap.xml"

    targets = [
        ("bing", f"https://www.bing.com/ping?sitemap={sitemap_url}"),
        ("google", f"https://www.google.com/ping?sitemap={sitemap_url}"),
        ("yandex", f"https://webmaster.yandex.com/ping?sitemap={sitemap_url}"),
    ]

    async with httpx.AsyncClient(timeout=15.0) as client:
        for name, url in targets:
            try:
                res = await client.get(url, follow_redirects=True)
                _log.info(
                    "sitemap_submit.ping engine=%s status=%s",
                    name,
                    res.status_code,
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "sitemap_submit.exception engine=%s err=%s",
                    name,
                    exc,
                )


# Re-exported so ``asyncio.run(indexnow_ping_job({}))`` works from a
# manual smoke-test script.
__all__ = [
    "enqueue_indexnow_url",
    "enqueue_product_url",
    "enqueue_blog_url",
    "enqueue_category_url",
    "indexnow_ping_job",
    "indexnow_bulk_publish_job",
    "sitemap_submit_job",
]
