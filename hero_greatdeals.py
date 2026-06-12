# -*- coding: utf-8 -*-
"""Rebuild storefront hero into Amazon-style 'Great Deals. Everyday.' (green)."""
P = r"C:/Users/imyou/Downloads/hypershop-storefront-serve/index.html"
html = open(P, encoding="utf-8").read()

BOX = ('<svg width="300" height="230" viewBox="0 0 300 230" fill="none" aria-hidden="true">'
 '<path d="M55 150 L150 188 L245 150 L245 92 L150 128 L55 92 Z" fill="#B9824C"/>'
 '<path d="M55 92 L150 128 L150 188 L55 150 Z" fill="#9A6A3A"/>'
 '<path d="M150 128 L245 92 L245 150 L150 188 Z" fill="#A9743F"/>'
 '<path d="M55 92 L95 66 L188 66 L150 128 Z" fill="#D9A463"/>'
 '<path d="M245 92 L205 66 L150 128 L150 70 Z" fill="#CC9A57"/>'
 '<path d="M95 66 L150 70 L150 128 Z" fill="#E4B575"/>'
 '<g transform="translate(212,46)">'
 '<path d="M0 -44 L9 -29 L27 -34 L24 -16 L42 -8 L27 0 L42 8 L24 16 L27 34 L9 29 L0 44 L-9 29 L-27 34 L-24 16 L-42 8 L-27 0 L-42 -8 L-24 -16 L-27 -34 L-9 -29 Z" fill="#F5B800"/>'
 '<text x="0" y="13" text-anchor="middle" font-size="38" font-weight="900" fill="#0F6B4B" font-family="Arial,Segoe UI,sans-serif">%</text>'
 '</g></svg>')

def tile(href, label, kw, lock):
    return ('<a class="gd-tile" data-internal="1" href="%s">'
            '<span class="gd-thumb"><img src="https://loremflickr.com/300/220/%s?lock=%d" alt="%s" loading="lazy"></span>'
            '<span class="gd-lab">%s</span></a>' % (href, kw, lock, label, label))

def card(title, tiles, link_href, link_text):
    return ('<div class="az-promo-card"><div class="az-promo-title">%s</div>'
            '<div class="gd-grid">%s</div>'
            '<a class="az-promo-link" data-internal="1" href="%s">%s</a></div>'
            % (title, ''.join(tiles), link_href, link_text))

card1 = card("Up to 30% off &middot; Headsets &amp; Speakers",
    [tile("/c/electronics","Headsets","headphones",11), tile("/c/electronics","Earbuds","earbuds",12),
     tile("/c/electronics","Speakers","speaker",13), tile("/c/electronics","Soundbars","soundbar",14)],
    "/c/electronics","Shop now")
card2 = card("Up to 25% off &middot; Home &amp; Daily Needs",
    [tile("/c/grocery","Home &amp; care","detergent",21), tile("/c/grocery","Food &amp; drinks","groceries",22),
     tile("/c/baby","Baby essentials","baby",23), tile("/c/grocery","Pet supplies","pet,dog",24)],
    "/c/grocery","Shop now")
card3 = card("Up to 20% off &middot; Electronics",
    [tile("/c/electronics","Laptops","laptop",31), tile("/c/electronics","Video games","videogame,console",32),
     tile("/c/electronics","Audio","headset",33), tile("/c/electronics","TVs","television",34)],
    "/c/electronics","Shop now")

card4 = ('<div class="az-promo-card"><div class="az-promo-title">Buy Now, Pay in Easy installments</div>'
 '<div class="gd-pay">'
 '<span class="gd-chip" style="background:#E2136E">bKash</span>'
 '<span class="gd-chip" style="background:#F26522">Nagad</span>'
 '<span class="gd-chip" style="background:#8E2DE2">Rocket</span>'
 '<span class="gd-chip" style="background:#0F6B4B">0% EMI</span></div>'
 '<a class="gd-tile" data-internal="1" href="/deals/installment" style="margin-top:auto">'
 '<span class="gd-thumb" style="aspect-ratio:2.4/1;background:linear-gradient(120deg,#15856B,#0F6B4B);color:#fff;font-weight:800;font-size:14px">EMI from &#2547;1,238/mo</span></a>'
 '<a class="az-promo-link" data-internal="1" href="/deals/installment">Choose payment at checkout</a></div>')

