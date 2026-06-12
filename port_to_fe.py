# -*- coding: utf-8 -*-
"""Port the preview design (CSS + BODY markup) into the real FE MarketplaceHome.tsx, visual 1:1.

- STYLES <- preview's <style> CSS
- BODY   <- preview's <body> markup (sprite + all visual markup), EXCLUDING <script> blocks
           (the real app wires interactivity via React/MarketplaceConnect, not the
            preview's vanilla JS).
- HERO_CAROUSEL_SCRIPT + the React component are left untouched.
"""
import re

PREV = r"C:/Users/imyou/Downloads/hypershop-storefront-serve/index.html"
MH = r"F:/Yousuf/E CIMMERCE MASTER DATA/E COMMERCEH MASTER BANDLE/CRM/hypershop_with_crm/frontend/apps/customer-web/components/MarketplaceHome.tsx"

prev = open(PREV, encoding="utf-8").read()

# --- extract CSS (first <style>...</style>) ---
css = re.search(r"<style>(.*?)</style>", prev, re.S).group(1).strip("\n")

# --- extract BODY markup: after <body...> up to the first <script ---
after_body = re.split(r"<body[^>]*>", prev, maxsplit=1)[1]
body = after_body.split("<script", 1)[0].rstrip()

# safety: no backticks / ${ that would break String.raw
assert "`" not in css and "`" not in body, "backtick found"
assert "${" not in css and "${" not in body, "${ found"

mh = open(MH, encoding="utf-8").read()
orig_len = len(mh)

# replace STYLES content (keep the const ... String.raw` wrapper + `;)
mh, n1 = re.subn(r"(const STYLES = String\.raw`).*?(`;)",
                 lambda m: m.group(1) + "\n" + css + "\n" + m.group(2), mh, count=1, flags=re.S)
# replace BODY content
mh, n2 = re.subn(r"(const BODY = String\.raw`).*?(`;)",
                 lambda m: m.group(1) + "\n" + body + "\n" + m.group(2), mh, count=1, flags=re.S)

assert n1 == 1 and n2 == 1, f"replace count STYLES={n1} BODY={n2}"

# sanity: component + carousel script still present
for marker in ("const HERO_CAROUSEL_SCRIPT", "export function MarketplaceHome", "dangerouslySetInnerHTML"):
    assert marker in mh, "missing " + marker

open(MH, "w", encoding="utf-8", newline="\n").write(mh)
print(f"ported. STYLES+BODY replaced. file {orig_len} -> {len(mh)} bytes")
print(f"  css {len(css)} chars · body {len(body)} chars")
