# -*- coding: utf-8 -*-
"""Align fromProductDetail() to the real backend detail shape (name/brand{}/media/variants)."""
OLD = '''export function fromProductDetail(wire: ProductDetailWire): ProductDetail {
  return {
    id: wire.id,
    slug: wire.slug,
    title: wire.title,
    description: wire.description,
    brand_name: wire.brand,
    category_path: wire.category_path,
    attributes: wire.attributes ?? {},
    images: [...(wire.images ?? [])]
      .sort((a, b) => a.sort_order - b.sort_order)
      .map(fromProductImageWire),
    offers: (wire.offers ?? []).map(fromOfferPublicWire),
    buy_box: wire.buy_box ? fromOfferPublicWire(wire.buy_box) : null,
    variants: wire.variants
      .filter((v) => v.is_active)
      .sort((a, b) => a.sort_order - b.sort_order)
      .map(fromVariantWire),
    rating_avg: wire.rating_avg,
    rating_count: wire.rating_count,
  };
}'''

NEW = '''export function fromProductDetail(wire: ProductDetailWire): ProductDetail {
  // Backend detail serializer returns name/brand{}/media/variants (no
  // offers/buy_box). Map defensively + synthesize offers + buy_box from
  // active variants (price lives on the variant).
  const w = wire as any;
  const currency = w.base_currency ?? "BDT";
  const media: any[] = Array.isArray(w.media)
    ? w.media
    : Array.isArray(w.images)
      ? w.images
      : [];
  const images = media
    .map((m: any, i: number) => ({
      id: String(m.id ?? i),
      url: m.url ?? null,
      alt: m.alt ?? null,
      is_primary: (m.position ?? m.sort_order ?? i) === 0,
      sort_order: m.position ?? m.sort_order ?? i,
    }))
    .sort((a: any, b: any) => a.sort_order - b.sort_order);
  const activeVariants: any[] = (Array.isArray(w.variants) ? w.variants : [])
    .filter((v: any) => v.is_active !== false);
  const offers: any[] = activeVariants
    .filter((v: any) => v.price != null)
    .map((v: any) => ({
      id: v.id,
      seller_id: v.seller_id ?? "",
      price: { amount_minor: decimalStringToMinor(String(v.price)), currency },
      in_stock: true,
    }));
  return {
    id: w.id,
    slug: w.slug,
    title: w.title ?? w.name ?? "",
    description: w.description ?? w.short_description ?? null,
    brand_name:
      typeof w.brand === "string" ? w.brand : (w.brand?.name ?? w.brand_name ?? null),
    category_path:
      w.category_path ??
      (typeof w.category === "object" ? w.category?.name : w.category) ??
      null,
    attributes: w.attributes ?? {},
    images,
    offers,
    buy_box: offers[0] ?? (w.buy_box ? fromOfferPublicWire(w.buy_box) : null),
    variants: activeVariants.map((v: any) => ({
      id: v.id,
      sku: v.sku ?? "",
      name: v.name ?? null,
      is_active: v.is_active !== false,
      sort_order: v.sort_order ?? 0,
      attribute_values: v.attribute_values ?? [],
    })),
    rating_avg: w.rating_avg ?? null,
    rating_count: w.rating_count ?? 0,
  };
}'''

for f in [
 r"F:/Yousuf/E CIMMERCE MASTER DATA/E COMMERCEH MASTER BANDLE/CRM/hypershop_with_crm/frontend/packages/api-client/src/normalise.ts",
 r"C:/hs_fe/packages/api-client/src/normalise.ts",
]:
    try: t=open(f,encoding="utf-8").read()
    except FileNotFoundError: print("skip",f[:2]); continue
    if OLD in t:
        t=t.replace(OLD,NEW,1); open(f,"w",encoding="utf-8",newline="\n").write(t)
        print(("F:" if f.startswith("F:") else "C:"),"patched OK")
    else:
        print(("F:" if f.startswith("F:") else "C:"),"OLD NOT FOUND")
