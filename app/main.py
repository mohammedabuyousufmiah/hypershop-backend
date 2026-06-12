from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.cache import close_redis, get_redis
from app.core.config import get_settings
from app.core.db.session import close_engine, get_engine
from app.core.metrics import install_metrics_route
from app.core.queue import close_arq_pool
from app.core.tracing import (
    init_tracing,
    instrument_all_libraries,
    instrument_fastapi,
    instrument_sqlalchemy,
)
from app.core.exception_handlers import install_exception_handlers
from app.core.health.api import router as health_router
from app.core.logging import configure_logging, get_logger
from app.core.middleware import (
    AccessLogMiddleware,
    PrometheusMetricsMiddleware,
    RequestIdMiddleware,
    ResponseEnvelopeMiddleware,
    SecurityHeadersMiddleware,
    SelfHealMiddleware,
)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    log = get_logger("hypershop.app")
    log.info(
        "startup",
        environment=settings.environment,
        service=settings.service_name,
        api_prefix=settings.api_prefix,
    )

    engine = get_engine()
    redis = get_redis()
    try:
        await redis.ping()
    except Exception as e:
        log.warning("redis_ping_failed_at_startup", error=str(e))

    # Bind external provider chains. The factories construct adapters
    # from env vars; if any required key is missing the factory returns
    # the NotConfigured default and the relevant endpoints return 502.
    # No external HTTP calls happen here — adapters connect lazily on
    # the first capability call.
    try:
        from app.modules.ai.providers import bind_from_settings as bind_ai
        from app.modules.iam.transport.sms_factory import (
            bind_from_settings as bind_sms,
        )
        from app.modules.invoice_dispatch.transport.whatsapp_factory import (
            bind_from_settings as bind_whatsapp,
        )
        from app.modules.payments.providers import (
            bind_from_settings as bind_payments,
        )
        from app.modules.push.transport.push_factory import (
            bind_from_settings as bind_push,
        )
        from app.modules.search.providers import (
            bind_from_settings as bind_search_reranker,
        )
        ai_provider = bind_ai()
        payments_summary = bind_payments()
        sms_transport = bind_sms()
        whatsapp_transport = bind_whatsapp()
        push_summary = bind_push()
        search_reranker = bind_search_reranker()
        log.info(
            "providers_bound",
            ai_provider=ai_provider.name,
            payment_providers=payments_summary,
            sms_transport=sms_transport.name,
            whatsapp_transport=whatsapp_transport.name,
            push_transports=push_summary,
            search_reranker=search_reranker.name,
        )
    except Exception as e:
        # Provider binding errors must not prevent app startup —
        # we'd rather have the app up with NotConfigured providers
        # (502 on AI calls) than refuse to boot.
        log.warning("provider_binding_failed", error=str(e))

    # Seed default report definitions + role policies for Module 30.
    # Idempotent: re-runs are no-ops, safe on every boot. Failure is
    # non-fatal — the app still starts; reporting endpoints will just
    # return empty lists until the seeder succeeds on a later boot.
    try:
        from app.modules.reporting.bootstrap import seed_default_reports
        counts = await seed_default_reports()
        log.info("reporting_bootstrap", **counts)
    except Exception as e:
        log.warning("reporting_bootstrap_failed", error=str(e))

    # Seed default supplier-payment approval workflows (Module 33).
    # Idempotent — repo upserts on workflow_code.
    try:
        from app.modules.supplier_payments.workflow_seed import (
            seed_default_workflows,
        )
        counts = await seed_default_workflows()
        log.info("supplier_payments_workflow_seed", **counts)
    except Exception as e:
        log.warning("supplier_payments_workflow_seed_failed", error=str(e))

    # Start the voice-call SSE Redis pubsub bridge so multi-pod
    # deployments fan voice.call.* events across replicas. No-op if
    # REDIS_URL is unreachable (single-pod dev keeps working).
    try:
        from app.modules.customer_care import sse_redis_bridge
        sse_redis_bridge.start_listener()
        log.info("voice_event_redis_listener_started", pod_id=sse_redis_bridge.POD_ID)
    except Exception as e:  # noqa: BLE001
        log.warning("voice_event_redis_listener_start_failed", error=str(e))

    # Same pattern for module.config.changed — cross-pod fan-out of
    # admin-config flips so all admin tabs (across replicas) auto-refresh.
    try:
        from app.modules.admin_config import sse_redis_bridge as _cfg_bridge
        _cfg_bridge.start_listener()
        log.info("module_config_redis_listener_started", pod_id=_cfg_bridge.POD_ID)
    except Exception as e:  # noqa: BLE001
        log.warning("module_config_redis_listener_start_failed", error=str(e))

    try:
        yield
    finally:
        log.info("shutdown")
        try:
            from app.modules.customer_care import sse_redis_bridge
            await sse_redis_bridge.stop_listener()
        except Exception as e:  # noqa: BLE001
            log.warning("voice_event_redis_listener_stop_failed", error=str(e))
        try:
            from app.modules.admin_config import sse_redis_bridge as _cfg_bridge
            await _cfg_bridge.stop_listener()
        except Exception as e:  # noqa: BLE001
            log.warning("module_config_redis_listener_stop_failed", error=str(e))
        await close_engine()
        await close_redis()
        await close_arq_pool()
        del engine


