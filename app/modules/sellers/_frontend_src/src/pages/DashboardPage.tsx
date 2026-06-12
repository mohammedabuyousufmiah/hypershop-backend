import useSWR from "swr";
import { swrFetcher } from "../api/client";
import { useLocale } from "../i18n";
import {
  RevenueChart,
  TopProductsChart,
  type TimeseriesPoint,
  type TopProduct,
} from "../components/Charts";

interface DashboardData {
  seller_id: string;
  window_days: number;
  total_products: number;
  active_products: number;
  recent_orders: number;
  recent_revenue: string;
  pending_payouts_total: string;
}

function Tile({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div className="card p-5">
      <p className="text-xs text-slate-500 uppercase tracking-wide">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-slate-900">{value}</p>
      {hint && <p className="mt-1 text-xs text-slate-400">{hint}</p>}
    </div>
  );
}

export default function DashboardPage() {
  const { t } = useLocale();
  const { data, error, isLoading } = useSWR<DashboardData>(
    "/api/v1/seller/me/dashboard?days=30",
    swrFetcher,
    { refreshInterval: 60_000 },
  );
  const { data: tsData } = useSWR<TimeseriesPoint[]>(
    "/api/v1/seller/me/orders/timeseries?days=30",
    swrFetcher,
    { refreshInterval: 60_000 },
  );
  const { data: topData } = useSWR<TopProduct[]>(
    "/api/v1/seller/me/top-products?days=30&limit=5",
    swrFetcher,
    { refreshInterval: 60_000 },
  );

  if (isLoading) return <p className="text-slate-500">{t("common.loading")}</p>;
  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded">
        {t("common.error")}: {String((error as Error).message)}
      </div>
    );
  }
  if (!data) return null;
  return (
    <div>
      <h1 className="text-xl font-semibold mb-1">{t("nav.dashboard")}</h1>
      <p className="text-xs text-slate-500 mb-6">
        {t("dashboard.window", { days: data.window_days })}
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <Tile
          label={t("dashboard.total_products")}
          value={data.total_products}
          hint={`${data.active_products} ${t("dashboard.active_products").toLowerCase()}`}
        />
        <Tile label={t("dashboard.recent_orders")} value={data.recent_orders} />
        <Tile
          label={t("dashboard.recent_revenue")}
          value={`৳ ${Number(data.recent_revenue).toLocaleString()}`}
        />
        <Tile
          label={t("dashboard.pending_payouts")}
          value={`৳ ${Number(data.pending_payouts_total).toLocaleString()}`}
        />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="card p-5 lg:col-span-2">
          <h2 className="text-sm font-medium text-slate-700 mb-3">
            {t("chart.revenue_30d")}
          </h2>
          <RevenueChart data={tsData ?? []} />
        </div>
        <div className="card p-5">
          <h2 className="text-sm font-medium text-slate-700 mb-3">
            {t("chart.top_products")}
          </h2>
          <TopProductsChart data={topData ?? []} />
        </div>
      </div>
    </div>
  );
}
