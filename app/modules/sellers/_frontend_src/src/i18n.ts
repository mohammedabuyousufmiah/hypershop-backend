/**
 * Lean i18n harness for the seller dashboard.
 * English + Bangla. Persisted in localStorage under `seller.locale`.
 */
import { useEffect, useState } from "react";

type Locale = "en" | "bn";
const LOCALE_KEY = "seller.locale";

const STRINGS: Record<Locale, Record<string, string>> = {
  en: {
    "app.title": "Hypershop Seller",
    "nav.dashboard": "Dashboard",
    "nav.products": "Products",
    "nav.orders": "Orders",
    "nav.payouts": "Payouts",
    "nav.logout": "Sign out",
    "login.heading": "Seller dashboard sign-in",
    "login.email": "Email",
    "login.password": "Password",
    "login.submit": "Sign in",
    "login.submitting": "Signing in…",
    "login.invalid": "Invalid email or password",
    "login.not_seller": "Your account isn't linked to a seller. Contact admin.",
    "dashboard.total_products": "Total products",
    "dashboard.active_products": "Active",
    "dashboard.recent_orders": "Recent orders",
    "dashboard.recent_revenue": "Recent revenue",
    "dashboard.pending_payouts": "Pending payouts",
    "dashboard.window": "Last {days} days",
    "products.heading": "My products",
    "products.empty": "No products yet — contact admin to add products to your seller account.",
    "products.col.name": "Name",
    "products.col.slug": "Slug",
    "products.col.status": "Status",
    "products.col.created": "Created",
    "products.col.actions": "Actions",
    "products.action.edit": "Edit",
    "edit.back": "Back to products",
    "edit.name": "Name",
    "edit.description": "Description",
    "edit.status": "Status",
    "edit.status_active": "Active",
    "edit.status_inactive": "Inactive",
    "edit.status_draft": "Draft",
    "edit.variants": "Variants",
    "edit.no_variants": "No variants yet.",
    "edit.variants_help": "Variant pricing is managed by admin. Contact support to add or change variants.",
    "edit.save": "Save changes",
    "edit.saving": "Saving…",
    "edit.cancel": "Cancel",
    "edit.saved": "Saved.",
    "chart.revenue_30d": "Revenue — last 30 days",
    "chart.top_products": "Top products — last 30 days",
    "chart.no_data": "Not enough data yet.",
    "chart.orders_axis": "orders",
    "chart.revenue_axis": "revenue",
    "orders.heading": "Orders with my products",
    "orders.empty": "No orders yet.",
    "orders.col.code": "Order code",
    "orders.col.status": "Status",
    "orders.col.total": "Total",
    "orders.col.placed": "Placed at",
    "payouts.heading": "Payouts",
    "payouts.empty": "No payouts on record yet.",
    "payouts.col.period": "Period",
    "payouts.col.gross": "Gross",
    "payouts.col.commission": "Commission",
    "payouts.col.return_debit": "Return debit",
    "payouts.col.net": "Net",
    "payouts.col.status": "Status",
    "payouts.col.paid_at": "Paid at",
    "common.loading": "Loading…",
    "common.error": "Failed to load",
    "common.retry": "Retry",
  },
  bn: {
    "app.title": "Hypershop সেলার",
    "nav.dashboard": "ড্যাশবোর্ড",
    "nav.products": "পণ্য",
    "nav.orders": "অর্ডার",
    "nav.payouts": "পেআউট",
    "nav.logout": "সাইন আউট",
    "login.heading": "সেলার ড্যাশবোর্ড সাইন-ইন",
    "login.email": "ইমেইল",
    "login.password": "পাসওয়ার্ড",
    "login.submit": "সাইন ইন",
    "login.submitting": "সাইন ইন হচ্ছে…",
    "login.invalid": "ভুল ইমেইল অথবা পাসওয়ার্ড",
    "login.not_seller": "আপনার একাউন্ট কোনো সেলারের সাথে যুক্ত নয়। অ্যাডমিনের সাথে যোগাযোগ করুন।",
    "dashboard.total_products": "মোট পণ্য",
    "dashboard.active_products": "সক্রিয়",
    "dashboard.recent_orders": "সাম্প্রতিক অর্ডার",
    "dashboard.recent_revenue": "সাম্প্রতিক আয়",
    "dashboard.pending_payouts": "বকেয়া পেআউট",
    "dashboard.window": "শেষ {days} দিন",
    "products.heading": "আমার পণ্য",
    "products.empty": "এখনো কোনো পণ্য নেই — অ্যাডমিনকে বলে পণ্য যোগ করান।",
    "products.col.name": "নাম",
    "products.col.slug": "স্লাগ",
    "products.col.status": "স্ট্যাটাস",
    "products.col.created": "যোগ হয়েছে",
    "products.col.actions": "অ্যাকশন",
    "products.action.edit": "এডিট",
    "edit.back": "পণ্যে ফেরত",
    "edit.name": "নাম",
    "edit.description": "বিবরণ",
    "edit.status": "স্ট্যাটাস",
    "edit.status_active": "সক্রিয়",
    "edit.status_inactive": "নিষ্ক্রিয়",
    "edit.status_draft": "খসড়া",
    "edit.variants": "ভেরিয়েন্ট",
    "edit.no_variants": "এখনো কোনো ভেরিয়েন্ট নেই।",
    "edit.variants_help": "ভেরিয়েন্টের দাম অ্যাডমিন নিয়ন্ত্রণ করেন। যুক্ত/পরিবর্তনের জন্য সাপোর্টে যোগাযোগ করুন।",
    "edit.save": "সংরক্ষণ",
    "edit.saving": "সংরক্ষণ হচ্ছে…",
    "edit.cancel": "বাতিল",
    "edit.saved": "সংরক্ষিত।",
    "chart.revenue_30d": "আয় — শেষ ৩০ দিন",
    "chart.top_products": "শীর্ষ পণ্য — শেষ ৩০ দিন",
    "chart.no_data": "এখনো যথেষ্ট তথ্য নেই।",
    "chart.orders_axis": "অর্ডার",
    "chart.revenue_axis": "আয়",
    "orders.heading": "আমার পণ্যের অর্ডার",
    "orders.empty": "এখনো কোনো অর্ডার নেই।",
    "orders.col.code": "অর্ডার কোড",
    "orders.col.status": "স্ট্যাটাস",
    "orders.col.total": "মোট",
    "orders.col.placed": "প্লেস হয়েছে",
    "payouts.heading": "পেআউট",
    "payouts.empty": "কোনো পেআউট রেকর্ড নেই।",
    "payouts.col.period": "সময়সীমা",
    "payouts.col.gross": "গ্রস",
    "payouts.col.commission": "কমিশন",
    "payouts.col.return_debit": "রিটার্ন ডেবিট",
    "payouts.col.net": "নেট",
    "payouts.col.status": "স্ট্যাটাস",
    "payouts.col.paid_at": "পরিশোধ",
    "common.loading": "লোড হচ্ছে…",
    "common.error": "লোড ব্যর্থ",
    "common.retry": "আবার চেষ্টা",
  },
};

const subs = new Set<() => void>();

function getStored(): Locale {
  try {
    const v = localStorage.getItem(LOCALE_KEY);
    if (v === "bn" || v === "en") return v;
  } catch {
    /* ignore */
  }
  return "en";
}

let active: Locale = getStored();

export function getLocale(): Locale {
  return active;
}

export function setLocale(l: Locale): void {
  active = l;
  try {
    localStorage.setItem(LOCALE_KEY, l);
  } catch {
    /* ignore */
  }
  document.documentElement.lang = l;
  subs.forEach((cb) => cb());
}

export function t(key: string, vars?: Record<string, string | number>): string {
  let out = STRINGS[active]?.[key] ?? STRINGS.en[key] ?? key;
  if (vars) {
    Object.entries(vars).forEach(([k, v]) => {
      out = out.replace(`{${k}}`, String(v));
    });
  }
  return out;
}

export function useLocale() {
  const [, force] = useState(0);
  useEffect(() => {
    const cb = () => force((x) => x + 1);
    subs.add(cb);
    return () => {
      subs.delete(cb);
    };
  }, []);
  return { locale: active, setLocale, t };
}
