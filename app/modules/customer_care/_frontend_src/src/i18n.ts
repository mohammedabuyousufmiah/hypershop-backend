/**
 * Minimal i18n harness for the Hypershop Customer-Care agent PWA.
 *
 * Two locales:
 *   - en (English) — default
 *   - bn (Bangla)  — primary for Bangladesh ops
 *
 * Persisted in localStorage under `agent-inbox.locale`. Components
 * call `t('key')` which looks up the active locale's table and
 * falls back to English if missing. A `setLocale(...)` setter
 * triggers a re-render via the `useLocale()` React hook.
 *
 * String coverage focused on the agent dashboard surface — login,
 * inbox header, conversation list, message composer, reports tabs,
 * KB management. Bangla translations reviewed by a native speaker
 * before launch.
 */
import { useEffect, useState } from "react";

type Locale = "en" | "bn";

const LOCALE_KEY = "agent-inbox.locale";
const DEFAULT_LOCALE: Locale = "en";

const STRINGS: Record<Locale, Record<string, string>> = {
  en: {
    "app.title": "Agent Inbox · Customer Care",
    "app.subtitle": "Hypershop",

    "login.heading": "Sign in to your dashboard",
    "login.email_label": "Email",
    "login.email_placeholder": "you@hypershop.com.bd",
    "login.password_label": "Password",
    "login.button": "Sign in",
    "login.error": "Invalid email or password",
    "login.must_change_password": "You must change your password before continuing.",

    "header.signed_in_as": "Signed in as",
    "header.logout": "Sign out",
    "header.status_online": "Online",
    "header.status_busy": "Busy",
    "header.status_away": "Away",
    "header.status_offline": "Offline",

    "inbox.tab_mine": "My inbox",
    "inbox.tab_unassigned": "Unassigned",
    "inbox.tab_all": "All",
    "inbox.empty": "No conversations to show.",
    "inbox.unread": "unread",
    "inbox.handover_required": "Needs human handover",
    "inbox.sla_breached": "SLA breached",

    "conversation.send_placeholder": "Type a message…",
    "conversation.send": "Send",
    "conversation.resolve": "Mark resolved",
    "conversation.reopen": "Reopen",
    "conversation.transfer": "Transfer",
    "conversation.handover": "Hand over",
    "conversation.csat_send": "Send CSAT survey",
    "conversation.export": "Export",
    "conversation.suggest": "Suggest replies",
    "conversation.translate": "Translate",
    "conversation.summary_loading": "Generating summary…",
    "conversation.summary_heading": "AI summary",

    "messages.customer": "Customer",
    "messages.agent": "Agent",
    "messages.ai": "AI",
    "messages.system": "System",
    "messages.image_label": "Image",
    "messages.voice_label": "Voice note",

    "reports.tab_dashboard": "Dashboard",
    "reports.tab_agent_perf": "Agent performance",
    "reports.tab_sla": "SLA",
    "reports.tab_csat": "CSAT",
    "reports.tab_topics": "Topics",
    "reports.tab_sentiment": "Sentiment",
    "reports.tab_anomaly": "Anomaly",
    "reports.window_label": "Window",
    "reports.day_count_label": "days",

    "kb.tab_documents": "Knowledge base",
    "kb.add_document": "Add document",
    "kb.bulk_csv": "Bulk CSV import",
    "kb.title_label": "Title",
    "kb.body_label": "Body",
    "kb.language_label": "Language",
    "kb.save": "Save",
    "kb.delete": "Delete",
    "kb.search_placeholder": "Search knowledge base…",

    "saved_replies.title": "Saved replies",
    "saved_replies.add": "Add quick reply",
    "saved_replies.shared": "Shared with team",

    "common.cancel": "Cancel",
    "common.save": "Save",
    "common.loading": "Loading…",
    "common.error": "Something went wrong.",
    "common.retry": "Retry",
    "common.yes": "Yes",
    "common.no": "No",
  },
  bn: {
    "app.title": "এজেন্ট ইনবক্স · কাস্টমার কেয়ার",
    "app.subtitle": "Hypershop",

    "login.heading": "ড্যাশবোর্ডে সাইন ইন করুন",
    "login.email_label": "ইমেইল",
    "login.email_placeholder": "you@hypershop.com.bd",
    "login.password_label": "পাসওয়ার্ড",
    "login.button": "সাইন ইন",
    "login.error": "ভুল ইমেইল অথবা পাসওয়ার্ড",
    "login.must_change_password": "চালিয়ে যাওয়ার আগে পাসওয়ার্ড পরিবর্তন করতে হবে।",

    "header.signed_in_as": "সাইন ইন করেছেন",
    "header.logout": "সাইন আউট",
    "header.status_online": "অনলাইন",
    "header.status_busy": "ব্যস্ত",
    "header.status_away": "অনুপস্থিত",
    "header.status_offline": "অফলাইন",

    "inbox.tab_mine": "আমার ইনবক্স",
    "inbox.tab_unassigned": "বরাদ্দ ছাড়া",
    "inbox.tab_all": "সব",
    "inbox.empty": "কোনো কনভারসেশন নেই।",
    "inbox.unread": "অপঠিত",
    "inbox.handover_required": "মানব এজেন্ট প্রয়োজন",
    "inbox.sla_breached": "SLA ভঙ্গ হয়েছে",

    "conversation.send_placeholder": "মেসেজ লিখুন…",
    "conversation.send": "পাঠান",
    "conversation.resolve": "সমাধান চিহ্নিত করুন",
    "conversation.reopen": "পুনরায় খুলুন",
    "conversation.transfer": "ট্রান্সফার",
    "conversation.handover": "হ্যান্ডওভার",
    "conversation.csat_send": "CSAT সার্ভে পাঠান",
    "conversation.export": "এক্সপোর্ট",
    "conversation.suggest": "AI সাজেশন",
    "conversation.translate": "অনুবাদ",
    "conversation.summary_loading": "সারাংশ তৈরি হচ্ছে…",
    "conversation.summary_heading": "AI সারাংশ",

    "messages.customer": "কাস্টমার",
    "messages.agent": "এজেন্ট",
    "messages.ai": "AI",
    "messages.system": "সিস্টেম",
    "messages.image_label": "ছবি",
    "messages.voice_label": "ভয়েস মেসেজ",

    "reports.tab_dashboard": "ড্যাশবোর্ড",
    "reports.tab_agent_perf": "এজেন্ট পারফরম্যান্স",
    "reports.tab_sla": "SLA",
    "reports.tab_csat": "CSAT",
    "reports.tab_topics": "টপিক",
    "reports.tab_sentiment": "সেন্টিমেন্ট",
    "reports.tab_anomaly": "অ্যানোমালি",
    "reports.window_label": "সময়সীমা",
    "reports.day_count_label": "দিন",

    "kb.tab_documents": "নলেজ বেস",
    "kb.add_document": "ডকুমেন্ট যোগ করুন",
    "kb.bulk_csv": "CSV বাল্ক ইম্পোর্ট",
    "kb.title_label": "শিরোনাম",
    "kb.body_label": "বিষয়বস্তু",
    "kb.language_label": "ভাষা",
    "kb.save": "সংরক্ষণ",
    "kb.delete": "মুছুন",
    "kb.search_placeholder": "নলেজ বেসে খুঁজুন…",

    "saved_replies.title": "সংরক্ষিত উত্তর",
    "saved_replies.add": "কুইক রিপ্লাই যোগ করুন",
    "saved_replies.shared": "টিমের সাথে শেয়ার করা",

    "common.cancel": "বাতিল",
    "common.save": "সংরক্ষণ",
    "common.loading": "লোড হচ্ছে…",
    "common.error": "কিছু ভুল হয়েছে।",
    "common.retry": "আবার চেষ্টা",
    "common.yes": "হ্যাঁ",
    "common.no": "না",
  },
};

