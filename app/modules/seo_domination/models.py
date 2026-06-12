"""ORM models for SEO Domination — mirrors migration 0094."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text,
    UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from app.core.db import Base


class ProgrammaticPage(Base):
    __tablename__ = "seo_programmatic_pages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug = Column(String(512), nullable=False, unique=True, index=True)
    page_type = Column(String(32), nullable=False)
    locale = Column(String(8), nullable=False, default="en")
    city_slug = Column(String(64), nullable=True, index=True)
    category_slug = Column(String(128), nullable=True, index=True)
    brand_slug = Column(String(128), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    meta_description = Column(Text, nullable=False)
    h1 = Column(String(255), nullable=False)
    body_html = Column(Text, nullable=False)
    schema_jsonld = Column(JSONB, nullable=False, default=dict)
    indexable = Column(Boolean, nullable=False, default=True)
    priority = Column(Numeric(2, 1), nullable=False, default=Decimal("0.7"))
    auto_generated = Column(Boolean, nullable=False, default=True)
    ctr_pct = Column(Numeric(5, 2), nullable=True)
    impressions_7d = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=text("(now() AT TIME ZONE 'UTC')"))
    updated_at = Column(DateTime(timezone=True), server_default=text("(now() AT TIME ZONE 'UTC')"))


class WebStory(Base):
    __tablename__ = "seo_web_stories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug = Column(String(255), nullable=False, unique=True, index=True)
    title = Column(String(255), nullable=False)
    publisher_logo_url = Column(String(512), nullable=False)
    poster_portrait_url = Column(String(512), nullable=False)
    locale = Column(String(8), nullable=False, default="en")
    pages_jsonb = Column(JSONB, nullable=False)
    source_product_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    source_category_slug = Column(String(128), nullable=True, index=True)
    amp_html_cached = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="draft")
    views = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=text("(now() AT TIME ZONE 'UTC')"))
    published_at = Column(DateTime(timezone=True), nullable=True)


class AuthorProfile(Base):
    __tablename__ = "seo_author_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug = Column(String(128), nullable=False, unique=True)
    full_name = Column(String(255), nullable=False)
    title_role = Column(String(255), nullable=False)
    avatar_url = Column(String(512), nullable=True)
    bio_en = Column(Text, nullable=False)
    bio_bn = Column(Text, nullable=True)
    expertise_areas = Column(ARRAY(String(64)), nullable=False, default=list)
    credentials = Column(JSONB, nullable=False, default=list)
    social_links = Column(JSONB, nullable=False, default=dict)
    wikidata_qid = Column(String(32), nullable=True)
    article_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=text("(now() AT TIME ZONE 'UTC')"))


class InternalLink(Base):
    __tablename__ = "seo_internal_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_url = Column(String(512), nullable=False, index=True)
    target_url = Column(String(512), nullable=False, index=True)
    anchor_text = Column(String(255), nullable=False)
    link_type = Column(String(32), nullable=False)
    relevance_score = Column(Numeric(4, 3), nullable=False, default=Decimal("0.5"))
    approved = Column(Boolean, nullable=False, default=True)
    clicks_30d = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=text("(now() AT TIME ZONE 'UTC')"))

    __table_args__ = (UniqueConstraint("source_url", "target_url", name="uq_seo_link_pair"),)


class ContentPipelineItem(Base):
    __tablename__ = "seo_content_pipeline"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind = Column(String(32), nullable=False)
    topic = Column(String(255), nullable=False)
    locale = Column(String(8), nullable=False, default="en")
    target_keywords = Column(ARRAY(String(128)), nullable=False, default=list)
    status = Column(String(16), nullable=False, default="queued")
    author_id = Column(UUID(as_uuid=True), nullable=True)
    scheduled_for = Column(DateTime(timezone=True), nullable=True)
    published_url = Column(String(512), nullable=True)
    word_count = Column(Integer, nullable=True)
    seo_score = Column(Numeric(4, 1), nullable=True)
    generation_meta = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=text("(now() AT TIME ZONE 'UTC')"))
    updated_at = Column(DateTime(timezone=True), server_default=text("(now() AT TIME ZONE 'UTC')"))


class EntityGraphNode(Base):
    __tablename__ = "seo_entity_graph"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type = Column(String(32), nullable=False)
    entity_key = Column(String(128), nullable=False)
    wikidata_qid = Column(String(32), nullable=True)
    geonames_id = Column(Integer, nullable=True)
    wikipedia_url_en = Column(String(512), nullable=True)
    wikipedia_url_bn = Column(String(512), nullable=True)
    external_same_as = Column(JSONB, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=text("(now() AT TIME ZONE 'UTC')"))

    __table_args__ = (UniqueConstraint("entity_type", "entity_key", name="uq_entity_lookup"),)


class BacklinkOutreach(Base):
    __tablename__ = "seo_backlink_outreach"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_domain = Column(String(255), nullable=False, index=True)
    target_contact_email = Column(String(255), nullable=True)
    domain_authority = Column(Integer, nullable=True)
    traffic_estimate = Column(Integer, nullable=True)
    niche = Column(String(64), nullable=True)
    outreach_status = Column(String(16), nullable=False, default="discovered")
    pitch_template_id = Column(String(64), nullable=True)
    expected_anchor = Column(String(255), nullable=True)
    link_acquired_url = Column(String(512), nullable=True)
    link_acquired_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("(now() AT TIME ZONE 'UTC')"))
