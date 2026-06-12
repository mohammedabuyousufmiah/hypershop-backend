"""Replace storefront emoji with inline SVG icons (sprite + <use>).

Safe-by-context: only converts emoji that live in innerHTML / markup.
LEAVES alone (glyphs): caret ▾, check ✓, cross ✗, filled heart ♥ (textContent),
the ☰ hamburger (CSS content — handled separately), country flags, 𝕏.
Product-image fallback ('🛒' in JS) becomes a picture icon, not a cart.
Idempotent: bails if the sprite is already injected.
"""
import io, sys
P = r"C:/Users/imyou/Downloads/hypershop-storefront-serve/index.html"
html = open(P, encoding="utf-8").read()
if 'id="hs-icons"' in html:
    print("sprite already present — nothing to do"); sys.exit(0)

# ---- icon symbols (viewBox 24, currentColor) ----
S = {
 "cart": '<path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M3 4h2l2.2 11h11l1.8-8H6"/><circle cx="9.5" cy="20" r="1.4" fill="currentColor"/><circle cx="18" cy="20" r="1.4" fill="currentColor"/>',
 "pin": '<path fill="currentColor" d="M12 2a7 7 0 00-7 7c0 5.2 7 13 7 13s7-7.8 7-13a7 7 0 00-7-7z"/><circle cx="12" cy="9" r="2.6" fill="#fff"/>',
 "phone": '<path fill="currentColor" d="M6.6 3H4.6C3.7 3 3 3.7 3 4.6 3 13 11 21 19.4 21c.9 0 1.6-.7 1.6-1.6v-2c0-.7-.5-1.3-1.2-1.5l-3-.6c-.6-.1-1.2.1-1.5.6l-1 1.4c-2.3-1.1-4.2-3-5.3-5.3l1.4-1c.5-.4.7-1 .6-1.6l-.6-3C7.9 3.5 7.3 3 6.6 3z"/>',
 "search": '<g fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></g>',
 "bolt": '<path fill="currentColor" d="M13 2L4 14h6l-1 8 9-12h-6z"/>',
 "rocket": '<g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 18.5c1-2.5 2.5-3.5 2.5-3.5M15 4c3 1.6 4 6 3 10.5l-4 3-4-4 3-4.5C16 7 16 5 15 4z"/><circle cx="14" cy="9" r="1.3" fill="currentColor"/></g>',
 "info": '<g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5"/></g><circle cx="12" cy="7.6" r="0.9" fill="currentColor"/>',
 "mail": '<g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3.5 6.5L12 13l8.5-6.5"/></g>',
 "star": '<path fill="currentColor" d="M12 2.5l2.9 5.9 6.5.9-4.7 4.6 1.1 6.5L12 17.4 5.7 20.4l1.1-6.5L2.1 9.3l6.5-.9z"/>',
 "heart": '<path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round" d="M12 20.3l-1.4-1.3C5.4 14.3 2 11.2 2 7.5 2 5 4 3 6.5 3c1.7 0 3.3.8 4.5 2.1C12.2 3.8 13.8 3 15.5 3 18 3 20 5 20 7.5c0 3.7-3.4 6.8-8.6 11.5z"/>',
 "heartf": '<path fill="currentColor" d="M12 20.3l-1.4-1.3C5.4 14.3 2 11.2 2 7.5 2 5 4 3 6.5 3c1.7 0 3.3.8 4.5 2.1C12.2 3.8 13.8 3 15.5 3 18 3 20 5 20 7.5c0 3.7-3.4 6.8-8.6 11.5z"/>',
 "play": '<path fill="currentColor" d="M7 5v14l12-7z"/>',
 "user": '<g fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4.5 21c.7-3.8 3.8-6 7.5-6s6.8 2.2 7.5 6"/></g>',
 "package": '<g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7.5l9-4.5 9 4.5v9L12 21l-9-4.5z"/><path d="M3 7.5l9 4.5 9-4.5M12 12v9"/></g>',
 "gift": '<g fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><rect x="3" y="8" width="18" height="5"/><path d="M5 13h14v8H5zM12 8v13M12 8C12 8 10.5 3.5 8 4.5 5.8 5.6 9 8 12 8zM12 8s1.5-4.5 4-3.5c2.2 1.1-1 3.5-4 3.5z"/></g>',
 "chat": '<path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" d="M20 4H4v12h4v4l5-4h7z"/>',
 "card": '<g fill="none" stroke="currentColor" stroke-width="1.8"><rect x="2.5" y="5" width="19" height="14" rx="2"/><path d="M2.5 9.5h19"/></g>',
 "question": '<g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 114 2c-1 .8-1.5 1.3-1.5 2.5"/></g><circle cx="12" cy="17.2" r="0.9" fill="currentColor"/>',
 "leaf": '<path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M4 20c0-9 7-15 16-15 0 9-6 15-15 15zM5 19c4-5 8-7 11-8"/>',
 "truck": '<g fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M2 6h11v9H2zM13 9h4l4 3.5V15h-8z"/><circle cx="6" cy="18" r="1.6"/><circle cx="17" cy="18" r="1.6"/></g>',
 "lock": '<g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="5" y="10.5" width="14" height="9.5" rx="2"/><path d="M8 10.5V8a4 4 0 018 0v2.5"/></g>',
 "headphone": '<path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M4 14v4a2 2 0 002 2h1v-7H5a1 1 0 01-1-1 8 8 0 0116 0 1 1 0 01-1 1h-2v7h1a2 2 0 002-2v-4"/>',
 "clock": '<g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></g>',
 "monitor": '<g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16v4"/></g>',
 "bag": '<g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 8h14l-1 12H6z"/><path d="M9 8V6a3 3 0 016 0v2"/></g>',
 "bulb": '<g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6M10 21h4M12 3a6 6 0 00-4 10.5c.7.7 1 1.3 1 2.5h6c0-1.2.3-1.8 1-2.5A6 6 0 0012 3z"/></g>',
 "robot": '<g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><rect x="4" y="8" width="16" height="11" rx="2"/><path d="M12 4v4M8 13v1.5M16 13v1.5"/></g>',
 "camera": '<g fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="M3 7h4l1.5-2h7L17 7h4v12H3z"/><circle cx="12" cy="13" r="3.5"/></g>',
 "apple": '<path fill="currentColor" d="M16 13.2c0 3-2 4.8-3.1 4.8-.8 0-1.3-.5-2.3-.5s-1.6.5-2.4.5C6.9 18 5 15.5 5 12.6 5 9.8 6.8 8.2 8.5 8.2c.9 0 1.7.6 2.3.6.6 0 1.5-.7 2.6-.6 1 0 2 .4 2.6 1.3-1.6 1-1.7 3.4 0 4.7zM12.6 6.9c.5-.7.5-1.8.4-2.2-.9.1-1.7.6-2.1 1.1-.4.5-.6 1.3-.5 2 .8.1 1.6-.3 2.2-.9z"/>',
 "image": '<g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><rect x="3" y="4.5" width="18" height="15" rx="2"/><circle cx="8.5" cy="9.5" r="1.6"/><path d="M4 18l5-5 3.5 3.5L16 12l4 5"/></g>',
 "tag": '<g fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="M4 4h7l9 9-7 7-9-9z"/><circle cx="8" cy="8" r="1.3" fill="currentColor" stroke="none"/></g>',
}

