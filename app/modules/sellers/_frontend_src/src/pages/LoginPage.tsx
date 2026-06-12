import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../api/auth";
import { useLocale } from "../i18n";

export default function LoginPage() {
  const { login } = useAuth();
  const { locale, setLocale, t } = useLocale();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      navigate("/", { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError(t("login.invalid"));
      } else {
        setError(typeof (err as ApiError).detail === "string"
          ? String((err as ApiError).detail)
          : "Login failed");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-brand-50 via-slate-50 to-brand-100 p-4">
      <div className="card w-full max-w-md p-8">
        <div className="flex items-center gap-2 mb-6">
          <div className="h-10 w-10 rounded-lg bg-brand-500 text-white grid place-items-center font-bold">
            HS
          </div>
          <div className="flex-1">
            <h1 className="text-xl font-semibold">{t("app.title")}</h1>
            <p className="text-xs text-slate-500">{t("login.heading")}</p>
          </div>
          <select
            value={locale}
            onChange={(e) => setLocale(e.target.value as "en" | "bn")}
            className="border border-slate-300 rounded px-2 py-1 text-xs bg-white"
            aria-label="Language"
          >
            <option value="en">English</option>
            <option value="bn">বাংলা</option>
          </select>
        </div>
        <form className="space-y-4" onSubmit={onSubmit}>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              {t("login.email")}
            </label>
            <input
              autoFocus
              autoComplete="email"
              required
              type="email"
              className="input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              {t("login.password")}
            </label>
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
            <div className="bg-red-50 border border-red-200 text-red-700 px-3 py-2 rounded text-sm">
              {error}
            </div>
          )}
          <button type="submit" disabled={submitting} className="btn-primary w-full">
            {submitting ? t("login.submitting") : t("login.submit")}
          </button>
        </form>
      </div>
    </div>
  );
}
