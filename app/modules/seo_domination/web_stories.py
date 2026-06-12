"""Google Web Stories — AMP-based, Discover-eligible.

Generates AMP HTML for product/category showcase stories.
Discover-eligible if:
  - poster portrait 640x853
  - publisher logo 96x96
  - <= 30 pages per story
  - all images CDN-hosted (R2 in our case)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

AMP_BOILERPLATE = """<!doctype html>
<html amp lang="{locale}">
<head>
  <meta charset="utf-8">
  <script async src="https://cdn.ampproject.org/v0.js"></script>
  <script async custom-element="amp-story" src="https://cdn.ampproject.org/v0/amp-story-1.0.js"></script>
  <title>{title}</title>
  <link rel="canonical" href="{canonical_url}">
  <meta name="viewport" content="width=device-width,minimum-scale=1,initial-scale=1">
  <style amp-boilerplate>body{{-webkit-animation:-amp-start 8s steps(1,end) 0s 1 normal both;-moz-animation:-amp-start 8s steps(1,end) 0s 1 normal both;-ms-animation:-amp-start 8s steps(1,end) 0s 1 normal both;animation:-amp-start 8s steps(1,end) 0s 1 normal both}}@-webkit-keyframes -amp-start{{from{{visibility:hidden}}to{{visibility:visible}}}}@-moz-keyframes -amp-start{{from{{visibility:hidden}}to{{visibility:visible}}}}@-ms-keyframes -amp-start{{from{{visibility:hidden}}to{{visibility:visible}}}}@keyframes -amp-start{{from{{visibility:hidden}}to{{visibility:visible}}}}</style><noscript><style amp-boilerplate>body{{-webkit-animation:none;-moz-animation:none;-ms-animation:none;animation:none}}</style></noscript>
  <style amp-custom>
    amp-story-page {{ background-color: #0F6B4B; }}
    .cap {{ font-family: Inter, sans-serif; font-size: 20px; font-weight: 700; color: #fff; padding: 16px; background: rgba(0,0,0,0.55); border-radius: 12px; margin: 12px; }}
    .cta {{ background: #FF6A3D; color: #fff; padding: 12px 24px; border-radius: 24px; font-weight: 800; text-decoration: none; display: inline-block; margin: 16px; }}
  </style>
</head>
<body>
  <amp-story standalone
    title="{title}"
    publisher="Hypershop"
    publisher-logo-src="{publisher_logo_url}"
    poster-portrait-src="{poster_portrait_url}">
{pages}
  </amp-story>
</body>
</html>
"""

PAGE_TEMPLATE = """    <amp-story-page id="page-{idx}">
      <amp-story-grid-layer template="fill">
        <amp-img src="{image_url}" alt="{alt}" width="720" height="1280" layout="fill"></amp-img>
      </amp-story-grid-layer>
      <amp-story-grid-layer template="vertical">
        <div class="cap">{caption}</div>
        {cta_html}
      </amp-story-grid-layer>
    </amp-story-page>"""


@dataclass
class StoryPage:
    image_url: str
    alt: str
    caption: str
    cta_label: str = ""
    cta_url: str = ""


def render_amp_story(
    title: str,
    canonical_url: str,
    publisher_logo_url: str,
    poster_portrait_url: str,
    pages: Iterable[StoryPage],
    locale: str = "en",
) -> str:
    """Render a complete Web Story to AMP HTML, validator-clean."""
    rendered_pages = []
    for idx, page in enumerate(pages, start=1):
        cta_html = ""
        if page.cta_label and page.cta_url:
            cta_html = f'<a class="cta" href="{page.cta_url}">{page.cta_label}</a>'
        rendered_pages.append(PAGE_TEMPLATE.format(
            idx=idx,
            image_url=page.image_url,
            alt=page.alt,
            caption=page.caption,
            cta_html=cta_html,
        ))
    return AMP_BOILERPLATE.format(
        locale=locale,
        title=title,
        canonical_url=canonical_url,
        publisher_logo_url=publisher_logo_url,
        poster_portrait_url=poster_portrait_url,
        pages="\n".join(rendered_pages),
    )


def discover_eligibility_check(
    poster_portrait_url: str,
    publisher_logo_url: str,
    page_count: int,
) -> tuple[bool, list[str]]:
    """Check if a story qualifies for Google Discover.

    Returns (eligible, [list of issues if not eligible]).
    """
    issues = []
    if not poster_portrait_url:
        issues.append("missing poster-portrait-src (640x853 required)")
    if not publisher_logo_url:
        issues.append("missing publisher-logo-src (96x96 required)")
    if page_count < 4:
        issues.append(f"too few pages ({page_count}); minimum 4 recommended")
    if page_count > 30:
        issues.append(f"too many pages ({page_count}); maximum 30")
    return (len(issues) == 0, issues)