def _mount_customer_care_pwa(app: FastAPI) -> None:
    """Mount the customer-care agent dashboard PWA at /customercare/.

    The PWA source lives in ``app/modules/customer_care/_frontend_src/``.
    On deploy you run ``pnpm install && pnpm build`` there which emits
    a ``dist/`` folder; we mount that as static files. If the dist
    folder doesn't exist (e.g. first boot before the frontend build
    ran), we serve a small fallback page that explains how to build it
    so the user gets a clear next-step instead of a 404.
    """
    from pathlib import Path
    from fastapi import APIRouter
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles

    pwa_root = Path(__file__).parent / "modules" / "customer_care"
    dist = pwa_root / "_frontend_src" / "dist"
    if dist.is_dir():
        # ``html=True`` serves index.html for unknown sub-paths — the
        # PWA does client-side routing, so /customercare/inbox/123
        # should resolve to index.html (React Router handles the rest).
        app.mount(
            "/customercare",
            StaticFiles(directory=str(dist), html=True),
            name="customer_care_pwa",
        )
    else:
        # Fallback: an explanatory placeholder until the operator
        # builds the PWA. Still gated on the customercare role would
        # be ideal, but the dashboard URL itself is publicly reachable
        # by design — auth is enforced on /api/v1/customer-care/* via
        # JWT, and the SPA bootstrap is harmless without those tokens.
        placeholder = APIRouter()

        @placeholder.get("/customercare", response_class=HTMLResponse)
        @placeholder.get("/customercare/", response_class=HTMLResponse)
        async def _cc_placeholder() -> str:
            return (
                "<!doctype html>"
                "<html><head><meta charset='utf-8'>"
                "<title>Hypershop Customer Care</title>"
                "<style>body{font:14px/1.5 system-ui;margin:48px auto;max-width:640px;color:#222}"
                "code{background:#f3f3f3;padding:2px 6px;border-radius:3px}"
                "</style></head><body>"
                "<h1>Hypershop Customer Care</h1>"
                "<p>The agent dashboard PWA hasn't been built yet.</p>"
                "<p>To build it, run from the backend project root:</p>"
                "<pre><code>cd app/modules/customer_care/_frontend_src\n"
                "pnpm install\n"
                "pnpm build</code></pre>"
                "<p>This emits <code>dist/</code> which is served here at "
                "<code>/customercare</code>. The agent API is available "
                "now at <code>/api/v1/customer-care/*</code> — log in with "
                "a user holding the <code>customercare_agent</code> or "
                "<code>customercare_admin</code> role.</p>"
                "</body></html>"
            )

        app.include_router(placeholder)


