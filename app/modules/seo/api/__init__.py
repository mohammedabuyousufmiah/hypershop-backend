"""SEO module API: 4 routers.

  - ``seo_root_router``    — root-mounted /robots.txt, /sitemap.xml,
                             /r/{path} (no /api/v1 prefix); these must
                             be at the public domain root for crawlers.
  - ``seo_public_router``  — /api/v1/seo/* JSON read endpoints
                             (meta bundles, banners, blog).
  - ``seo_admin_router``   — /api/v1/admin/seo/* admin write endpoints
                             (overrides, banners, blog, redirects,
                             multi-lang translations).
  - ``seo_agents_router``  — /api/v1/admin/seo/agents/* AI-driven SEO
                             tooling (keywords, tasks, rank, audit).
"""
from fastapi import APIRouter

from app.modules.seo.api.admin import router as admin_router
from app.modules.seo.api.agents import router as agents_router
from app.modules.seo.api.public import router as public_router
from app.modules.seo.api.root import router as root_router

seo_api_router = APIRouter()
seo_api_router.include_router(public_router)
seo_api_router.include_router(admin_router)
seo_api_router.include_router(agents_router)

# Root-mounted endpoints (no /api/v1 prefix) live in seo_root_router
# so main.py can include them with `prefix=""`.
seo_root_router = root_router

__all__ = ["seo_api_router", "seo_root_router"]