NEW = ('  <!-- Hero: "Great Deals. Everyday." single green banner (Amazon-style, brand green) -->\n'
 '    <div class="az-hero-banner" data-slide-count="1" data-active-index="0" style="background:linear-gradient(115deg,#0F6B4B 0%,#15856B 52%,#0A4D36 100%);display:flex;align-items:center;padding:0">\n'
 '      <button type="button" class="az-arrow prev" data-az-arrow="prev" aria-label="Previous slide" style="background:rgba(255,255,255,.18)">&lsaquo;</button>\n'
 '      <div class="az-slide theme-green" data-index="0" data-active="true" style="position:relative;opacity:1;pointer-events:auto;background:none;display:flex;align-items:center;justify-content:space-between;width:100%;padding:26px 70px">\n'
 '        <div class="az-slide-content" style="max-width:54%">\n'
 '          <div class="az-slide-eyebrow">Hypershop &middot; Bangladesh</div>\n'
 '          <h1 class="az-slide-title" style="font-size:58px;line-height:1.02;font-weight:900">Great Deals.<br>Everyday.</h1>\n'
 '          <div class="az-slide-sub">COD &middot; 7-day returns &middot; Free delivery over &#2547;999</div>\n'
 '          <a class="az-slide-cta" data-internal="1" href="/deals">Shop all deals</a>\n'
 '        </div>\n'
 '        <div aria-hidden="true" style="flex:none">' + BOX + '</div>\n'
 '      </div>\n'
 '      <button type="button" class="az-arrow next" data-az-arrow="next" aria-label="Next slide" style="background:rgba(255,255,255,.18)">&rsaquo;</button>\n'
 '    </div>\n\n'
 '    <!-- 4 promo cards (image-tile layout, green accents) -->\n'
 '    <div class="az-promo-row">\n      ' + card1 + card2 + card3 + card4 + '\n    </div>\n  </div>\n\n  ')

# replace from the carousel comment through (exclusive) the TRUST STRIP comment
start = html.index('<!-- Top: 5-slide cross-fade carousel -->')
end = html.index('<!-- TRUST STRIP -->')
html = html[:start] + NEW + html[end:]

# recolor the red flash deal bar -> green
html = html.replace('linear-gradient(90deg,#DC2626,#F59E0B)', 'linear-gradient(90deg,#0F6B4B,#15856B)')

# inject tile/card CSS before first </style>
css = ('  .gd-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;flex:1}\n'
       '  .gd-tile{display:flex;flex-direction:column;text-decoration:none;color:#1f2937}\n'
       '  .gd-thumb{display:flex;align-items:center;justify-content:center;background:#EAF3EF;border-radius:6px;overflow:hidden;aspect-ratio:1.3/1}\n'
       '  .gd-thumb img{width:100%;height:100%;object-fit:cover}\n'
       '  .gd-lab{font-size:12px;margin-top:5px;line-height:1.2;color:#374151}\n'
       '  .gd-tile:hover .gd-lab{color:var(--brand)}\n'
       '  .gd-pay{display:flex;gap:7px;flex-wrap:wrap;margin:2px 0 6px}\n'
       '  .gd-chip{font-size:12px;font-weight:800;padding:7px 11px;border-radius:8px;color:#fff}\n')
html = html.replace('</style>', css + '</style>', 1)

open(P, 'w', encoding='utf-8', newline='').write(html)
print('hero rebuilt: Great Deals. Everyday. (green) + 4 image-tile cards; flash bar recolored green')
