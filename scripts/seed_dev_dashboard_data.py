"""Seed sample reviews + loyalty transactions + customer notifications.

Idempotent — re-runnable. Skips writing when the table already has
>= 10 rows so re-runs after the first don't keep ballooning the demo
data. Targets the existing ``admin@hypershop.dev`` (or first
``admin``-role user) so the seeded notifications + loyalty txns show
up immediately in the admin's /account/* pages.

Run: ``python -m scripts.seed_dev_dashboard_data``
"""
from __future__ import annotations

import asyncio
import os
import random
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4


# Manual .env load (bypasses MSYS path mangling on Windows).
with open(".env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from sqlalchemy import text  # noqa: E402

from app.core.db.session import get_sessionmaker  # noqa: E402


REVIEW_SAMPLES = [
    (5, "Loved it!", "Genuine product, fast delivery, exactly as described."),
    (5, "Highly recommended", "Five stars. Came with full warranty docs and original box."),
    (4, "Good quality", "Good quality for the price. Packaging was a bit minimal."),
    (5, "Worth every taka", "Best price I've seen in Bangladesh. COD worked smoothly too."),
    (4, "Pretty good", "Item is good but delivery took an extra day to Sylhet."),
    (5, "Awesome", "Better than what I bought from a physical shop last year."),
    (3, "Average", "It's fine but I expected better build quality."),
    (5, "Excellent service", "Customer care answered on WhatsApp within 5 minutes."),
    (4, "Solid", "Solid product. Box was slightly damaged but contents fine."),
    (5, "Five stars", "Hypershop is now my default for electronics."),
]

NOTIFICATION_SAMPLES = [
    ("order", "Order confirmed", "Your order HSO-20260513-YSCA3 was confirmed and is being packed.", "/account/orders"),
    ("order", "Order shipped", "Your order is now with our delivery partner. ETA: tomorrow before 6 PM.", "/account/orders"),
    ("promo", "Eid Mega Sale", "Up to 70% off on electronics + fashion. Tap to browse.", "/deals"),
    ("loyalty", "200 points earned", "You earned 200 Hypershop points from your last order.", "/account/loyalty"),
    ("review", "Rate your purchase", "How was your order? Drop a 1-tap rating and earn 50 bonus points.", "/account/orders"),
    ("price_drop", "Price drop alert", "An item in your wishlist just dropped by ৳500. Tap to view.", "/wishlist"),
    ("cart", "Cart abandoned", "You left items in your cart. Complete checkout to get free delivery in Dhaka.", "/cart"),
]


async def main() -> None:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        # 1. Find a target user (prefer admin@hypershop.dev for visibility)
        user_id_row = (
            await s.execute(
                text("SELECT id FROM users WHERE email = 'admin@hypershop.dev' LIMIT 1"),
            )
        ).first()
        if not user_id_row:
            user_id_row = (
                await s.execute(text("SELECT id FROM users LIMIT 1"))
            ).first()
        if not user_id_row:
            print("No users found — run create-superuser first.")
            return
        user_id: UUID = user_id_row[0]
        print(f"Seeding for user_id={user_id}")

        # 2. Find product ids to attach reviews to (need real product rows)
        products = (
            await s.execute(text("SELECT id FROM products LIMIT 30"))
        ).all()
        if not products:
            print("No products — run seed_catalog_demo first.")
            return

        # --- REVIEWS ---
        existing_reviews = (
            await s.execute(text("SELECT COUNT(*) FROM product_reviews"))
        ).scalar()
        if existing_reviews >= 10:
            print(f"  reviews: already {existing_reviews}, skipping")
        else:
            count = 0
            for p in products[:10]:
                rating, title, body = random.choice(REVIEW_SAMPLES)
                await s.execute(
                    text(
                        """
                        INSERT INTO product_reviews
                          (id, product_id, customer_id, rating, title, body,
                           status, helpful_count, created_at, updated_at)
                        VALUES
                          (:id, :pid, :cid, :rating, :title, :body,
                           'approved', :helpful, :ts, :ts)
                        """
                    ),
                    {
                        "id": uuid4(),
                        "pid": p[0],
                        "cid": user_id,
                        "rating": rating,
                        "title": title,
                        "body": body,
                        "helpful": random.randint(0, 15),
                        "ts": datetime.now(timezone.utc) - timedelta(days=random.randint(1, 60)),
                    },
                )
                count += 1
            print(f"  reviews: inserted {count}")

        # --- LOYALTY ---
        # Ensure loyalty_accounts has a row for this user (FK target).
        acct = (
            await s.execute(
                text("SELECT user_id FROM loyalty_accounts WHERE user_id = :u"),
                {"u": user_id},
            )
        ).first()
        if not acct:
            await s.execute(
                text(
                    """
                    INSERT INTO loyalty_accounts
                      (user_id, balance_points, lifetime_earned_points, tier, created_at, updated_at)
                    VALUES (:u, 0, 0, 'BRONZE', :ts, :ts)
                    """
                ),
                {"u": user_id, "ts": datetime.now(timezone.utc)},
            )

        existing_txns = (
            await s.execute(
                text("SELECT COUNT(*) FROM loyalty_transactions WHERE user_id = :u"),
                {"u": user_id},
            )
        ).scalar()
        if existing_txns >= 5:
            print(f"  loyalty_transactions: already {existing_txns}, skipping")
        else:
            txns = [
                ("EARN", 250, "Welcome bonus"),
                ("EARN", 120, "Order HSO-20260512-AAAA1 — 1% cashback"),
                ("EARN", 60, "Product review reward"),
                ("REDEEM", -100, "Applied at checkout HSO-20260513-YSCA3"),
                ("EARN", 200, "Referral bonus — friend signed up"),
            ]
            total_pts = 0
            for t_type, pts, reason in txns:
                total_pts += pts
                await s.execute(
                    text(
                        """
                        INSERT INTO loyalty_transactions
                          (id, user_id, txn_type, points, reason, created_at)
                        VALUES (:id, :u, :tt, :p, :r, :ts)
                        """
                    ),
                    {
                        "id": uuid4(),
                        "u": user_id,
                        "tt": t_type,
                        "p": pts,
                        "r": reason,
                        "ts": datetime.now(timezone.utc) - timedelta(days=random.randint(1, 45)),
                    },
                )
            # Update balance
            await s.execute(
                text(
                    """
                    UPDATE loyalty_accounts
                       SET balance_points = :b,
                           lifetime_earned_points = lifetime_earned_points + :earned,
                           updated_at = :ts
                     WHERE user_id = :u
                    """
                ),
                {
                    "u": user_id,
                    "b": max(total_pts, 0),
                    "earned": sum(p for _, p, _ in txns if p > 0),
                    "ts": datetime.now(timezone.utc),
                },
            )
            print(f"  loyalty_transactions: inserted {len(txns)} (balance={total_pts})")

        # --- NOTIFICATIONS ---
        existing_notifs = (
            await s.execute(
                text("SELECT COUNT(*) FROM customer_notifications WHERE customer_user_id = :u"),
                {"u": user_id},
            )
        ).scalar()
        if existing_notifs >= 5:
            print(f"  notifications: already {existing_notifs}, skipping")
        else:
            for cat, title, body, url in NOTIFICATION_SAMPLES:
                is_read = random.random() < 0.3
                ts = datetime.now(timezone.utc) - timedelta(hours=random.randint(1, 168))
                await s.execute(
                    text(
                        """
                        INSERT INTO customer_notifications
                          (id, customer_user_id, category, title, body, action_url,
                           is_read, read_at, created_at)
                        VALUES (:id, :u, :cat, :t, :b, :url, :r, :rat, :ts)
                        """
                    ),
                    {
                        "id": uuid4(),
                        "u": user_id,
                        "cat": cat,
                        "t": title,
                        "b": body,
                        "url": url,
                        "r": is_read,
                        "rat": ts + timedelta(minutes=30) if is_read else None,
                        "ts": ts,
                    },
                )
            print(f"  notifications: inserted {len(NOTIFICATION_SAMPLES)}")

        # --- PRODUCT REVIEW AGGREGATES ---
        # Refresh from the reviews we just inserted (idempotent — uses
        # ON CONFLICT to replace).
        await s.execute(
            text(
                """
                INSERT INTO product_review_aggregates
                  (product_id, avg_rating, review_count, created_at, updated_at)
                SELECT product_id,
                       ROUND(AVG(rating)::numeric, 2),
                       COUNT(*),
                       MIN(created_at),
                       MAX(updated_at)
                  FROM product_reviews
                 WHERE status = 'approved'
                 GROUP BY product_id
                ON CONFLICT (product_id) DO UPDATE
                  SET avg_rating = EXCLUDED.avg_rating,
                      review_count = EXCLUDED.review_count,
                      updated_at = EXCLUDED.updated_at
                """
            ),
        )
        n_agg = (await s.execute(text("SELECT COUNT(*) FROM product_review_aggregates"))).scalar()
        print(f"  product_review_aggregates: {n_agg} rows refreshed")

        # --- Q&A — product_questions + product_answers ---
        existing_q = (await s.execute(text("SELECT COUNT(*) FROM product_questions"))).scalar()
        if existing_q >= 5:
            print(f"  product_questions: already {existing_q}, skipping")
        else:
            QUESTIONS = [
                ("Is this product covered by official BD warranty?",
                 "Yes — every electronics item ships with the brand's official 1-year BD warranty."),
                ("How long does delivery to Chittagong take?",
                 "Usually 2–3 business days via Sundarban Courier. Free delivery on orders above ৳1500."),
                ("Can I pay with bKash on delivery?",
                 "Yes, bKash, Nagad, Rocket, and cash on delivery — all supported, no extra charge."),
                ("Is this the latest model or the older version?",
                 "Latest version — we update the SKU within 7 days of every new brand release."),
                ("What's the return policy?",
                 "7-day no-questions-asked return for most categories. Rider picks it up free of charge."),
                ("Do you ship to Sylhet?",
                 "Yes, we ship nationwide. Sylhet ETA: 3 business days, ৳120 delivery fee."),
            ]
            n_q = 0
            n_a = 0
            for i, (q_body, a_body) in enumerate(QUESTIONS):
                if i >= len(products): break
                q_id = uuid4()
                await s.execute(
                    text(
                        """
                        INSERT INTO product_questions
                          (id, product_id, customer_id, body, status, created_at, updated_at)
                        VALUES (:id, :pid, :cid, :body, 'approved', :ts, :ts)
                        """
                    ),
                    {
                        "id": q_id,
                        "pid": products[i][0],
                        "cid": user_id,
                        "body": q_body,
                        "ts": datetime.now(timezone.utc) - timedelta(days=random.randint(3, 30)),
                    },
                )
                n_q += 1
                # Seller answer to every question
                await s.execute(
                    text(
                        """
                        INSERT INTO product_answers
                          (id, question_id, customer_id, body, status,
                           helpful_count, is_seller_answer, created_at, updated_at)
                        VALUES (:id, :qid, :cid, :body, 'approved',
                                :helpful, true, :ts, :ts)
                        """
                    ),
                    {
                        "id": uuid4(),
                        "qid": q_id,
                        "cid": user_id,
                        "body": a_body,
                        "helpful": random.randint(2, 25),
                        "ts": datetime.now(timezone.utc) - timedelta(days=random.randint(1, 25)),
                    },
                )
                n_a += 1
            print(f"  product_questions: {n_q}, product_answers: {n_a}")

        # --- COUPONS ---
        existing_coupons = (await s.execute(text("SELECT COUNT(*) FROM coupons"))).scalar()
        if existing_coupons >= 3:
            print(f"  coupons: already {existing_coupons}, skipping")
        else:
            COUPONS = [
                # (code, description, type, value_minor, min_subtotal_minor, max_discount_minor, max_uses)
                ("WELCOME100", "Welcome — ৳100 off your first order", "FIXED", 10000, 50000, None, 1000),
                ("EID2026", "Eid Mega Sale — 15% off, max ৳500", "PERCENT", 15, 100000, 50000, 5000),
                ("FREESHIP", "Free shipping on orders over ৳1000", "FIXED", 12000, 100000, 12000, None),
                ("DHAKA50", "Dhaka-only — flat ৳50 off", "FIXED", 5000, 30000, None, 10000),
                ("VIP500", "VIP cashback — ৳500 off on ৳5000+", "FIXED", 50000, 500000, None, 200),
            ]
            for code, desc, dtype, val, min_st, max_disc, max_uses in COUPONS:
                await s.execute(
                    text(
                        """
                        INSERT INTO coupons
                          (id, code, description, discount_type, discount_value_minor,
                           min_subtotal_minor, max_discount_minor, max_total_uses,
                           max_uses_per_customer, total_uses, valid_from, valid_until,
                           is_active, created_at)
                        VALUES (:id, :code, :desc, :dtype, :val,
                                :min_st, :max_disc, :max_uses,
                                1, 0, :vf, :vu, true, :ts)
                        ON CONFLICT (code) DO NOTHING
                        """
                    ),
                    {
                        "id": uuid4(),
                        "code": code,
                        "desc": desc,
                        "dtype": dtype,
                        "val": val,
                        "min_st": min_st,
                        "max_disc": max_disc,
                        "max_uses": max_uses,
                        "vf": datetime.now(timezone.utc) - timedelta(days=2),
                        "vu": datetime.now(timezone.utc) + timedelta(days=60),
                        "ts": datetime.now(timezone.utc),
                    },
                )
            print(f"  coupons: inserted {len(COUPONS)}")

        # --- GIFT CARDS ---
        existing_gc = (await s.execute(text("SELECT COUNT(*) FROM gift_cards"))).scalar()
        if existing_gc >= 3:
            print(f"  gift_cards: already {existing_gc}, skipping")
        else:
            GIFT_CARDS = [
                # (code, face_value_minor, status)
                ("GIFT-HS-1000-AAAA", 100000, "active"),
                ("GIFT-HS-2500-BBBB", 250000, "active"),
                ("GIFT-HS-500-CCCC", 50000, "redeemed"),
            ]
            for code, val, status in GIFT_CARDS:
                redeemed_at = datetime.now(timezone.utc) - timedelta(days=5) if status == "redeemed" else None
                redeemed_by = user_id if status == "redeemed" else None
                await s.execute(
                    text(
                        """
                        INSERT INTO gift_cards
                          (id, code, face_value_minor, currency, status,
                           purchased_by_user_id, redeemed_by_user_id, redeemed_at,
                           expires_at, created_at)
                        VALUES (:id, :code, :val, 'BDT', :status,
                                :pby, :rby, :rat, :exp, :ts)
                        ON CONFLICT (code) DO NOTHING
                        """
                    ),
                    {
                        "id": uuid4(),
                        "code": code,
                        "val": val,
                        "status": status,
                        "pby": user_id,
                        "rby": redeemed_by,
                        "rat": redeemed_at,
                        "exp": datetime.now(timezone.utc) + timedelta(days=365),
                        "ts": datetime.now(timezone.utc) - timedelta(days=10),
                    },
                )
            print(f"  gift_cards: inserted {len(GIFT_CARDS)}")

        # --- REFERRAL CODE ---
        existing_ref = (
            await s.execute(
                text("SELECT COUNT(*) FROM referral_codes WHERE user_id = :u"),
                {"u": user_id},
            )
        ).scalar()
        if existing_ref == 0:
            await s.execute(
                text(
                    """
                    INSERT INTO referral_codes
                      (id, user_id, code, total_referrals, rewarded_referrals, created_at)
                    VALUES (:id, :u, :c, 3, 2, :ts)
                    """
                ),
                {
                    "id": uuid4(),
                    "u": user_id,
                    "c": f"HS-{str(user_id)[:8].upper()}",
                    "ts": datetime.now(timezone.utc) - timedelta(days=20),
                },
            )
            print("  referral_codes: inserted 1 for admin")
        else:
            print(f"  referral_codes: already {existing_ref}, skipping")

        # --- BLOG POSTS ---
        existing_blog = (await s.execute(text("SELECT COUNT(*) FROM blog_posts"))).scalar()
        if existing_blog >= 3:
            print(f"  blog_posts: already {existing_blog}, skipping")
        else:
            POSTS = [
                {
                    "slug": "best-smartphones-under-30000-bdt-2026",
                    "title": "Best Smartphones Under ৳30,000 in Bangladesh (2026 Edition)",
                    "excerpt": "Our top 5 picks for mid-range phones with the best camera, battery, and BD warranty.",
                    "body": (
                        "If you have a ৳30,000 budget in 2026, you have more good options than ever. "
                        "Here are our top 5 picks based on real BD warranty support, after-sales service, "
                        "and the kind of performance that holds up after the honeymoon period.\n\n"
                        "## 1. Xiaomi Redmi Note 14 Pro\n"
                        "200 MP camera, 5500 mAh battery, official Xiaomi BD warranty.\n\n"
                        "## 2. Samsung Galaxy A35 5G\n"
                        "Best build quality at the price. 4-year security update commitment.\n\n"
                        "## 3. Realme 13 Pro+\n"
                        "Fastest charging in the segment (100W). Slightly more plasticky body.\n\n"
                        "Read on for full comparison + camera samples shot in Dhaka."
                    ),
                    "tags": "smartphones,buying-guide,under-30k",
                    "image": "https://loremflickr.com/1200/600/smartphone,bangladesh?lock=11",
                },
                {
                    "slug": "how-to-pick-a-saree-for-eid",
                    "title": "How to Pick the Right Saree for Eid",
                    "excerpt": "Aarong, Tangail, or Jamdani? A practical guide for choosing by occasion + budget.",
                    "body": (
                        "Every Eid the same question — Aarong, Tangail, or Jamdani? The answer depends on "
                        "three things: the occasion, your skin tone, and how much you actually want to spend.\n\n"
                        "**For day events** (family gatherings, lunches), a soft Tangail cotton works great. "
                        "**For evening events**, silk chiffon photographs much better under warm lighting. "
                        "**Jamdani** is the most expensive but holds its value — if you're going to spend "
                        "৳25,000+, Jamdani is the only one that doesn't lose colour after 5 washes."
                    ),
                    "tags": "fashion,saree,eid,women",
                    "image": "https://loremflickr.com/1200/600/saree,bangladesh?lock=12",
                },
                {
                    "slug": "bkash-vs-nagad-vs-rocket-for-online-shopping",
                    "title": "bKash vs Nagad vs Rocket — Which Is Best for Online Shopping?",
                    "excerpt": "Real-world transaction times, failure rates, and cashback rates compared.",
                    "body": (
                        "We process ~10,000 mobile-money transactions a month at Hypershop. Here's what the "
                        "data actually says about which gateway is fastest, most reliable, and gives you the "
                        "best cashback.\n\n"
                        "**bKash** is the most reliable but the slowest (avg 8s callback). **Nagad** is "
                        "fastest (avg 3s) but has a 1.2% failure rate during peak hours. **Rocket** is "
                        "improving but still has trouble with merchants that use the older API."
                    ),
                    "tags": "payments,bkash,nagad,rocket,guide",
                    "image": "https://loremflickr.com/1200/600/mobile-payment?lock=13",
                },
                {
                    "slug": "grocery-delivery-dhaka-2026",
                    "title": "Free Grocery Delivery in Dhaka — How It Actually Works",
                    "excerpt": "Cut-off times, stock policies, replacement rules, and tipping etiquette.",
                    "body": (
                        "Order rice, oil, vegetables, dairy by **4 PM** and we'll deliver before 8 PM the "
                        "same day. Free across Dhaka metro, no minimum order. Here's the operational detail.\n\n"
                        "## Replacement policy\n"
                        "If an item is out of stock, you'll get a WhatsApp message with 2 replacement "
                        "options + a \"refund this item\" link. The rider waits for your reply for 10 minutes "
                        "before defaulting to refund."
                    ),
                    "tags": "grocery,delivery,dhaka,how-to",
                    "image": "https://loremflickr.com/1200/600/grocery,vegetables?lock=14",
                },
                {
                    "slug": "what-makes-a-good-baby-stroller",
                    "title": "What Makes a Good Baby Stroller (Bangladesh Edition)",
                    "excerpt": "Surviving Dhaka pavements, rainy season, and the new-grandparent inspection.",
                    "body": (
                        "A stroller that works fine in a Singapore mall will fall apart on Dhanmondi 27 "
                        "in three months. Here's what to look for if you're shopping in Bangladesh.\n\n"
                        "## Wheels\n"
                        "**Air-filled tyres** > foam > hard plastic. Pavement potholes will break a plastic-"
                        "wheeled stroller within weeks.\n\n"
                        "## Frame\n"
                        "Aluminium frame for monsoon resistance. Steel rusts; carbon fibre snaps."
                    ),
                    "tags": "baby,buying-guide,stroller",
                    "image": "https://loremflickr.com/1200/600/baby-stroller?lock=15",
                },
            ]
            for p in POSTS:
                await s.execute(
                    text(
                        """
                        INSERT INTO blog_posts
                          (id, slug, title, excerpt, body_markdown, cover_image_url,
                           author_name, status, published_at, tags_csv, created_at, updated_at)
                        VALUES (:id, :slug, :title, :exc, :body, :img,
                                'Hypershop Editorial', 'published', :pub, :tags, :ts, :ts)
                        ON CONFLICT (slug) DO NOTHING
                        """
                    ),
                    {
                        "id": uuid4(),
                        "slug": p["slug"],
                        "title": p["title"],
                        "exc": p["excerpt"],
                        "body": p["body"],
                        "img": p["image"],
                        "pub": datetime.now(timezone.utc) - timedelta(days=random.randint(1, 30)),
                        "tags": p["tags"],
                        "ts": datetime.now(timezone.utc) - timedelta(days=random.randint(2, 35)),
                    },
                )
            print(f"  blog_posts: inserted {len(POSTS)}")

        # --- WISHLIST_ITEMS — add 4 products to admin's wishlist ---
        existing_wl = (
            await s.execute(
                text("SELECT COUNT(*) FROM wishlist_items WHERE user_id = :u"),
                {"u": user_id},
            )
        ).scalar()
        if existing_wl >= 4:
            print(f"  wishlist_items: already {existing_wl}, skipping")
        else:
            n_wl = 0
            for p in products[:4]:
                await s.execute(
                    text(
                        """
                        INSERT INTO wishlist_items (id, user_id, product_id, created_at)
                        VALUES (:id, :u, :pid, :ts)
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {
                        "id": uuid4(),
                        "u": user_id,
                        "pid": p[0],
                        "ts": datetime.now(timezone.utc) - timedelta(days=random.randint(1, 14)),
                    },
                )
                n_wl += 1
            print(f"  wishlist_items: inserted {n_wl}")

        # --- PAYMENT INTENTS — one captured intent per existing order ---
        order_rows = (
            await s.execute(
                text(
                    "SELECT id, customer_user_id, currency, grand_total "
                    "FROM orders WHERE customer_user_id = :u",
                ),
                {"u": user_id},
            )
        ).all()
        existing_pi = (
            await s.execute(text("SELECT COUNT(*) FROM payment_intents"))
        ).scalar()
        if existing_pi >= len(order_rows) and existing_pi > 0:
            print(f"  payment_intents: already {existing_pi}, skipping")
        elif order_rows:
            n_pi = 0
            for order_id, cust_id, currency, total in order_rows:
                # Skip if intent already exists for this order
                hit = (
                    await s.execute(
                        text("SELECT id FROM payment_intents WHERE order_id = :o"),
                        {"o": order_id},
                    )
                ).first()
                if hit:
                    continue
                now = datetime.now(timezone.utc)
                await s.execute(
                    text(
                        """
                        INSERT INTO payment_intents
                          (id, order_id, customer_user_id, provider, provider_payment_id,
                           status, currency, amount, amount_captured, amount_refunded,
                           initiated_at, captured_at, created_at, updated_at)
                        VALUES (:id, :oid, :cust, 'fake', :pid,
                                'captured', :cur, :amt, :amt, 0,
                                :init, :cap, :now, :now)
                        """
                    ),
                    {
                        "id": uuid4(),
                        "oid": order_id,
                        "cust": cust_id,
                        "pid": f"fake-pi-{uuid4().hex[:16]}",
                        "cur": currency or "BDT",
                        "amt": total,
                        "init": now - timedelta(minutes=5),
                        "cap": now - timedelta(minutes=4),
                        "now": now,
                    },
                )
                n_pi += 1
            print(f"  payment_intents: inserted {n_pi}")

        print("done.")


if __name__ == "__main__":
    asyncio.run(main())
