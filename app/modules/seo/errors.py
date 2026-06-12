"""SEO module exceptions, mapped via the global handler."""

from __future__ import annotations

from app.core.errors import (
    ConflictError,
    NotFoundError,
    ValidationError,
)


class SeoOverrideNotFoundError(NotFoundError):
    code = "seo.override_not_found"


class BannerNotFoundError(NotFoundError):
    code = "seo.banner_not_found"


class BlogPostNotFoundError(NotFoundError):
    code = "seo.blog_post_not_found"


class BlogSlugTakenError(ConflictError):
    code = "seo.blog_slug_taken"


class RedirectNotFoundError(NotFoundError):
    code = "seo.redirect_not_found"


class RedirectLoopError(ValidationError):
    """Raised if from_path == to_path on a redirect."""
    code = "seo.redirect_loop"


class EntityNotFoundError(NotFoundError):
    """Raised when the entity_type/entity_id pair points at a row
    that doesn't exist in the catalog (e.g. building SEO meta for a
    deleted product).
    """
    code = "seo.entity_not_found"
