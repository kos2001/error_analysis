/** 이슈 관계 그래프 — center 중심 인라인 SVG.
 *
 * FailureAnalysis.tsx 가 700줄까지 커져서 분리했다. 동작은 그대로다:
 * rerank 관련도를 노드 크기·선 굵기로 인코딩하고, 관련도순 거리로 초기 배치한 뒤
 * 충돌 완화(겹침 제거)를 돌린다. 의존성 없는 인라인 SVG.
 */

import { STATUS_HEX } from "./theme";

export type GNode = {
  id: string; label: string; status: string; category: string; chip: string;
  template: string; center: boolean; relevance: number | null;
};
export type GEdge = { source: string; target: string; weight: number; same_template: boolean; rerank: number | null };
export type GraphData = { center: string | null; nodes: GNode[]; edges: GEdge[]; has_rerank?: boolean };

export default function RelationGraph({ data, onSelect }: { data: GraphData; onSelect: (k: string) => void }) {
  const W = 360, H = 360, CX = W / 2, CY = H / 2;
  const RNEAR = 78, RFAR = 150, M = 36;            // M: 라벨용 가장자리 여백
  const center = data.nodes.find((n) => n.center) ?? data.nodes[0];
  const neigh = data.nodes.filter((n) => n !== center); // 백엔드에서 관련도 내림차순 정렬됨
  const cid = center?.id;
  const nodeR = (n: GNode) => (n.center ? 16 : 6 + (n.relevance ?? 0) * 8);

  // 1) 초기 배치: 관련도↑ → center에 가깝게, 각도는 균등
  const pos: Record<string, { x: number; y: number; r: number }> = {};
  if (center) pos[center.id] = { x: CX, y: CY, r: 16 };
  neigh.forEach((n, i) => {
    const a = (2 * Math.PI * i) / Math.max(neigh.length, 1) - Math.PI / 2;
    const rad = RFAR - (n.relevance ?? 0) * (RFAR - RNEAR);
    pos[n.id] = { x: CX + rad * Math.cos(a), y: CY + rad * Math.sin(a), r: nodeR(n) };
  });
  // 2) 충돌 완화: 라벨 폭까지 고려한 최소 간격 확보(center는 고정). 결정론적.
  const LABEL = 38;                                 // 'LSI-247' 라벨이 차지하는 반경 여유
  for (let it = 0; it < 80; it++) {
    for (let i = 0; i < neigh.length; i++) {
      const A = pos[neigh[i].id];
      // center에서 밀어내기
      const dx = A.x - CX, dy = A.y - CY, d = Math.hypot(dx, dy) || 0.01;
      const minC = A.r + 16 + 16;
      if (d < minC) { A.x = CX + (dx / d) * minC; A.y = CY + (dy / d) * minC; }
      // 다른 이웃과 분리
      for (let j = i + 1; j < neigh.length; j++) {
        const B = pos[neigh[j].id];
        const ex = A.x - B.x, ey = A.y - B.y, e = Math.hypot(ex, ey) || 0.01;
        const minD = A.r + B.r + LABEL;
        if (e < minD) {
          const push = (minD - e) / 2;
          A.x += (ex / e) * push; A.y += (ey / e) * push;
          B.x -= (ex / e) * push; B.y -= (ey / e) * push;
        }
      }
      A.x = Math.max(M, Math.min(W - M, A.x));
      A.y = Math.max(M, Math.min(H - M, A.y));
    }
  }
  const shown = data.edges.filter((e) => e.source === cid || e.target === cid || e.same_template);
  const relColor = (rel: number) => `rgb(${Math.round(99 + (1 - rel) * 130)},${Math.round(102 + (1 - rel) * 110)},241)`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 360 }}>
      <defs>
        <filter id="nodeShadow" x="-50%" y="-50%" width="200%" height="200%">
          <feDropShadow dx="0" dy="1" stdDeviation="1.2" floodColor="#1e293b" floodOpacity="0.22" />
        </filter>
      </defs>
      {/* 엣지 */}
      {shown.map((e, i) => {
        const a = pos[e.source], b = pos[e.target]; if (!a || !b) return null;
        const isCenter = e.rerank != null;
        const rel = e.rerank ?? 0;
        return (
          <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
            stroke={isCenter ? relColor(rel) : "#cbd5e1"}
            strokeWidth={isCenter ? 1.2 + rel * 5 : 1}
            strokeOpacity={isCenter ? 0.3 + rel * 0.55 : 0.4}
            strokeLinecap="round">
            {isCenter && <title>{`${e.source} ↔ ${e.target}\nrerank 관련도 ${Math.round(rel * 100)}%`}</title>}
          </line>
        );
      })}
      {/* 노드 */}
      {data.nodes.map((n) => {
        const p = pos[n.id]; if (!p) return null;
        const sib = !n.center && n.template === center?.template; // 같은 근본원인 형제
        return (
          <g key={n.id} transform={`translate(${p.x},${p.y})`} className="cursor-pointer"
            onClick={() => onSelect(n.id)}>
            <title>{`${n.id} · ${n.status}${n.relevance != null ? ` · 관련도 ${Math.round(n.relevance * 100)}%` : ""}\n${n.label}`}</title>
            {n.center && <circle r={p.r + 5} fill="none" stroke="#6366f1" strokeWidth={2} strokeOpacity={0.5} />}
            {sib && <circle r={p.r + 3} fill="none" stroke="#6366f1" strokeWidth={1.5} strokeDasharray="2 2" />}
            <circle r={p.r} fill={STATUS_HEX[n.status] ?? "#94a3b8"}
              stroke="#fff" strokeWidth={1.8} filter="url(#nodeShadow)" />
            {/* 노드 아래: Jira 이슈 ID (전체). 흰색 외곽선(halo)으로 엣지/노드 위에서도 또렷하게 */}
            <text y={p.r + 13} textAnchor="middle" fontSize={11} fontWeight={700}
              fill="#0f172a" stroke="#ffffff" strokeWidth={3.5} paintOrder="stroke"
              strokeLinejoin="round" className="font-mono select-none">{n.id}</text>
            {!n.center && n.relevance != null && (
              <text y={p.r + 24} textAnchor="middle" fontSize={9} fontWeight={700}
                fill="#4f46e5" stroke="#ffffff" strokeWidth={3} paintOrder="stroke"
                strokeLinejoin="round" className="select-none">{Math.round(n.relevance * 100)}%</text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