def _mount_seller_dashboard(app: FastAPI) -> None:
    """Mount the seller-facing Vite PWA at ``/seller/``.

    Source: ``app/modules/sellers/_frontend_src/``. On deploy run
    ``pnpm install && pnpm build`` there to emit ``dist/``. If the
    build is missing we serve a small placeholder explaining how to
    build it, mirroring the customer-care PWA mount pattern.
    """
    from pathlib import Path
    from fastapi import APIRouter
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles

    pwa_root = Path(__file__).parent / "modules" / "sellers"
    dist = pwa_root / "_frontend_src" / "dist"
    if dist.is_dir():
        app.mount(
            "/seller",
            StaticFiles(directory=str(dist), html=True),
            name="seller_dashboard",
        )
        return
    placeholder = APIRouter()

    @placeholder.get("/seller", response_class=HTMLResponse)
    @placeholder.get("/seller/", response_class=HTMLResponse)
    async def _seller_placeholder() -> str:
        return (
            "<!doctype html>"
            "<html><head><meta charset='utf-8'>"
            "<title>Hypershop Seller Dashboard</title>"
            "<style>body{font:14px/1.5 system-ui;margin:48px auto;max-width:640px;color:#222}"
            "code{background:#f3f3f3;padding:2px 6px;border-radius:3px}</style>"
            "</head><body>"
            "<h1>Hypershop Seller Dashboard</h1>"
            "<p>The seller PWA hasn't been built yet. Run from the backend project root:</p>"
            "<pre><code>cd app/modules/sellers/_frontend_src\n"
            "pnpm install\npnpm build</code></pre>"
            "<p>Backend seller endpoints already live at "
            "<code>/api/v1/seller/me/*</code>.</p>"
            "</body></html>"
        )

    app.include_router(placeholder)


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()

    # OpenTelemetry — no-op when OTEL_EXPORTER_OTLP_ENDPOINT is unset.
    # Must run BEFORE the FastAPI app is constructed so library
    # instrumentations (httpx, redis) wrap before any client is created.
    init_tracing(service_name=settings.otel_service_name)
    instrument_all_libraries()
    # SQLAlchemy hookup uses the engine reference; the engine is built
    # lazily by app.core.db.session.get_engine() — instrument it now
    # so spans cover queries from the very first request.
    from app.core.db.session import get_engine as _get_engine
    instrument_sqlalchemy(_get_engine())

    app = FastAPI(
        title="Hypershop API",
        version="0.1.0",
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=_lifespan,
        swagger_ui_parameters={"persistAuthorization": True} if settings.docs_enabled else None,
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-Id"],
            expose_headers=["X-Request-Id"],
            max_age=600,
        )

    # Order matters: Starlette wraps in REVERSE add-order, so the
    # last-added middleware is OUTERMOST. We want the metrics
    # middleware outermost so its timing captures the full
    # request → response lifecycle (including time spent inside the
    # other three middlewares). Likewise SecurityHeaders must be the
    # innermost wrapper of the user-supplied stack so its headers
    # land on every response, including 5xx generated upstream.
    app.add_middleware(SecurityHeadersMiddleware)
    # Standard response envelope (2026-05-16). Wraps every 2xx JSON
    # response into {success, message, data, meta}. Ordered between
    # SecurityHeaders (innermost — sets headers AFTER body is built)
    # and RequestIdMiddleware (so request.state.request_id is already
    # populated when the envelope reads it). AccessLogMiddleware sees
    # the wrapped status/size correctly.
    app.add_middleware(ResponseEnvelopeMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(PrometheusMetricsMiddleware)
    # Self-heal is added LAST → OUTERMOST: it wraps every other
    # middleware + the router, retries transient infra failures, and
    # converts any unhandled crash into a recoverable 503 instead of a
    # fatal 500. Server-side half of the resilience engine.
    app.add_middleware(SelfHealMiddleware)

    install_exception_handlers(app)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str | None]:
        return {
            "service": settings.service_name,
            "status": "live",
            "api_prefix": settings.api_prefix,
            "health": f"{settings.api_prefix}/health",
            "readiness": f"{settings.api_prefix}/ready",
            "docs": "/docs" if settings.docs_enabled else None,
            "storefront_preview": "http://127.0.0.1:5050/",
            "admin_preview": "http://127.0.0.1:5050/admin/features/",
        }

    # FastAPI auto-instrumentation — must come AFTER `add_middleware`
    # calls so its own middleware wraps inside the user's stack
    # (correct ordering for span attribution: outermost = trace root).
    instrument_fastapi(app)

    # Force security deps module to import so RBAC's lazy wiring resolves.
    from app.core.security import deps as _deps  # noqa: F401
    from app.modules.catalog.api import catalog_router

    # Importing handlers modules registers their outbox handlers as a side-effect.
    from app.modules.delivery.api import delivery_router
    from app.modules.iam import handlers as _iam_handlers  # noqa: F401
    from app.modules.iam.api import iam_router
    from app.modules.inventory import handlers as _inv_handlers  # noqa: F401
    from app.modules.inventory.api import inventory_router
    from app.modules.orders.api import orders_router
    from app.modules.deliveries.api import deliveries_router
    from app.modules.packing.api import packing_router
    from app.modules.returns.api import returns_router
    from app.modules.finance import handlers as _fin_handlers  # noqa: F401
    from app.modules.finance.api import finance_router
    from app.modules.dashboard.api import dashboard_router
    from app.modules.ai.api import ai_router
    from app.modules.mobile.api import mobile_router
    from app.modules.payments import handlers as _pay_handlers  # noqa: F401
    from app.modules.payments.api import payments_router
    # Subscribes to prescription.approved + payment.captured → invoice dispatch.
    from app.modules.invoice_dispatch import handlers as _inv_handlers  # noqa: F401
    # Subscribes to order lifecycle events → fan-out push notifications.
    from app.modules.push import handlers as _push_handlers  # noqa: F401
    # M4 disputes — auto-registers 6 push handlers (open/respond/escalate/resolve/close).
    from app.modules.disputes import handlers as _disp_handlers  # noqa: F401
    # Inbound WhatsApp delivery-status webhook (Meta posts here).
    from app.modules.whatsapp_webhook.api import whatsapp_webhook_router
    # Public catalog search + admin reindex.
    from app.modules.search.api import search_router
    # Reporting platform — user-facing /reports + admin /admin/reporting.
    from app.modules.reporting.api import reporting_router
    # Register all built-in report builders (idempotent on re-import).
    from app.modules.reporting.builders import register_all as _register_reports
    _register_reports()
    # Rider routing — /rider/* + /admin/rider-dispatch/*
    from app.modules.rider_routing.api import rider_routing_router
    # Rider wallet + settlement — /rider/wallet/* + /admin/rider-wallets/*
    # Importing handlers as a side-effect registers the
    # delivery.delivered → cod_collection ledger handler.
    from app.modules.rider_wallet import handlers as _rw_handlers  # noqa: F401
    from app.modules.rider_wallet.api import rider_wallet_router
    # Supplier payment approval engine — /admin/supplier-payments/*
    # Importing handlers as a side-effect registers the
    # approval-decision → email handlers.
    from app.modules.supplier_payments import handlers as _sp_handlers  # noqa: F401
    from app.modules.supplier_payments.api import supplier_payments_router
    # SEO + dynamic content — /api/v1/seo/* + /api/v1/admin/seo/*
    # plus root-mounted /robots.txt + /sitemap.xml + /r/<path>
    from app.modules.seo.api import seo_api_router, seo_root_router
    # SEO Domination — 7-pillar 10/10 vs Daraz BD overlay (2026-05-28)
    # 10 admin endpoints under /api/v1/admin/seo-domination/*
    from app.modules.seo_domination.api import (
        router as seo_domination_router,
        public_router as seo_domination_public_router,
    )
    # storefront_cms — admin control panel + unified /storefront/layout
    # (banners + nav + featured categories + footer pages). Save fires
    # a revalidation webhook so the customer-web flushes its cache.
    from app.modules.storefront_cms import (
        storefront_admin_router,
        storefront_public_router,
    )
    # Marketplace Fulfillment — 4 aggregation views (seller-pickup,
    # reschedule, sla-alerts, seller-delay) on top of orders+rider data.
    from app.modules.fulfillment import (
        fulfillment_router,
        marketplace_fulfillment_router,
    )
    # Product page videos (Module 35) — public catalog/products/{id}/videos
    # + admin upload / approve / reject / disable. FFmpeg processing
    # happens on the worker via the cron tick registered in
    # app.worker.WorkerSettings.
    from app.modules.product_videos.api import product_videos_router
    # Seller-facing upload endpoint — POST /product-videos/products/{id}/upload.
    # Lives in its own router so seller-side rules (rate limits, ownership
    # check once catalog gets seller_id) can evolve independently.
    from app.modules.product_videos.router import (
        router as product_videos_upload_router,
    )
    # Admin moderation endpoints — /admin/product-videos/...
    # Coexists with the legacy /admin/catalog/videos/... routes; both
    # delegate to ProductVideoService so the rule set is unified.
    from app.modules.product_videos.admin_router import (
        router as product_videos_admin_router,
    )
    # Public product-page video list — GET /products/{id}/videos.
    # Customer-web hits this to render the lazy HLS rail on PDP.
    from app.modules.product_videos.public_router import (
        router as product_videos_public_router,
    )
    # Reviews phase-1 — verified-purchase 1-5 stars + admin moderation.
    # Single aggregator router fans out to public / customer / admin
    # subrouters internally.
    from app.modules.reviews.api import reviews_router
    # Reviews phase-3 — Q&A surface (questions + answers) under
    # `app/modules/product_qa/`. Reuses `reviews.write` / `reviews.admin`
    # permissions so operators don't manage two parallel grant sets.
    from app.modules.product_qa.api import qa_router
    # Sellers phases 1-3:
    #   - admin onboarding + KYC + commission config (phase 1)
    #   - product ownership + cross-seller authz (phase 2; wired into
    #     CatalogService and Module 35 upload)
    #   - seller-self-serve dashboard endpoints (phase 3)
    # Two routers — admin under /admin/sellers/*, seller under /seller/*.
    from app.modules.sellers.api import sellers_router, seller_self_router
    # Importing handlers as a side-effect registers the
    # returns.completed → seller_wallet_ledger.return_debit handler.
    from app.modules.sellers import handlers as _seller_handlers  # noqa: F401
    # Module 47 — AI Customer Care (added 2026-05-13).
    # Mounts /api/v1/customer-care/* (agent API + WhatsApp webhook) and
    # static PWA at /customercare/ for the agent dashboard.
    from app.modules.customer_care import customer_care_router
    # Side-effect import: registers the voice.call.* → sse_bus forwarder
    # so softphone /voice-calls/stream subscribers get live events.
    from app.modules.customer_care import voice_handlers as _cc_voice_handlers  # noqa: F401
    # Sprint 9 — cross-module deferred-phase additions (loyalty tiers,
    # seller self-serve + payouts, P1 in-app notifications)
    from app.modules.sprint9 import (
        loyalty_router as _sprint9_loyalty,
        admin_loyalty_router as _sprint9_admin_loyalty,
        seller_self_router as _sprint9_seller_self,
        admin_payout_router as _sprint9_admin_payouts,
    )
    # Sprint 10 — Sellers phase 4 self-serve registration
    from app.modules.sellers.applications import (
        customer_router as _seller_apps_customer,
        admin_router as _seller_apps_admin,
    )
    # Module 48 — Marketing automation (Sprint 11)
    from app.modules.marketing import marketing_router
    # Module 49 — Subscriptions / recurring orders (Sprint 12)
    from app.modules.subscriptions import (
        subscriptions_customer_router,
        subscriptions_admin_router,
    )
    # Module 50 — Live Shopping. Admin CRUD endpoints are live so the
    # sidebar page is fully wired (2026-06-11). Actual video streaming
    # infra (RTMP, HLS, recording) is still future work — the stream
    # rows are schedulable but playback URLs stay empty until then.
    from app.modules.live_shopping import live_public_router, live_admin_router
    # Module 30 — Reporting Platform BI cube depth (Sprint 13)
    from app.modules.bi import bi_router
    # Module 46 — Funnel KPI segmentation deepening (Sprint 15)
    from app.modules.funnel.segments_api import router as funnel_segments_router
    # Side-effect import: registers the 4 P1 notification handlers
    from app.modules.notifications import handlers as _notif_handlers  # noqa: F401
    # Phase B-1 — customer-facing cart + checkout
    from app.modules.cart.api.router import router as cart_router
    from app.modules.checkout.api.router import router as checkout_router
    # Phase B-2 — customer-facing loyalty + share-and-earn affiliates
    from app.modules.loyalty.api import loyalty_router
    from app.modules.affiliates.api import affiliates_router
    # Phase B-3 (Daraz/Noon parity) — wishlist + coupons + in-app notifications
    from app.modules.wishlist.api import wishlist_router
    from app.modules.coupons.api import coupons_router
    from app.modules.notifications.api import notifications_router
    # Customer-facing support tickets (2026-05-16) — tables existed
    # in DB since the 2026-05-04 migration; api+service wired now.
    # Admin agent-side router landed alongside (Phase 4, 2026-05-16).
    from app.modules.support_tickets.api import (
        support_admin_router,
        support_router,
    )
    # Customer-facing e-commerce wallet (2026-05-16) — fresh tables
    # via migration 0059; distinct from the pharmacy customer_wallets
    # and the rider_wallet / seller_wallet ledgers.
    from app.modules.wallet.api import wallet_router
    # Admin config / module registry endpoint (2026-05-16). FE admin
    # shell calls /admin/config/me to render dynamic nav from the
    # declarative catalog in app/core/registry/admin_modules.py.
    from app.modules.admin_config.api import (
        admin_config_router,
        admin_dashboard_config_router,
        admin_dashboard_layout_router,
        admin_dashboard_widgets_router,
        admin_module_registry_router,
        admin_modules_settings_router,
    )
    # Side-effect import — registers module.config.changed → SSE forwarder.
    from app.modules.admin_config import handlers as _admin_cfg_handlers  # noqa: F401
    # F9 admin v3 stubs — wire every /v1/admin/*-hardening + automation /
    # workflows / bi / etc. namespace so the admin SPA panels render
    # against real endpoints. Replace stub-by-stub as v3 hardening
    # modules land. See app/modules/admin_v3_stubs/__init__.py.
    from app.modules.admin_v3_stubs import admin_v3_stubs_router
    # Composed KPI feed — read-only aggregator for admin dashboard tiles.
    from app.modules.kpi_dashboard.api import router as kpi_dashboard_router
    # Phase B-3 expanded marketplace modules (added 2026-05-12 expanded build).
    from app.modules.analytics.api import analytics_router
    from app.modules.feature_flags.api import feature_flags_router
    from app.modules.fraud.api import fraud_router
    from app.modules.gift_cards.api import gift_cards_router
    from app.modules.referrals.api import referrals_router
    from app.modules.tax_rules.api import tax_rules_router
    # Funnel — customer behavior tracking + retargeting + KPI dashboard.
    # Added 2026-05-13 (merged_funnel_kpi). Mounts under /api/v1/funnel/*.
    from app.modules.funnel import funnel_router
    # Sponsored Products advertising platform — Phase 1.A skeleton (2026-05-17).
    # 7 hypershop_ad_* tables via migration 0067; routers are stubs (501)
    # until Phase 1.B+ wires real handlers. /ads/auction returns empty list.
    from app.modules.ads.api import (
        admin_router as ads_admin_router,
        public_router as ads_public_router,
        seller_router as ads_seller_router,
        webhook_router as ads_webhook_router,
    )
    # Couriers — external courier integrations (Pathao, RedX, Sundarban,
    # Steadfast). Phase M2.A skeleton (2026-05-17) — providers return
    # NotConfigured until M2.B wires real HTTP adapters.
    from app.modules.couriers.api import (
        admin_router as couriers_admin_router,
        webhook_router as couriers_webhook_router,
    )
    # Cart recovery — abandoned-cart reminders + win-back automation.
    # Phase M3.B + M3.C — 2026-05-17. Admin KPI dashboard + customer opt-out.
    from app.modules.cart_recovery.api import (
        admin_router as cart_recovery_admin_router,
        public_router as cart_recovery_public_router,
    )
    # Disputes — buyer/seller/mediator dispute resolution + escrow holds.
    # Phase M4 (2026-05-17).
    from app.modules.disputes.api import (
        admin_router as disputes_admin_router,
        buyer_router as disputes_buyer_router,
        seller_router as disputes_seller_router,
    )
    # M6 customer segmentation — RFM cohorts + named audiences (2026-05-18).
    from app.modules.customer_segments.api import (
        admin_router as customer_segments_admin_router,
    )
    # Seller rating (Phase M5 — 2026-05-18) — admin console + public badge.
    from app.modules.seller_rating.api import (
        admin_router as seller_rating_admin_router,
        public_router as seller_rating_public_router,
    )
    # M7 bulk product upload — seller CSV/XLSX ingest (2026-05-18).
    from app.modules.bulk_upload.api import (
        admin_router as bulk_upload_admin_router,
        seller_router as bulk_upload_seller_router,
    )

    app.include_router(health_router, prefix=settings.api_prefix)
    app.include_router(iam_router, prefix=settings.api_prefix)
    app.include_router(catalog_router, prefix=settings.api_prefix)
    app.include_router(inventory_router, prefix=settings.api_prefix)
    app.include_router(orders_router, prefix=settings.api_prefix)
    app.include_router(delivery_router, prefix=settings.api_prefix)
    app.include_router(packing_router, prefix=settings.api_prefix)
    app.include_router(deliveries_router, prefix=settings.api_prefix)
    app.include_router(returns_router, prefix=settings.api_prefix)
    app.include_router(finance_router, prefix=settings.api_prefix)
    app.include_router(dashboard_router, prefix=settings.api_prefix)
    app.include_router(ai_router, prefix=settings.api_prefix)
    app.include_router(mobile_router, prefix=settings.api_prefix)
    app.include_router(payments_router, prefix=settings.api_prefix)
    app.include_router(whatsapp_webhook_router, prefix=settings.api_prefix)
    app.include_router(search_router, prefix=settings.api_prefix)
    app.include_router(reporting_router, prefix=settings.api_prefix)
    app.include_router(rider_routing_router, prefix=settings.api_prefix)
    app.include_router(rider_wallet_router, prefix=settings.api_prefix)
    app.include_router(supplier_payments_router, prefix=settings.api_prefix)
    app.include_router(seo_api_router, prefix=settings.api_prefix)
    app.include_router(seo_domination_router, prefix=settings.api_prefix)
    app.include_router(seo_domination_public_router, prefix=settings.api_prefix)
    app.include_router(storefront_public_router, prefix=settings.api_prefix)
    app.include_router(storefront_admin_router, prefix=settings.api_prefix)
    app.include_router(fulfillment_router, prefix=settings.api_prefix)
    app.include_router(marketplace_fulfillment_router, prefix=settings.api_prefix)
    # Supervisor + Last-Mile Manager (Phase D — wired 2026-05-29)
    from app.modules.supervisor_lm import supervisor_lm_router  # noqa: E402
    app.include_router(supervisor_lm_router, prefix=settings.api_prefix)
    # Rider mobile compat — /rider/kyc + /rider/scan/parcel (2026-05-29).
    # SDK contract for rider-android + rider-hms QrScanScreen / KycScreen.
    from app.modules.rider_kyc import rider_kyc_router  # noqa: E402
    app.include_router(rider_kyc_router, prefix=settings.api_prefix)
    app.include_router(product_videos_router, prefix=settings.api_prefix)
    app.include_router(product_videos_upload_router, prefix=settings.api_prefix)
    app.include_router(product_videos_admin_router, prefix=settings.api_prefix)
    app.include_router(product_videos_public_router, prefix=settings.api_prefix)
    app.include_router(reviews_router, prefix=settings.api_prefix)
    app.include_router(qa_router, prefix=settings.api_prefix)
    app.include_router(sellers_router, prefix=settings.api_prefix)
    app.include_router(seller_self_router, prefix=settings.api_prefix)
    # Module 47 — customer-care API + agent PWA. The router lives at
    # /api/v1/customer-care/*; the PWA build is served at /customercare
    # by the static-mount block below (only if the build dir exists).
    app.include_router(customer_care_router, prefix=settings.api_prefix)

    # ── Gap endpoints (option B — 2026-06-05) ─────────────────────────────
    # Real backend GET routes for admin pages whose endpoints were missing.
    # Each is imported+mounted defensively so a single bad gap router can
    # never block app boot — it's logged and skipped.
    import logging as _gaplog
    for _gap_mod in (
        "app.modules.rider_kyc.admin_gap",
        "app.modules.admin_v3_stubs.api.wms_gap",
        "app.modules.finance.api.settlements_gap",
        "app.modules.catalog.api.catalog_moderation_gap",
        "app.modules.wallet.api.wallets_gap",
        "app.modules.referrals.api.referrals_gap",
        "app.modules.fraud.api.fraud_extra_gap",
        "app.modules.sellers.api.seller_detail_gap",
        "app.modules.admin_v3_stubs.api.admin_lite_gap",
        "app.modules.seo.api.system_cms_gap",
        "app.modules.inventory.api.warehouse_ops_gap",
        # Action endpoints (option B complete — 2026-06-05)
        "app.modules.rider_kyc.admin_actions_gap",
        "app.modules.admin_v3_stubs.api.wms_actions_gap",
        "app.modules.finance.api.settlements_actions_gap",
        "app.modules.catalog.api.catalog_moderation_actions_gap",
        "app.modules.wallet.api.wallets_actions_gap",
        "app.modules.referrals.api.referrals_actions_gap",
        "app.modules.fraud.api.fraud_extra_actions_gap",
        "app.modules.sellers.api.seller_detail_actions_gap",
        "app.modules.admin_v3_stubs.api.admin_lite_actions_gap",
        "app.modules.seo.api.system_cms_actions_gap",
        "app.modules.inventory.api.warehouse_ops_actions_gap",
        # Round 2 — pricing + ops read gaps (page-mount 404s).
        "app.modules.tax_rules.api.pricing_extra_gap",
        "app.modules.reviews.api.reviews_extra_gap",
        # Round 3 — catalog attribute catalog CRUD (Attributes/Categories tabs).
        "app.modules.catalog.api.catalog_attributes_gap",
        # Resilience engine — self-heal incident status.
        "app.modules.resilience.api",
    ):
        try:
            _m = __import__(_gap_mod, fromlist=["router"])
            app.include_router(_m.router, prefix=settings.api_prefix)
        except Exception as _e:  # noqa: BLE001
            _gaplog.getLogger("hypershop.boot").warning(
                "gap_router_skip %s: %s", _gap_mod, _e,
            )
    # CC unified inbox + voice-call admin + CSAT (migration 0073 — 2026-05-18).
    from app.modules.customer_care import (
        cc_csat_router,
        cc_inbox_router,
        cc_sim_gateway_router,
        cc_voice_calls_router,
    )
    app.include_router(cc_inbox_router, prefix=settings.api_prefix)
    app.include_router(cc_voice_calls_router, prefix=settings.api_prefix)
    app.include_router(cc_csat_router, prefix=settings.api_prefix)
    app.include_router(cc_sim_gateway_router, prefix=settings.api_prefix)
    _mount_customer_care_pwa(app)
    # Sprint 14 — seller-facing Vite PWA at /seller/
    _mount_seller_dashboard(app)
    # Sprint 9 routers
    app.include_router(_sprint9_loyalty, prefix=settings.api_prefix)
    app.include_router(_sprint9_admin_loyalty, prefix=settings.api_prefix)
    app.include_router(_sprint9_seller_self, prefix=settings.api_prefix)
    app.include_router(_sprint9_admin_payouts, prefix=settings.api_prefix)
    # Sprint 10 — sellers self-serve apply + admin approval workflow
    app.include_router(_seller_apps_customer, prefix=settings.api_prefix)
    app.include_router(_seller_apps_admin, prefix=settings.api_prefix)
    # Module 48 — marketing automation router
    app.include_router(marketing_router, prefix=settings.api_prefix)
    # Module 49 — subscriptions (customer + admin)
    app.include_router(subscriptions_customer_router, prefix=settings.api_prefix)
    app.include_router(subscriptions_admin_router, prefix=settings.api_prefix)
    # Module 50 — live shopping (admin CRUD + public listing)
    app.include_router(live_public_router, prefix=settings.api_prefix)
    app.include_router(live_admin_router, prefix=settings.api_prefix)
    # Module 30 — BI cube depth router
    app.include_router(bi_router, prefix=settings.api_prefix)
    # Module 46 — Funnel segments router
    app.include_router(funnel_segments_router, prefix=settings.api_prefix)
    app.include_router(cart_router, prefix=settings.api_prefix)
    app.include_router(checkout_router, prefix=settings.api_prefix)
    # Phase B-2 / B-3 — loyalty, affiliates, wishlist, coupons, notifications.
    app.include_router(loyalty_router, prefix=settings.api_prefix)
    app.include_router(affiliates_router, prefix=settings.api_prefix)
    app.include_router(wishlist_router, prefix=settings.api_prefix)
    app.include_router(coupons_router, prefix=settings.api_prefix)
    app.include_router(notifications_router, prefix=settings.api_prefix)
    app.include_router(support_router, prefix=settings.api_prefix)
    app.include_router(support_admin_router, prefix=settings.api_prefix)
    app.include_router(wallet_router, prefix=settings.api_prefix)
    # Sponsored Products — seller console, admin oversight, public auction.
    app.include_router(ads_seller_router, prefix=settings.api_prefix)
    app.include_router(ads_admin_router, prefix=settings.api_prefix)
    app.include_router(ads_public_router, prefix=settings.api_prefix)
    # Phase 1.B — provider callback for ad-wallet recharges (Bkash et al.)
    app.include_router(ads_webhook_router, prefix=settings.api_prefix)
    # Couriers — admin CRUD + generic webhook entry.
    app.include_router(couriers_admin_router, prefix=settings.api_prefix)
    app.include_router(couriers_webhook_router, prefix=settings.api_prefix)
    # Cart recovery — admin dashboard + public opt-out.
    app.include_router(cart_recovery_admin_router, prefix=settings.api_prefix)
    app.include_router(cart_recovery_public_router, prefix=settings.api_prefix)
    # Disputes — buyer self-serve, seller responses, admin/mediator console.
    app.include_router(disputes_buyer_router, prefix=settings.api_prefix)
    app.include_router(disputes_seller_router, prefix=settings.api_prefix)
    app.include_router(disputes_admin_router, prefix=settings.api_prefix)
    # M6 customer segmentation — admin segments + RFM + audience export.
    app.include_router(
        customer_segments_admin_router, prefix=settings.api_prefix,
    )
    # M7 bulk product upload — seller self-serve + admin oversight.
    app.include_router(bulk_upload_seller_router, prefix=settings.api_prefix)
    app.include_router(bulk_upload_admin_router, prefix=settings.api_prefix)
    # M5 seller rating — admin console + public storefront badge.
    app.include_router(seller_rating_admin_router, prefix=settings.api_prefix)
    app.include_router(seller_rating_public_router, prefix=settings.api_prefix)
    app.include_router(admin_config_router, prefix=settings.api_prefix)
    app.include_router(admin_module_registry_router, prefix=settings.api_prefix)
    app.include_router(admin_modules_settings_router, prefix=settings.api_prefix)
    app.include_router(admin_dashboard_config_router, prefix=settings.api_prefix)
    app.include_router(admin_dashboard_widgets_router, prefix=settings.api_prefix)
    app.include_router(admin_dashboard_layout_router, prefix=settings.api_prefix)
    # F9 admin v3 stubs — every /v1/admin/* endpoint the new admin SPA expects.
    #
    # PRODUCTION GATE (added 2026-05-13): the stubs are placeholder
    # responses (mostly returning empty arrays / static success
    # payloads) intended to keep the admin SPA from 404-ing while the
    # real implementations land in their own modules. Shipping the
    # stubs to a real deploy is dangerous — an attacker can hit
    # /v1/admin/* and get the canned "ok" response without any actual
    # work happening server-side, masking bugs in monitoring + audit.
    # When ENVIRONMENT=production we refuse to mount them; the admin
    # SPA will 404 on any endpoint not yet implemented, which is the
    # correct failure mode.
    if (settings.environment or "").lower() != "production":
        app.include_router(admin_v3_stubs_router, prefix=settings.api_prefix)
        get_logger("hypershop.app").info(
            "admin_v3_stubs_mounted",
            environment=settings.environment,
            note="non-production env — stubs active",
        )
    else:
        get_logger("hypershop.app").warning(
            "admin_v3_stubs_skipped_in_production",
            environment=settings.environment,
        )
    # Composed KPI feed for admin dashboard tiles.
    app.include_router(kpi_dashboard_router, prefix=settings.api_prefix)
    # Phase B-3 expanded marketplace.
    app.include_router(analytics_router, prefix=settings.api_prefix)
    app.include_router(feature_flags_router, prefix=settings.api_prefix)
    app.include_router(fraud_router, prefix=settings.api_prefix)
    app.include_router(gift_cards_router, prefix=settings.api_prefix)
    app.include_router(referrals_router, prefix=settings.api_prefix)
    app.include_router(tax_rules_router, prefix=settings.api_prefix)
    # Funnel (events / customers / retargeting / kpi) — see app/modules/funnel/.
    app.include_router(funnel_router, prefix=settings.api_prefix)
    # Root-mounted (no /api/v1 prefix) — crawlers expect these paths.
    app.include_router(seo_root_router)

    # ---------- Pharmacy-vestige route filter (Hypershop e-commerce) ----------
    # The Master Bundle codebase was forked from an earlier pharmacy
    # project, and a handful of pharmacy-domain admin routes
    # (Rx OCR, medicine suggestion, doctor-sales leaderboard,
    # prescription approval on orders, by-prescription reminder
    # cancellation) are still defined in their respective module
    # routers. Per the project rule "NO pharmacy FEATURES in
    # Hypershop" (see memory/feedback_no_pharmacy_features_hypershop.md)
    # we strip them at startup so they never appear in the OpenAPI
    # schema and return 404 if a stale frontend tries to call them.
    #
    # The route HANDLERS stay imported — only the binding to
    # ``app.routes`` is removed. That way the code keeps compiling
    # and any cross-module imports keep working; we just refuse to
    # serve the URLs.
    _PHARMACY_PATH_FRAGMENTS = (
        "/approve-prescription",
        "/by-prescription/",
        "/doctor-sales",
        "/ocr-prescription",
        "/suggest-medicines",
    )
    _routes_before = len(app.routes)
    app.router.routes = [
        r for r in app.router.routes
        if not (
            hasattr(r, "path")
            and any(frag in r.path for frag in _PHARMACY_PATH_FRAGMENTS)
        )
    ]
    _routes_after = len(app.routes)
    if _routes_before != _routes_after:
        # Use a fresh logger — ``log`` from _lifespan() isn't in scope here.
        get_logger("hypershop.app").info(
            "pharmacy_routes_stripped",
            removed=_routes_before - _routes_after,
            fragments=list(_PHARMACY_PATH_FRAGMENTS),
        )

    # Module-35-first Prometheus exporter — register the metric
    # definitions BEFORE mounting the endpoint so the first scrape
    # already returns the full set instead of a sparse first response.
    # See app/core/metrics.py for the registry contract.
    # NB: ``import app.modules.X`` would rebind the local name ``app``
    # to the package, shadowing the FastAPI instance defined above.
    # Use ``from ... import`` so the local ``app`` keeps pointing at
    # the FastAPI object.
    from app.modules.product_videos import metrics as _video_metrics  # noqa: F401 — side-effect: registers metrics
    install_metrics_route(app)

    return app


app = create_app()
