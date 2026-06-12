import useSWR from "swr";
import { swrFetcher } from "../api/client";
import { useLocale } from "../i18n";

interface Payout {
  id: string;
  period_start: string;
  period_end: string;
  gross_amount: string;
  commission_deducted: string;
  return_debit: string;
  net_amount: string;
  currency: string;
  status: string;
  payment_method: string | null;
  payment_reference: string | null;
  paid_at: string | null;
  created_at: string;
}

const STATUS_COLOR: Record<string, string> = {
  paid: "bg-green-100 text-green-800",
  pending: "bg-yellow-100 text-yellow-800",
  approved: "bg-blue-100 text-blue-800",
  failed: "bg-red-100 text-red-700",
  cancelled: "bg-slate-100 text-slate-600",
};

export default function PayoutsPage() {
  const { t } = useLocale();
  const { data, isLoading, error } = useSWR<Payout[]>(
    "/api/v1/seller/me/payouts?limit=100",
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
      <h1 className="text-xl font-semibold mb-4">{t("payouts.heading")}</h1>
      {!data || data.length === 0 ? (
        <div className="card p-8 text-center text-slate-500">
          {t("payouts.empty")}
        </div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-600 border-b border-slate-200">
              <tr>
                <th className="text-left px-4 py-2 font-medium">
                  {t("payouts.col.period")}
                </th>
                <th className="text-right px-4 py-2 font-medium">
                  {t("payouts.col.gross")}
                </th>
                <th className="text-right px-4 py-2 font-medium">
                  {t("payouts.col.commission")}
                </th>
                <th className="text-right px-4 py-2 font-medium">
                  {t("payouts.col.return_debit")}
                </th>
                <th className="text-right px-4 py-2 font-medium">
                  {t("payouts.col.net")}
                </th>
                <th className="text-left px-4 py-2 font-medium">
                  {t("payouts.col.status")}
                </th>
                <th className="text-left px-4 py-2 font-medium">
                  {t("payouts.col.paid_at")}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.map((p) => (
                <tr key={p.id} className="hover:bg-slate-50">
                  <td className="px-4 py-2.5 text-xs">
                    {new Date(p.period_start).toLocaleDateString()} →{" "}
                    {new Date(p.period_end).toLocaleDateString()}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums">
                    {p.currency} {Number(p.gross_amount).toLocaleString()}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-slate-500">
                    − {Number(p.commission_deducted).toLocaleString()}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-slate-500">
                    − {Number(p.return_debit).toLocaleString()}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums font-semibold">
                    {p.currency} {Number(p.net_amount).toLocaleString()}
                  </td>
                  <td className="px-4 py-2.5">
                    <span
                      className={`badge ${STATUS_COLOR[p.status] ?? "bg-slate-100 text-slate-600"}`}
                    >
                      {p.status}
                    </span>
                    {p.payment_reference && (
                      <p className="text-xs text-slate-400 mt-0.5 font-mono">
                        {p.payment_reference}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-slate-500 text-xs">
                    {p.paid_at ? new Date(p.paid_at).toLocaleString() : "—"}
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
