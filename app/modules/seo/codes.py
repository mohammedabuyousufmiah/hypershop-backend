"""Audit action codes for the SEO module."""

from __future__ import annotations

# ----- SEO overrides -----
ACTION_SEO_OVERRIDE_UPSERTED = "seo.override.upserted"
ACTION_SEO_OVERRIDE_DELETED = "seo.override.deleted"

# ----- Banners -----
ACTION_BANNER_CREATED = "seo.banner.created"
ACTION_BANNER_UPDATED = "seo.banner.updated"
ACTION_BANNER_DEACTIVATED = "seo.banner.deactivated"

# ----- Blog -----
ACTION_BLOG_POST_CREATED = "seo.blog_post.created"
ACTION_BLOG_POST_UPDATED = "seo.blog_post.updated"
ACTION_BLOG_POST_PUBLISHED = "seo.blog_post.published"
ACTION_BLOG_POST_ARCHIVED = "seo.blog_post.archived"

# ----- Redirects -----
ACTION_REDIRECT_CREATED = "seo.redirect.created"
ACTION_REDIRECT_HIT = "seo.redirect.hit"
ACTION_REDIRECT_DELETED = "seo.redirect.deleted"

# ----- Sitemap -----
ACTION_SITEMAP_REQUESTED = "seo.sitemap.requested"

# ----- Product FAQs -----
ACTION_FAQ_CREATED = "seo.faq.created"
ACTION_FAQ_UPDATED = "seo.faq.updated"
ACTION_FAQ_DELETED = "seo.faq.deleted"
