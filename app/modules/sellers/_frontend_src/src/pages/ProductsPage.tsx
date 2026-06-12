import { Link } from "react-router-dom";
import useSWR from "swr";
import { swrFetcher } from "../api/client";
import { useLocale } from "../i18n";

interface Product {
  id: string;
  name: string;
  slug: string;
  status: string;
  created_at: string;
}

export default function ProductsPage() {
  const { t } = useLocale();
  const { data, isLoading, error } = useSWR<Product[]>(
    "/api/v1/seller/me/products?limit=100",
    swrFetcher,
  );
  if (isLoading) return <p className="text-slate-500">{t("common.loading")}</p>;
  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded">
        {t("common.error")}: {String((error as Error).message)}
      </div>
    );
  }
  return (
    <div>
      <h1 className="text-xl font-semibold mb-4">{t("products.heading")}</h1>
      {!data || data.length === 0 ? (
        <div className="card p-8 text-center text-slate-500">
          {t("products.empty")}
        </div>
      ) : (
        <div className="card overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-600 border-b border-slate-200">
              <tr>
                <th className="text-left px-4 py-2 font-medium">
                  {t("products.col.name")}
                </th>
                <th className="text-left px-4 py-2 font-medium">
                  {t("products.col.slug")}
                </th>
                <th className="text-left px-4 py-2 font-medium">
                  {t("products.col.status")}
                </th>
                <th className="text-left px-4 py-2 font-medium">
                  {t("products.col.created")}
                </th>
                <th className="text-right px-4 py-2 font-medium">
                  {t("products.col.actions")}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.map((p) => (
                <tr key={p.id} className="hover:bg-slate-50">
                  <td className="px-4 py-2.5">
                    <Link
                      to={`/products/${p.id}`}
                      className="text-brand-600 hover:underline"
                    >
                      {p.name}
                    </Link>
                  </td>
                  <td className="px-4 py-2.5 text-slate-500 font-mono text-xs">
                    {p.slug}
                  </td>
                  <td className="px-4 py-2.5">
                    <span
                      className={`badge ${
                        p.status === "active"
                          ? "bg-green-100 text-green-800"
                          : "bg-slate-100 text-slate-600"
                      }`}
                    >
                      {p.status}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-slate-500 text-xs">
                    {new Date(p.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <Link
                      to={`/products/${p.id}`}
                      className="text-xs text-brand-500 hover:underline"
                    >
                      {t("products.action.edit")} →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
