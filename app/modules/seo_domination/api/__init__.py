"""SEO Domination — admin + public routers."""
from .router import router  # noqa: F401 — admin
from .public import router as public_router  # noqa: F401 — public (programmatic pages, stories, authors, sitemap)
