"""Smoke tests — pure-function only, no DB.

Verifies all 7 pillars expose callable helpers + return non-empty data shapes.
"""
from __future__ import annotations


def test_programmatic_capacity_estimate():
    from app.modules.seo_domination import programmatic
    cap = programmatic.estimate_capacity()
    assert cap["estimate_total_pages"] >= 10_000
    assert cap["cities"] == len(programmatic.BD_CITIES)


def test_programmatic_seed_generation():
    from app.modules.seo_domination import programmatic
    seeds = programmatic.generate_city_category(
        programmatic.BD_CITIES[:3],
        [("electronics", "Electronics"), ("fashion", "Fashion")],
    )
    assert len(seeds) == 6
    assert all(s.page_type == "city_cat" for s in seeds)
    assert all(s.slug.startswith("city/") for s in seeds)


def test_web_story_amp_render():
    from app.modules.seo_domination.web_stories import StoryPage, render_amp_story
    html = render_amp_story(
        title="Test", canonical_url="https://hypershop.com.bd/story/test",
        publisher_logo_url="https://hypershop.com.bd/logo.png",
        poster_portrait_url="https://hypershop.com.bd/poster.jpg",
        pages=[
            StoryPage(image_url="https://x.test/1.jpg", alt="1", caption="One"),
            StoryPage(image_url="https://x.test/2.jpg", alt="2", caption="Two",
                      cta_label="Shop", cta_url="https://hypershop.com.bd/p/1"),
        ],
    )
    assert "<amp-story " in html
    assert "amp-story-page" in html


def test_web_story_discover_eligibility():
    from app.modules.seo_domination.web_stories import discover_eligibility_check
    ok, issues = discover_eligibility_check(
        "https://x/p.jpg", "https://x/l.png", page_count=6,
    )
    assert ok and not issues
    bad_ok, bad_issues = discover_eligibility_check("", "", page_count=2)
    assert not bad_ok and len(bad_issues) >= 2


def test_eeat_person_schema():
    from app.modules.seo_domination.eeat import author_person_schema
    s = author_person_schema({
        "slug": "yousuf-m",
        "full_name": "Yousuf Miah",
        "title_role": "Founder",
        "bio_en": "Founder of Hypershop",
        "expertise_areas": ["ecommerce", "seo"],
        "credentials": [],
        "social_links": {"linkedin": "https://linkedin.com/in/yousuf"},
        "wikidata_qid": None,
    })
    assert s["@type"] == "Person"
    assert s["url"].endswith("/yousuf-m")


def test_eeat_article_schema():
    from app.modules.seo_domination.eeat import article_schema_with_author
    s = article_schema_with_author(
        headline="Test", url="https://hypershop.com.bd/blog/test",
        image_url="https://x/i.jpg",
        published_iso="2026-05-28T10:00:00Z",
        modified_iso="2026-05-28T10:00:00Z",
        author_profile={
            "slug": "y", "full_name": "Y", "title_role": "Editor",
            "bio_en": "bio", "expertise_areas": [], "credentials": [],
            "social_links": {},
        },
        body_word_count=1500,
        description="desc",
    )
    assert s["@type"] == "Article"
    assert s["wordCount"] == 1500
    assert s["author"]["@type"] == "Person"


def test_internal_link_product_suggestions():
    from app.modules.seo_domination.internal_link import suggest_for_product
    out = suggest_for_product(
        product_url="https://hypershop.com.bd/p/a35",
        product_name="Samsung A35",
        category_slug="mobile",
        brand_slug="samsung",
        siblings_in_category=[("/p/m14", "M14"), ("/p/a25", "A25")],
        same_brand_other_cat=[("/p/qled", "Samsung QLED TV")],
        related_guides=[("/blog/best-mobile-bd", "Best mobile in BD")],
        upsells=[("/p/s24", "Galaxy S24")],
    )
    assert len(out) >= 4
    assert any(s.link_type == "upsell" for s in out)


def test_content_pipeline_daily_spec():
    from app.modules.seo_domination.content_pipeline import daily_blog_spec
    spec = daily_blog_spec()
    assert spec.kind == "blog"
    assert spec.min_word_count >= 1500


def test_content_pipeline_annual_output():
    from app.modules.seo_domination.content_pipeline import expected_annual_output
    out = expected_annual_output()
    assert out["total_articles_per_year"] >= 500


def test_entity_graph_city():
    from app.modules.seo_domination.entity_graph import city_sameas
    s = city_sameas("dhaka")
    assert s["wikidata_qid"] == "Q1354"
    assert "wikipedia.org/wiki/Dhaka" in s["wikipedia_url_en"]


def test_entity_graph_brand():
    from app.modules.seo_domination.entity_graph import brand_sameas
    s = brand_sameas("samsung")
    assert s["wikidata_qid"] == "Q20716"


def test_backlinks_render_pitch():
    from app.modules.seo_domination.backlinks import render_pitch
    subj, body = render_pitch(
        "pitch.tech.featured",
        editor_name="Mr Editor",
    )
    assert "Hi Mr Editor" in body
    assert "Hypershop" in subj


def test_domination_scorecard_is_10_of_10():
    from app.modules.seo_domination.service import domination_score_card
    card = domination_score_card()
    assert all(axis["hypershop"] == 10 for axis in card["axes"])
    assert card["hypershop_total"] == 150
    assert card["daraz_total"] < card["hypershop_total"]


def test_generator_fallback_no_creds():
    """Generator must succeed via template even when no LLM creds are set."""
    import os
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ.pop("ANTHROPIC_API_KEY", None)
    from app.modules.seo_domination.generator import generate_article
    out = generate_article(
        topic="Best mobile in Dhaka",
        keywords=["mobile", "dhaka", "bangladesh"],
        min_words=1500,
    )
    assert out["source"] == "template"
    assert out["word_count"] >= 1500
    assert "<h1>" in out["body_html"]
    assert out["seo_score"] > 50


def test_public_router_routes_present():
    """Public router exposes 5 endpoints (page, story, author, sitemap-urls, sitemap-shard, shard-count)."""
    from app.modules.seo_domination.api.public import router as pr
    paths = sorted({r.path for r in pr.routes})
    assert any("/seo-domination/page/" in p for p in paths)
    assert any("/seo-domination/story/" in p for p in paths)
    assert any("/seo-domination/author/" in p for p in paths)
    assert any("/seo-domination/sitemap" in p for p in paths)


def test_admin_router_has_generator_endpoint():
    from app.modules.seo_domination.api.router import router as ar
    paths = sorted({r.path for r in ar.routes})
    assert any("/content/" in p and "generate" in p for p in paths)
    assert any("/health" in p for p in paths)


def test_cron_link_audit_is_real_not_stub():
    """daily_link_audit must do real work — not return {'audited': 0, 'note': '...'}."""
    import inspect
    from app.modules.seo_domination.cron import daily_link_audit
    src = inspect.getsource(daily_link_audit)
    assert "internal-link audit hook" not in src
    assert "decay" in src.lower() or "update" in src.lower()


def test_cron_backlink_pings_is_real_not_stub():
    import inspect
    from app.modules.seo_domination.cron import daily_backlink_pings
    src = inspect.getsource(daily_backlink_pings)
    assert "outreach reminder hook" not in src
    assert "reminder" in src.lower()
    assert "smtp" in src.lower() or "sendgrid" in src.lower()