const subscribers = new Set<() => void>();

function getStoredLocale(): Locale {
  try {
    const v = localStorage.getItem(LOCALE_KEY);
    if (v === "bn" || v === "en") return v;
  } catch {
    /* localStorage unavailable (SSR / private mode) */
  }
  return DEFAULT_LOCALE;
}

let activeLocale: Locale = getStoredLocale();

export function getLocale(): Locale {
  return activeLocale;
}

export function setLocale(locale: Locale): void {
  activeLocale = locale;
  try {
    localStorage.setItem(LOCALE_KEY, locale);
  } catch {
    /* ignore */
  }
  document.documentElement.lang = locale === "bn" ? "bn" : "en";
  subscribers.forEach((cb) => cb());
}

export function t(key: string): string {
  const table = STRINGS[activeLocale] || STRINGS.en;
  return table[key] ?? STRINGS.en[key] ?? key;
}

/**
 * React hook — re-renders the consuming component on locale change.
 * Usage:
 *   const { locale, setLocale, t } = useLocale();
 *   return <h1>{t("app.title")}</h1>;
 */
export function useLocale() {
  const [, force] = useState(0);
  useEffect(() => {
    const cb = () => force((x) => x + 1);
    subscribers.add(cb);
    return () => {
      subscribers.delete(cb);
    };
  }, []);
  return {
    locale: activeLocale,
    setLocale,
    t,
  };
}
