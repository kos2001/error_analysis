/** 인라인 SVG 차트 프리미티브 — 외부 차트 의존성 없이 대시보드 시각화에 쓴다.
 *
 * 원칙: 색만으로 의미를 전달하지 않는다(항상 값 텍스트 병기), 각 도형에 <title>로
 * 접근 가능한 설명을 붙인다. RelationGraph 가 이미 인라인 SVG로 그리는 패턴을 따른다.
 */

import { PALETTE } from "./theme";

/** 큰 숫자 하나 + 라벨. tone 으로 상태(정상/주의/경고)를 함께 전달한다. */
export function StatTile({ label, value, sub, tone = "neutral", title }: {
  label: string;
  value: string | number;
  sub?: string;
  tone?: "neutral" | "good" | "warn" | "bad";
  title?: string;
}) {
  const color = {
    neutral: "text-slate-800", good: "text-emerald-600",
    warn: "text-amber-600", bad: "text-rose-600",
  }[tone];
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2" title={title}>
      <div className="text-[11px] text-slate-500 leading-tight">{label}</div>
      <div className={`text-xl font-bold leading-tight mt-0.5 ${color}`}>{value}</div>
      {sub && <div className="text-[11px] text-slate-400 leading-tight mt-0.5">{sub}</div>}
    </div>
  );
}

export type BarItem = { label: string; value: number; hint?: string; onClick?: () => void };

/** 라벨 + 수평 막대 + 값. max 미지정 시 최댓값으로 정규화한다. */
export function BarList({ items, max, unit = "", labelW = 112, emptyText = "데이터 없음" }: {
  items: BarItem[];
  max?: number;
  unit?: string;
  labelW?: number;
  emptyText?: string;
}) {
  if (!items.length) return <div className="text-xs text-slate-400 py-2">{emptyText}</div>;
  const top = max ?? Math.max(...items.map((i) => i.value), 1);
  return (
    <div className="space-y-1">
      {items.map((it, i) => {
        const pct = top > 0 ? Math.max(0, Math.min(100, (it.value / top) * 100)) : 0;
        const row = (
          <>
            <span className="text-[11px] text-slate-600 truncate shrink-0" style={{ width: labelW }}
              title={it.label}>{it.label}</span>
            <span className="flex-1 h-3 rounded bg-slate-100 overflow-hidden" aria-hidden="true">
              <span className="block h-full rounded"
                style={{ width: `${pct}%`, background: PALETTE[i % PALETTE.length] }} />
            </span>
            <span className="text-[11px] font-medium text-slate-700 w-14 text-right shrink-0 tabular-nums">
              {it.value}{unit}
            </span>
          </>
        );
        const cls = "flex items-center gap-2 w-full text-left";
        return it.onClick
          ? <button key={it.label + i} onClick={it.onClick} title={it.hint ?? it.label}
              className={`${cls} rounded hover:bg-indigo-50 px-1 -mx-1`}>{row}</button>
          : <div key={it.label + i} title={it.hint ?? it.label} className={cls}>{row}</div>;
      })}
    </div>
  );
}

/** 비율 도넛 + 범례. 값이 0인 항목은 범례에서 제외한다. */
export function Donut({ items, size = 132, thickness = 20, centerLabel, centerValue }: {
  items: { label: string; value: number }[];
  size?: number;
  thickness?: number;
  centerLabel?: string;
  centerValue?: string | number;
}) {
  const data = items.filter((i) => i.value > 0);
  const total = data.reduce((s, i) => s + i.value, 0);
  if (!total) return <div className="text-xs text-slate-400 py-2">데이터 없음</div>;
  const r = (size - thickness) / 2;
  const C = 2 * Math.PI * r;
  // 각 조각의 시작 오프셋을 미리 계산한다 — 렌더 중 누적 변수를 갱신하지 않기 위해.
  const starts = data.reduce<number[]>((acc, _d, i) => {
    acc.push(i === 0 ? 0 : acc[i - 1] + data[i - 1].value / total);
    return acc;
  }, []);
  return (
    <div className="flex items-center gap-4">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img"
        aria-label={data.map((d) => `${d.label} ${d.value}`).join(", ")}>
        <g transform={`translate(${size / 2},${size / 2}) rotate(-90)`}>
          {data.map((d, i) => {
            const frac = d.value / total;
            return (
              <circle key={d.label} r={r} fill="none" strokeWidth={thickness}
                stroke={PALETTE[i % PALETTE.length]} strokeDasharray={`${frac * C} ${C}`}
                strokeDashoffset={-starts[i] * C}>
                <title>{`${d.label}: ${d.value}건 (${Math.round(frac * 100)}%)`}</title>
              </circle>
            );
          })}
        </g>
        {(centerValue !== undefined || centerLabel) && (
          <g>
            <text x={size / 2} y={size / 2 - 1} textAnchor="middle" fontSize={19} fontWeight={700}
              fill="#1e293b">{centerValue ?? total}</text>
            <text x={size / 2} y={size / 2 + 14} textAnchor="middle" fontSize={10}
              fill="#94a3b8">{centerLabel ?? "합계"}</text>
          </g>
        )}
      </svg>
      <ul className="text-[11px] space-y-0.5 min-w-0">
        {data.map((d, i) => (
          <li key={d.label} className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-sm shrink-0"
              style={{ background: PALETTE[i % PALETTE.length] }} aria-hidden="true" />
            <span className="text-slate-600 truncate" title={d.label}>{d.label || "(미분류)"}</span>
            <span className="text-slate-400 tabular-nums shrink-0">
              {d.value} · {Math.round((d.value / total) * 100)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** 0~1 비율 막대(fill rate 등). 임계 미달이면 색으로도 경고. */
export function RateBar({ label, value, warnBelow = 0.95 }: {
  label: string; value: number; warnBelow?: number;
}) {
  const pct = Math.round(value * 1000) / 10;
  const bad = value < warnBelow;
  return (
    <div className="flex items-center gap-2" title={`${label}: ${pct}%`}>
      <span className="text-[11px] text-slate-600 truncate shrink-0 w-36">{label}</span>
      <span className="flex-1 h-2.5 rounded bg-slate-100 overflow-hidden" aria-hidden="true">
        <span className={`block h-full rounded ${bad ? "bg-amber-500" : "bg-emerald-500"}`}
          style={{ width: `${Math.min(100, pct)}%` }} />
      </span>
      <span className={`text-[11px] font-medium w-12 text-right tabular-nums ${bad ? "text-amber-600" : "text-slate-700"}`}>
        {pct}%
      </span>
    </div>
  );
}
