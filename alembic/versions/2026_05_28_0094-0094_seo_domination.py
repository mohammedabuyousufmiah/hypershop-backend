"""SEO domination — 7-pillar 10/10 vs Daraz BD.

Adds tables to power:
  1. Programmatic landing pages (city x category x brand matrix, 50k+ pages)
  2. Google Web Stories (AMP-based, Discover-eligible)
  3. E-E-A-T author/expert profiles
  4. Internal link graph (auto-suggest related)
  5. Daily AI blog content pipeline
  6. Knowledge Graph entity linking (Wikidata sameAs)
  7. Backlink outreach pipeline

Revision: 0094_seo_domination
Down revision: 0093_mother_qr_workflow
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0094_seo_domination"
down_revision = "0093_mother_qr_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Programmatic landing pages — auto-generated city x category x brand matrix
    op.create_table(
        "seo_programmatic_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("slug", sa.String(512), nullable=False, unique=True, index=True),
        sa.Column("page_type", sa.String(32), nullable=False),  # city_cat | cat_brand | city_brand | brand_cat_city | guide | comparison
        sa.Column("locale", sa.String(8), nullable=False, server_default="en"),
        sa.Column("city_slug", sa.String(64), nullable=True, index=True),
        sa.Column("category_slug", sa.String(128), nullable=True, index=True),
        sa.Column("brand_slug", sa.String(128), nullable=True, index=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("meta_description", sa.Text, nullable=False),
        sa.Column("h1", sa.String(255), nullable=False),
        sa.Column("body_html", sa.Text, nullable=False),
        sa.Column("schema_jsonld", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("indexable", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Numeric(2, 1), nullable=False, server_default="0.7"),
        sa.Column("auto_generated", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("ctr_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("impressions_7d", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(now() AT TIME ZONE 'UTC')"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(now() AT TIME ZONE 'UTC')"), nullable=False),
    )
    op.create_index("ix_seo_prog_type_locale", "seo_programmatic_pages", ["page_type", "locale"])

    # 2. Google Web Stories — AMP Discover-eligible
    op.create_table(
        "seo_web_stories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("slug", sa.String(255), nullable=False, unique=True, index=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("publisher_logo_url", sa.String(512), nullable=False),
        sa.Column("poster_portrait_url", sa.String(512), nullable=False),
        sa.Column("locale", sa.String(8), nullable=False, server_default="en"),
        sa.Column("pages_jsonb", postgresql.JSONB, nullable=False),  # array of {image_url, alt, caption, cta}
        sa.Column("source_product_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("source_category_slug", sa.String(128), nullable=True, index=True),
        sa.Column("amp_html_cached", sa.Text, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),  # draft | published | archived
        sa.Column("views", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(now() AT TIME ZONE 'UTC')"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 3. E-E-A-T author profiles
    op.create_table(
        "seo_author_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("slug", sa.String(128), nullable=False, unique=True),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("title_role", sa.String(255), nullable=False),  # "Senior Editor — Electronics"
        sa.Column("avatar_url", sa.String(512), nullable=True),
        sa.Column("bio_en", sa.Text, nullable=False),
        sa.Column("bio_bn", sa.Text, nullable=True),
        sa.Column("expertise_areas", postgresql.ARRAY(sa.String(64)), nullable=False, server_default="{}"),
        sa.Column("credentials", postgresql.JSONB, nullable=False, server_default="[]"),  # [{type, issuer, year}]
        sa.Column("social_links", postgresql.JSONB, nullable=False, server_default="{}"),  # {linkedin, twitter, github}
        sa.Column("wikidata_qid", sa.String(32), nullable=True),  # for Person sameAs
        sa.Column("article_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(now() AT TIME ZONE 'UTC')"), nullable=False),
    )

    # 4. Internal link graph — auto-suggest related pages
    op.create_table(
        "seo_internal_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_url", sa.String(512), nullable=False, index=True),
        sa.Column("target_url", sa.String(512), nullable=False, index=True),
        sa.Column("anchor_text", sa.String(255), nullable=False),
        sa.Column("link_type", sa.String(32), nullable=False),  # related | upsell | category_sibling | brand_sibling | how_to
        sa.Column("relevance_score", sa.Numeric(4, 3), nullable=False, server_default="0.5"),
        sa.Column("approved", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("clicks_30d", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(now() AT TIME ZONE 'UTC')"), nullable=False),
        sa.UniqueConstraint("source_url", "target_url", name="uq_seo_link_pair"),
    )

    # 5. Daily content pipeline — AI blog + trend roundups
    op.create_table(
        "seo_content_pipeline",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("kind", sa.String(32), nullable=False),  # blog | trend_roundup | buying_guide | comparison | glossary
        sa.Column("topic", sa.String(255), nullable=False),
        sa.Column("locale", sa.String(8), nullable=False, server_default="en"),
        sa.Column("target_keywords", postgresql.ARRAY(sa.String(128)), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),  # queued | generating | review | published | failed
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_url", sa.String(512), nullable=True),
        sa.Column("word_count", sa.Integer, nullable=True),
        sa.Column("seo_score", sa.Numeric(4, 1), nullable=True),  # 0.0 — 100.0
        sa.Column("generation_meta", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(now() AT TIME ZONE 'UTC')"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(now() AT TIME ZONE 'UTC')"), nullable=False),
    )
    op.create_index("ix_content_status_sched", "seo_content_pipeline", ["status", "scheduled_for"])

    # 6. Knowledge Graph entity links (Wikidata, GeoNames, etc.)
    op.create_table(
        "seo_entity_graph",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("entity_type", sa.String(32), nullable=False),  # product | brand | category | city | person | organization
        sa.Column("entity_key", sa.String(128), nullable=False),  # local slug/id
        sa.Column("wikidata_qid", sa.String(32), nullable=True),  # e.g. Q42 (Wikidata)
        sa.Column("geonames_id", sa.Integer, nullable=True),  # for cities
        sa.Column("wikipedia_url_en", sa.String(512), nullable=True),
        sa.Column("wikipedia_url_bn", sa.String(512), nullable=True),
        sa.Column("external_same_as", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(now() AT TIME ZONE 'UTC')"), nullable=False),
        sa.UniqueConstraint("entity_type", "entity_key", name="uq_entity_lookup"),
    )

    # 7. Backlink outreach pipeline — BD media + brand partnerships
    op.create_table(
        "seo_backlink_outreach",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("target_domain", sa.String(255), nullable=False, index=True),
        sa.Column("target_contact_email", sa.String(255), nullable=True),
        sa.Column("domain_authority", sa.Integer, nullable=True),
        sa.Column("traffic_estimate", sa.Integer, nullable=True),
        sa.Column("niche", sa.String(64), nullable=True),  # tech | fashion | local-news | bd-tech-blog
        sa.Column("outreach_status", sa.String(16), nullable=False, server_default="discovered"),  # discovered | pitched | linked | declined
        sa.Column("pitch_template_id", sa.String(64), nullable=True),
        sa.Column("expected_anchor", sa.String(255), nullable=True),
        sa.Column("link_acquired_url", sa.String(512), nullable=True),
        sa.Column("link_acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(now() AT TIME ZONE 'UTC')"), nullable=False),
    )


def downgrade() -> None:
    for t in [
        "seo_backlink_outreach",
        "seo_entity_graph",
        "seo_content_pipeline",
        "seo_internal_links",
        "seo_author_profiles",
        "seo_web_stories",
        "seo_programmatic_pages",
    ]:
        op.drop_table(t)
