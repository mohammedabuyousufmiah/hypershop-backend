import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { useLocale } from "../i18n";

export default function LoginPage() {
  const { login, mustChange } = useAuth();
  const { locale, setLocale, t } = useLocale();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
      // mustChange is updated synchronously by AuthContext.login
      navigate(mustChange ? "/change-password" : "/inbox", { replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        setError(typeof err.detail === "string" ? err.detail : "Login failed");
      } else {
        setError("Network error");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-full flex items-center justify-center bg-gradient-to-br from-brand-50 via-slate-50 to-brand-100 p-4">
      <div className="card w-full max-w-md p-8">
        <div className="flex items-center gap-2 mb-6">
          <div className="h-10 w-10 rounded-lg bg-brand-500 text-white grid place-items-center font-bold">
            CC
          </div>
          <div>
            <h1 className="text-xl font-semibold">{t("app.title")}</h1>
            <p className="text-xs text-slate-500">{t("login.heading")}</p>
          </div>
          <select
            value={locale}
            onChange={(e) => setLocale(e.target.value as "en" | "bn")}
            className="ml-auto border border-slate-300 rounded px-2 py-1 text-xs bg-white"
            aria-label="Language"
          >
            <option value="en">English</option>
            <option value="bn">বাংলা</option>
          </select>
        </div>

        <form className="space-y-4" onSubmit={onSubmit}>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">{t("login.email_label")}</label>
            <input
              autoFocus
              autoComplete="username"
              required
              className="input"
              placeholder={t("login.email_placeholder")}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">{t("login.password_label")}</label>
            <input
              type="password"
              autoComplete="current-password"
              required
              className="input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {error && (
            <div className="badge-error w-full justify-center py-2 text-center">{error}</div>
          )}
          <button type="submit" disabled={submitting} className="btn-primary w-full py-2.5">
            {submitting ? t("common.loading") : t("login.button")}
          </button>
        </form>

        <p className="mt-6 text-xs text-slate-500 text-center">
          By signing in you agree to the customer care policy. Sessions are audit-logged.
        </p>
      </div>
    </div>
  );
}
