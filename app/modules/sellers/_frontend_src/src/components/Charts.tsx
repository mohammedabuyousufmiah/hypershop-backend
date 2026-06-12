/**
 * Tiny inline-SVG chart components for the seller dashboard.
 * No chart library — keeps the bundle small.
 */
import { useLocale } from "../i18n";

export interface TimeseriesPoint {
  day: string; // ISO date (yyyy-mm-dd)
  orders: number;
  revenue: string | number;
}

export interface TopProduct {
  product_id: string;
  name: string;
  units: number | string;
  revenue: number | string;
}

function num(v: number | string | null | undefined): number {
  if (v === null || v === undefined) return 0;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
}

/* --------------------------------------------------------------------- */
/* Revenue-over-time area chart                                          */
/* --------------------------------------------------------------------- */

export function RevenueChart({
  data,
  height = 160,
}: {
  data: TimeseriesPoint[];
  height?: number;
}) {
  const { t } = useLocale();
  if (!data || data.length === 0) {
    return (
      <p className="text-xs text-slate-400 py-8 text-center">
        {t("chart.no_data")}
      </p>
    );
  }
  const w = 600;
  const h = height;
  const padL = 36;
  const padR = 8;
  const padT = 8;
  const padB = 22;
  const innerW = w - padL - padR;
  const innerH = h - padT - padB;

  const revVals = data.map((d) => num(d.revenue));
  const maxRev = Math.max(1, ...revVals);
  const stepX = data.length > 1 ? innerW / (data.length - 1) : 0;

  const points = data.map((d, i) => {
    const x = padL + i * stepX;
    const y = padT + innerH - (revVals[i] / maxRev) * innerH;
    return { x, y, d, rev: revVals[i] };
  });

  const linePath = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`)
    .join(" ");
  const areaPath =
    `M ${padL} ${padT + innerH} ` +
    points
      .map((p) => `L ${p.x.toFixed(1)} ${p.y.toFixed(1)}`)
      .join(" ") +
    ` L ${padL + (data.length - 1) * stepX} ${padT + innerH} Z`;

  // 4 horizontal gridlines
  const gridYs = [0, 0.25, 0.5, 0.75, 1].map((f) => padT + innerH * (1 - f));

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      className="w-full h-auto"
      preserveAspectRatio="none"
    >
      <defs>
        <linearGradient id="rev-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#6366f1" stopOpacity="0.35" />
          <stop offset="100%" stopColor="#6366f1" stopOpacity="0" />
        </linearGradient>
      </defs>
      {gridYs.map((y, i) => (
        <line
          key={i}
          x1={padL}
          x2={w - padR}
          y1={y}
          y2={y}
          stroke="#e2e8f0"
          strokeDasharray="2 3"
          strokeWidth={1}
        />
      ))}
      {[0, 0.5, 1].map((f, i) => {
        const v = maxRev * (1 - f);
        const y = padT + innerH * f;
        return (
          <text
            key={i}
            x={padL - 4}
            y={y + 3}
            fontSize="9"
            textAnchor="end"
            fill="#94a3b8"
            fontFamily="ui-sans-serif, system-ui"
          >
            {Math.round(v).toLocaleString()}
          </text>
        );
      })}
      <path d={areaPath} fill="url(#rev-grad)" />
      <path
        d={linePath}
        fill="none"
        stroke="#6366f1"
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {points.map((p, i) => (
        <circle key={i} cx={p.x} cy={p.y} r={1.8} fill="#6366f1">
          <title>
            {p.d.day}: {p.d.orders} {t("chart.orders_axis")}, ৳{" "}
            {p.rev.toLocaleString()}
          </title>
        </circle>
      ))}
      {/* X-axis tick labels: first, middle, last */}
      {data.length > 0 &&
        [0, Math.floor(data.length / 2), data.length - 1].map((idx, i) => {
          const p = points[idx];
          if (!p) return null;
          const label = p.d.day.slice(5); // mm-dd
          return (
            <text
              key={i}
              x={p.x}
              y={h - 6}
              fontSize="9"
              textAnchor="middle"
              fill="#94a3b8"
              fontFamily="ui-sans-serif, system-ui"
            >
              {label}
            </text>
          );
        })}
    </svg>
  );
}

/* --------------------------------------------------------------------- */
/* Horizontal bar chart for top products                                 */
/* --------------------------------------------------------------------- */

export function TopProductsChart({ data }: { data: TopProduct[] }) {
  const { t } = useLocale();
  if (!data || data.length === 0) {
    return (
      <p className="text-xs text-slate-400 py-8 text-center">
        {t("chart.no_data")}
      </p>
    );
  }
  const maxRev = Math.max(1, ...data.map((d) => num(d.revenue)));
  return (
    <div className="space-y-2">
      {data.map((p) => {
        const rev = num(p.revenue);
        const pct = (rev / maxRev) * 100;
        return (
          <div key={p.product_id}>
            <div className="flex items-baseline justify-between text-xs">
              <span className="truncate pr-3 text-slate-700">{p.name}</span>
              <span className="tabular-nums text-slate-500 whitespace-nowrap">
                ৳ {rev.toLocaleString()}{" "}
                <span className="text-slate-400">
                  ({num(p.units)} {t("chart.orders_axis")})
                </span>
              </span>
            </div>
            <div className="mt-1 h-2 bg-slate-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-brand-500 rounded-full"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
