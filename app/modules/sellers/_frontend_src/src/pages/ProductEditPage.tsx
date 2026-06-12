import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import useSWR from "swr";
import { api, ApiError, swrFetcher } from "../api/client";
import { useLocale } from "../i18n";

interface ProductDetail {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  variants: { id: string; sku: string; price: string }[];
}

export default function ProductEditPage() {
  const { id = "" } = useParams();
  const { t } = useLocale();
  const navigate = useNavigate();
  const { data, error, isLoading, mutate } = useSWR<ProductDetail>(
    id ? `/api/v1/seller/me/products/${id}` : null,
    swrFetcher,
  );
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [status, setStatus] = useState<string>("active");
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);
  const [errMsg, setErrMsg] = useState<string | null>(null);

  useEffect(() => {
    if (data) {
      setName(data.name);
      setDescription(data.description ?? "");
      setStatus(data.status);
    }
  }, [data]);

  if (isLoading) return <p className="text-slate-500">{t("common.loading")}</p>;
  if (error || !data) {
    return (
      <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded">
        {t("common.error")}: {error ? String((error as Error).message) : "missing data"}
      </div>
    );
  }

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setErrMsg(null);
    setSavedMsg(null);
    try {
      await api("PATCH", `/api/v1/seller/me/products/${id}`, {
        body: { name, description, status },
      });
      setSavedMsg(t("edit.saved"));
      void mutate();
    } catch (err) {
      const msg = err instanceof ApiError
        ? (typeof err.detail === "string" ? err.detail : `HTTP ${err.status}`)
        : "save failed";
      setErrMsg(String(msg));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="max-w-3xl">
      <button
        type="button"
        onClick={() => navigate("/products")}
        className="text-xs text-brand-500 hover:underline mb-3"
      >
        ← {t("edit.back")}
      </button>
      <h1 className="text-xl font-semibold mb-1">{data.name}</h1>
      <p className="text-xs text-slate-500 mb-6 font-mono">{data.slug}</p>

      <form onSubmit={onSubmit} className="card p-6 space-y-4">
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">
            {t("edit.name")}
          </label>
          <input
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">
            {t("edit.description")}
          </label>
          <textarea
            className="input min-h-[120px] resize-y"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={20000}
          />
          <p className="text-xs text-slate-400 mt-1">
            {description.length} / 20,000
          </p>
        </div>
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">
            {t("edit.status")}
          </label>
          <select
            className="input"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="active">{t("edit.status_active")}</option>
            <option value="inactive">{t("edit.status_inactive")}</option>
            <option value="draft">{t("edit.status_draft")}</option>
          </select>
        </div>

        <div className="pt-2 border-t border-slate-100">
          <h3 className="text-sm font-medium text-slate-700 mb-2">
            {t("edit.variants")} ({data.variants.length})
          </h3>
          <div className="space-y-1">
            {data.variants.map((v) => (
              <div
                key={v.id}
                className="flex items-center justify-between bg-slate-50 px-3 py-2 rounded text-xs"
              >
                <span className="font-mono text-slate-600">{v.sku}</span>
                <span className="tabular-nums">৳ {Number(v.price).toLocaleString()}</span>
              </div>
            ))}
            {data.variants.length === 0 && (
              <p className="text-xs text-slate-400">{t("edit.no_variants")}</p>
            )}
          </div>
          <p className="text-xs text-slate-400 mt-2">
            {t("edit.variants_help")}
          </p>
        </div>

        {savedMsg && (
          <div className="bg-green-50 border border-green-200 text-green-700 px-3 py-2 rounded text-sm">
            {savedMsg}
          </div>
        )}
        {errMsg && (
          <div className="bg-red-50 border border-red-200 text-red-700 px-3 py-2 rounded text-sm">
            {errMsg}
          </div>
        )}

        <div className="flex gap-2">
          <button type="submit" disabled={saving} className="btn-primary">
            {saving ? t("edit.saving") : t("edit.save")}
          </button>
          <button
            type="button"
            onClick={() => navigate("/products")}
            className="btn-secondary"
          >
            {t("edit.cancel")}
          </button>
        </div>
      </form>
    </div>
  );
}
