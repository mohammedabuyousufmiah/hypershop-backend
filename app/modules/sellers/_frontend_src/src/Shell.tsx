import { NavLink, useLocation } from "react-router-dom";
import { useAuth } from "./api/auth";
import { useLocale } from "./i18n";

export function Shell({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();
  const { locale, setLocale, t } = useLocale();
  const loc = useLocation();

  const navItem = (to: string, label: string) => (
    <NavLink
      to={to}
      end={to === "/"}
      className={({ isActive }) =>
        `block px-3 py-2 rounded-md text-sm font-medium transition ${
          isActive
            ? "bg-brand-500 text-white"
            : "text-slate-700 hover:bg-slate-100"
        }`
      }
    >
      {label}
    </NavLink>
  );

  return (
    <div className="min-h-screen flex">
      <aside className="w-56 border-r border-slate-200 bg-white p-4 hidden sm:block">
        <div className="flex items-center gap-2 mb-6">
          <div className="h-9 w-9 rounded-md bg-brand-500 text-white grid place-items-center font-bold text-sm">
            HS
          </div>
          <div className="text-sm font-semibold leading-tight">
            {t("app.title")}
          </div>
        </div>
        <nav className="space-y-1">
          {navItem("/", t("nav.dashboard"))}
          {navItem("/products", t("nav.products"))}
          {navItem("/orders", t("nav.orders"))}
          {navItem("/payouts", t("nav.payouts"))}
        </nav>
        <div className="mt-8 border-t border-slate-200 pt-4">
          <p className="text-xs text-slate-500 truncate" title={user?.email ?? ""}>
            {user?.email ?? "—"}
          </p>
          <select
            value={locale}
            onChange={(e) => setLocale(e.target.value as "en" | "bn")}
            className="mt-2 w-full border border-slate-300 rounded px-2 py-1 text-xs bg-white"
            aria-label="Language"
          >
            <option value="en">English</option>
            <option value="bn">বাংলা</option>
          </select>
          <button
            type="button"
            className="mt-3 w-full text-xs text-slate-600 hover:text-slate-900 text-left"
            onClick={() => void logout()}
          >
            {t("nav.logout")}
          </button>
        </div>
      </aside>
      <main className="flex-1 p-6 overflow-x-auto">
        {/* Mobile tab bar */}
        <nav className="sm:hidden mb-4 flex gap-1 overflow-x-auto">
          {[
            ["/", t("nav.dashboard")],
            ["/products", t("nav.products")],
            ["/orders", t("nav.orders")],
            ["/payouts", t("nav.payouts")],
          ].map(([to, label]) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                `whitespace-nowrap px-3 py-1.5 rounded-md text-xs ${
                  isActive
                    ? "bg-brand-500 text-white"
                    : "bg-white border border-slate-200 text-slate-700"
                }`
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>
        <div key={loc.pathname}>{children}</div>
      </main>
    </div>
  );
}
