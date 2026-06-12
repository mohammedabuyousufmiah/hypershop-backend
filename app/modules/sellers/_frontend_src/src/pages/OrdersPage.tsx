import useSWR from "swr";
import { swrFetcher } from "../api/client";
import { useLocale } from "../i18n";

interface Order {
  id: string;
  code: string;
  status: string;
  grand_total: string;
  placed_at: string;
}

const STATUS_COLOR: Record<string, string> = {
  completed: "bg-green-100 text-green-800",
  out_for_delivery: "bg-blue-100 text-blue-800",
  packing: "bg-yellow-100 text-yellow-800",
  approved: "bg-yellow-100 text-yellow-800",
  stock_reserved: "bg-yellow-100 text-yellow-800",
  payment_confirmed: "bg-yellow-100 text-yellow-800",
  cancelled: "bg-red-100 text-red-700",
  pending_payment: "bg-slate-100 text-slate-600",
};

export default function OrdersPage() {
  const { t } = useLocale();
  const { data, isLoading, error } = useSWR<Order[]>(
    "/api/v1/seller/me/orders?limit=100",
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
      <h1 className="text-xl font-semibold mb-4">{t("orders.heading")}</h1>
      {!data || data.length === 0 ? (
        <div className="card p-8 text-center text-slate-500">
          {t("orders.empty")}
        </div>
      ) : (
        <div className="card overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-600 border-b border-slate-200">
              <tr>
                <th className="text-left px-4 py-2 font-medium">
                  {t("orders.col.code")}
                </th>
                <th className="text-left px-4 py-2 font-medium">
                  {t("orders.col.status")}
                </th>
                <th className="text-right px-4 py-2 font-medium">
                  {t("orders.col.total")}
                </th>
                <th className="text-left px-4 py-2 font-medium">
                  {t("orders.col.placed")}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.map((o) => (
                <tr key={o.id} className="hover:bg-slate-50">
                  <td className="px-4 py-2.5 font-mono text-xs">{o.code}</td>
                  <td className="px-4 py-2.5">
                    <span
                      className={`badge ${STATUS_COLOR[o.status] ?? "bg-slate-100 text-slate-600"}`}
                    >
                      {o.status}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums">
                    ৳ {Number(o.grand_total).toLocaleString()}
                  </td>
                  <td className="px-4 py-2.5 text-slate-500 text-xs">
                    {new Date(o.placed_at).toLocaleString()}
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
