# -*- coding: utf-8 -*-
"""Step 2: wire Watch into nav + make MarketplaceConnect hero green/'Great Deals'."""
FILES = [
 r"F:/Yousuf/E CIMMERCE MASTER DATA/E COMMERCEH MASTER BANDLE/CRM/hypershop_with_crm/frontend/apps/customer-web/components/MarketplaceConnect.tsx",
 r"C:/hs_fe/apps/customer-web/components/MarketplaceConnect.tsx",
]

WATCH_OLD = ('  const fixed = [\n'
 '    `<a class="catnav-item all" role="button" tabindex="0">All</a>`,\n'
 '    `<a class="catnav-item hot" data-internal="1" href="${lp("/deals")}">Daily Deals</a>`,\n'
 '  ];')
WATCH_NEW = ('  const fixed = [\n'
 '    `<a class="catnav-item all" role="button" tabindex="0">All</a>`,\n'
 '    `<a class="catnav-item watch" data-internal="1" href="${lp("/watch")}"><svg width="13" height="13" viewBox="0 0 24 24" style="vertical-align:-1px"><path fill="currentColor" d="M7 5v14l12-7z"/></svg> Watch</a>`,\n'
 '    `<a class="catnav-item hot" data-internal="1" href="${lp("/deals")}">Daily Deals</a>`,\n'
 '  ];')

# lead hero slide -> Great Deals. Everyday. (green); recolor all themes green
HERO_REPL = [
 ('href: "/eid-sale",', 'href: "/deals",'),
 ('brandName: "Eid Sale",', 'brandName: "Hypershop Deals",'),
 ('title: "Our Pride.",', 'title: "Great Deals.",'),
 ('titleAccent: "Our Bangladesh,",', 'titleAccent: "Everyday.",'),
 ('up to 80% off",', 'COD · returns · free delivery",'),
 ('theme: "blue",', 'theme: "green",'),
 ('theme: "pink",', 'theme: "green",'),
 ('theme: "teal",', 'theme: "green",'),
]

for f in FILES:
    try:
        t = open(f, encoding="utf-8").read()
    except FileNotFoundError:
        print("skip (not found):", f); continue
    n_watch = 1 if WATCH_OLD in t else 0
    t = t.replace(WATCH_OLD, WATCH_NEW)
    rc = 0
    for a, b in HERO_REPL:
        c = t.count(a); rc += c; t = t.replace(a, b)
    open(f, "w", encoding="utf-8", newline="\n").write(t)
    tag = "F:" if f.startswith("F:") else "C:"
    print(f"  [{tag}] watch_added={n_watch} hero_replacements={rc}")
print("done")