MAP = {
 "\U0001F6D2":"cart","\U0001F4CD":"pin","\U0001F4DE":"phone","\U0001F50D":"search",
 "⚡":"bolt","\U0001F680":"rocket","ⓘ":"info","✉":"mail",
 "★":"star","☆":"star","♡":"heart","▶":"play","▷":"play",
 "\U0001F464":"user","\U0001F4E6":"package","\U0001F381":"gift","\U0001F3AC":"play",
 "\U0001F4AC":"chat","\U0001F4B3":"card","\U0001F4B5":"card","❓":"question",
 "\U0001F96C":"leaf","\U0001F33F":"leaf","\U0001F69A":"truck","\U0001F512":"lock",
 "\U0001F3A7":"headphone","⌚":"clock","\U0001F4BB":"monitor","\U0001F3AE":"monitor",
 "\U0001F45F":"tag","\U0001F9F4":"tag","\U0001F373":"tag","\U0001F392":"bag",
 "\U0001F4A1":"bulb","\U0001F9F8":"tag","\U0001F916":"robot","\U0001F4F7":"camera",
 "\U0001F457":"tag","\U0001F4F1":"phone","\U0001F48A":"tag","\U0001F34E":"apple",
}

def svg(name):
    return ('<svg class="ic ic-%s" viewBox="0 0 24 24" aria-hidden="true">%s</svg>' % (name, S[name]))

# product-image fallback: the JS literal '🛒' -> picture icon (not cart)
html = html.replace("'\U0001F6D2'", "'" + svg("image") + "'")

# sweep all mapped emoji (now-remaining 🛒 are header/title -> cart)
for emo, name in MAP.items():
    if name in S:
        html = html.replace(emo, svg(name))
# strip stray variation selectors
html = html.replace("️", "")

# inject .ic CSS before first </style>
css = ('  .ic{width:1em;height:1em;display:inline-block;vertical-align:-.14em;flex:none}\n'
       '  .product-img .ic,.ph .ic{width:48px;height:48px;color:#cbd5d0}\n')
html = html.replace("</style>", css + "</style>", 1)

# inject sprite right after <body>
sprite = '<svg id="hs-icons" style="position:absolute;width:0;height:0;overflow:hidden" aria-hidden="true">' \
         + ''.join('<symbol id="i-%s" viewBox="0 0 24 24">%s</symbol>' % (n, S[n]) for n in S) \
         + '</svg>\n'
# we inlined symbols directly in each <svg> above, sprite optional; keep for <use> usages
import re
html = re.sub(r"(<body[^>]*>)", r"\1\n" + sprite.replace("\\","\\\\"), html, count=1)

open(P, "w", encoding="utf-8", newline="").write(html)
print("svgified. icons used:", sorted(set(MAP.values())))
