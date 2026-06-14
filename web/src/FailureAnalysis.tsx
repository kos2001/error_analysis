import { useEffect, useMemo, useState, type PointerEvent as ReactPointerEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const API = (import.meta as any).env?.VITE_API ?? "http://127.0.0.1:8001";

type Issue = {
  key: string; summary: string; status: string; chip: string;
  category: string; priority: string; severity: string; symptom: string;
};
type Match = {
  key: string; score: number; summary: string; chip: string; category: string;
  root_cause: string; resolution: string; workaround: string; debug_approach: string;
  embed_cos?: number; entity_overlap?: number; bm25_raw?: number; rerank_score?: number; verified?: boolean;
  known_issue?: { id: string; title: string };   // 소속 고장모드 기사(P2-4)
  lifecycle?: { state: string; superseded_by: string; freshness: number | null; fw_version: string; warnings: string[] };  // 수명주기(P2-5)
};
type Proposal = { root_cause: string; resolution: string; workaround: string; based_on: string; confidence: number };
type RecoResp = { query: any; matches: Match[]; proposal: Proposal | null; coverage: boolean; explanation?: string; explanation_citations?: string[]; explanation_dropped_citations?: string[] };
type GNode = { id: string; label: string; status: string; category: string; chip: string; template: string; center: boolean; relevance: number | null };
type GEdge = { source: string; target: string; weight: number; same_template: boolean; rerank: number | null };
type GraphData = { center: string | null; nodes: GNode[]; edges: GEdge[]; has_rerank?: boolean };

const STATUS_HEX: Record<string, string> = {
  "진행 중": "#10b981", "해야 할 일": "#94a3b8", "완료": "#3b82f6",
};

// 이슈 관계 그래프 — center 중심. rerank 관련도를 노드 크기·선 굵기로 인코딩하고
// 초기엔 관련도순 거리로 배치 후, 충돌 완화(겹침 제거)를 돌린다. 의존성 없는 인라인 SVG.
function RelationGraph({ data, onSelect }: { data: GraphData; onSelect: (k: string) => void }) {
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
      let dx = A.x - CX, dy = A.y - CY, d = Math.hypot(dx, dy) || 0.01;
      const minC = A.r + 16 + 16;
      if (d < minC) { A.x = CX + (dx / d) * minC; A.y = CY + (dy / d) * minC; }
      // 다른 이웃과 분리
      for (let j = i + 1; j < neigh.length; j++) {
        const B = pos[neigh[j].id];
        let ex = A.x - B.x, ey = A.y - B.y, e = Math.hypot(ex, ey) || 0.01;
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

const CAT_COLOR: Record<string, string> = {
  Firmware: "bg-blue-100 text-blue-700", Thermal: "bg-red-100 text-red-700",
  "Signal Integrity": "bg-lime-100 text-lime-700", Timing: "bg-violet-100 text-violet-700",
  Hardware: "bg-orange-100 text-orange-700", Power: "bg-amber-100 text-amber-700",
  Security: "bg-cyan-100 text-cyan-700",
};
// %값 → 색상(높을수록 강한 관련). 카드의 관련도/임베딩 색 구분용.
const pctText = (pct: number) =>
  pct >= 80 ? "text-emerald-600" : pct >= 60 ? "text-lime-600"
    : pct >= 40 ? "text-amber-600" : "text-rose-500";

const statusBadge = (s: string) =>
  s === "진행 중" ? "bg-emerald-500" : s === "해야 할 일" ? "bg-slate-400" : "bg-blue-500";

function Bar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = pct >= 80 ? "bg-emerald-500" : pct >= 50 ? "bg-amber-500" : "bg-red-500";
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-2 rounded-full bg-slate-200 overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-medium text-slate-600 w-10 text-right">{pct}%</span>
    </div>
  );
}

export default function FailureAnalysis({ onQueueChange }: { onQueueChange?: () => void } = {}) {
  const [stats, setStats] = useState<any>(null);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [q, setQ] = useState("");
  const [cat, setCat] = useState<string>("");
  const [sel, setSel] = useState<Issue | null>(null);
  const [reco, setReco] = useState<RecoResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [explaining, setExplaining] = useState(false);
  const [keyInput, setKeyInput] = useState("");
  const [err, setErr] = useState("");
  const [graph, setGraph] = useState<GraphData | null>(null);
  // 사이드바 너비 조정 + 접기
  const [leftW, setLeftW] = useState(320);
  const [rightW, setRightW] = useState(340);
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  const startDrag = (side: "left" | "right", e: ReactPointerEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = side === "left" ? leftW : rightW;
    const onMove = (ev: PointerEvent) => {
      const dx = ev.clientX - startX;
      const raw = side === "left" ? startW + dx : startW - dx;
      const w = Math.max(side === "left" ? 200 : 260, Math.min(640, raw));
      side === "left" ? setLeftW(w) : setRightW(w);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.style.userSelect = "";
    };
    document.body.style.userSelect = "none";
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  useEffect(() => {
    fetch(`${API}/reco/stats`).then((r) => r.json()).then(setStats).catch(() => {});
    fetch(`${API}/issues/unresolved`).then((r) => r.json()).then((d) => setIssues(d.issues ?? [])).catch(() => {});
  }, []);

  // 선택 이슈가 바뀌면 관계 그래프 로드 (우측 사이드바)
  useEffect(() => {
    if (!sel?.key) { setGraph(null); return; }
    fetch(`${API}/graph?key=${encodeURIComponent(sel.key)}&k=12`)
      .then((r) => r.json()).then(setGraph).catch(() => setGraph(null));
  }, [sel?.key]);

  const cats = useMemo(() => Array.from(new Set(issues.map((i) => i.category))).sort(), [issues]);
  const filtered = useMemo(
    () => issues.filter((i) =>
      (!cat || i.category === cat) &&
      (!q || (i.key + i.summary + i.chip).toLowerCase().includes(q.toLowerCase()))),
    [issues, q, cat]);

  const select = async (issue: Issue) => {
    setSel(issue); setReco(null); setErr(""); setLoading(true);
    try {
      const r = await fetch(`${API}/recommend`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: issue.key, k: 4 }),
      });
      setReco(await r.json());
    } catch (e: any) {
      setReco({ query: {}, matches: [], proposal: null, coverage: false, explanation: "[error] " + e.message });
    } finally { setLoading(false); }
  };

  // LLM 종합 분석을 SSE로 스트리밍 — 본문은 토큰 단위, 인용은 완료 시 검증본 반영
  const runExplain = (key: string) => {
    setExplaining(true);
    setReco((prev) => (prev ? { ...prev, explanation: "", explanation_citations: [], explanation_dropped_citations: [] } : prev));
    const es = new EventSource(`${API}/recommend/explain/stream?key=${encodeURIComponent(key)}&k=4`);
    const finish = () => { es.close(); setExplaining(false); };
    es.onmessage = (e) => {
      let d: any; try { d = JSON.parse(e.data); } catch { return; }
      if (d.type === "delta") {
        setReco((prev) => (prev ? { ...prev, explanation: (prev.explanation || "") + d.text } : prev));
      } else if (d.type === "done") {
        setReco((prev) => (prev ? { ...prev, explanation_citations: d.citations || [] } : prev));
        finish();
      } else if (d.type === "error") {
        setReco((prev) => (prev ? { ...prev, explanation: (prev.explanation || "") + `\n\n_(생성 오류: ${d.message})_` } : prev));
        finish();
      }
    };
    es.onerror = finish;
  };

  const explain = () => { if (sel) runExplain(sel.key); };

  // RCA 댓글 초안 → HITL 승인 대기 큐에 추가 (Jira 게시는 승인 시에만)
  const [drafting, setDrafting] = useState(false);
  const [draftMsg, setDraftMsg] = useState("");
  const draftRca = async () => {
    if (!sel) return;
    setDrafting(true); setDraftMsg("");
    try {
      const d = await fetch(`${API}/rca/draft`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: sel.key }),
      }).then((r) => r.json());
      setDraftMsg(d.error ? `⚠ ${d.error}`
        : d.already_approved ? `ℹ 이미 승인·게시된 이슈입니다${d.item?.comment_id ? ` (댓글 #${d.item.comment_id})` : ""} — 새 초안을 만들지 않았습니다`
        : "✓ 승인 대기 큐에 추가됨 (상단 '📤 승인 대기'에서 검토·게시)");
      if (!d.error) onQueueChange?.();
    } catch (e: any) { setDraftMsg(`⚠ ${e.message}`); } finally { setDrafting(false); }
  };

  // AI 심층 분석(LLM) → HITL 승인 대기 큐 (생성물이라 항상 검토 후 게시)
  const [draftingAn, setDraftingAn] = useState(false);
  const [draftAnMsg, setDraftAnMsg] = useState("");
  const draftFromAnalysis = async () => {
    if (!sel || !reco?.explanation) return;
    setDraftingAn(true); setDraftAnMsg("");
    try {
      const d = await fetch(`${API}/rca/draft-from-analysis`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: sel.key, analysis_md: reco.explanation, citations: reco.explanation_citations ?? [] }),
      }).then((r) => r.json());
      setDraftAnMsg(d.error ? `⚠ ${d.error}`
        : d.already_approved ? `ℹ 이미 승인·게시된 이슈입니다${d.item?.comment_id ? ` (댓글 #${d.item.comment_id})` : ""} — 새 초안을 만들지 않았습니다`
        : "✓ 이 심층 분석을 승인 대기 큐에 추가했습니다 (검토 후 게시)");
      if (!d.error) onQueueChange?.();
    } catch (e: any) { setDraftAnMsg(`⚠ ${e.message}`); } finally { setDraftingAn(false); }
  };

  // P1-3 추천 유용성 피드백 — 매치별 도움됨/아님 + 실제 근본원인 라벨 수집
  const [fb, setFb] = useState<Record<string, { rating?: "helpful" | "not_helpful"; actual?: boolean }>>({});
  const sendFeedback = async (m: Match, rank: number,
                              patch: { rating?: "helpful" | "not_helpful"; actual?: boolean }) => {
    const cur = fb[m.key] ?? {};
    const next = { ...cur, ...patch };
    setFb((s) => ({ ...s, [m.key]: next }));               // 낙관적 갱신
    if (!next.rating) return;                               // rating 없으면 전송 보류
    try {
      await fetch(`${API}/reco/feedback`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query_key: sel?.key ?? "", query_summary: sel?.summary ?? reco?.query?.summary ?? "",
          match_key: m.key, rating: next.rating, is_actual_root_cause: !!next.actual,
          match_rank: rank, match_score: m.rerank_score ?? m.embed_cos ?? m.score,
        }),
      });
    } catch { /* 피드백 실패는 조용히 무시(분석 흐름 방해 금지) */ }
  };

  // P2-4 고장모드 기사로 묶기 — 아직 기사에 속하지 않은 현재 매치들을 승격
  const [promoting, setPromoting] = useState(false);
  const [promoteMsg, setPromoteMsg] = useState("");
  const promoteMatches = async () => {
    const ms = reco?.matches ?? [];
    const free = ms.filter((m) => !m.known_issue).map((m) => m.key);
    if (free.length < 2) return;
    const title = (sel?.summary ?? reco?.query?.summary ?? "고장모드").slice(0, 80);
    setPromoting(true); setPromoteMsg("");
    try {
      const d = await fetch(`${API}/knowledge/known-issue`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, members: free }),
      }).then((r) => r.json());
      if (d.ok) {
        setPromoteMsg(`✓ 고장모드 기사 ${d.article.id} 생성 — ${free.length}건 묶음`);
        if (sel) select(sel);                // 재조회로 기사 배지 반영
      } else setPromoteMsg(`⚠ ${d.error || "승격 실패"}`);
    } catch (e: any) { setPromoteMsg(`⚠ ${e.message}`); } finally { setPromoting(false); }
  };

  // Jira 번호(또는 그래프 노드 클릭) → 유사 사례 검색 + 에이전트(LLM) 종합 분석
  const goKey = async (raw: string) => {
    const t = raw.trim().toUpperCase();
    if (!t) return;
    const key = /^\d+$/.test(t) ? `LSI-${t}` : t;
    setErr("");
    const found = issues.find((i) => i.key === key);
    if (found) {
      await select(found);
      runExplain(key);
      return;
    }
    // 미해결 목록에 없는 키(해결 이슈 등)는 백엔드 by_key로 직접 조회
    setSel(null); setReco(null); setLoading(true);
    try {
      const r = await fetch(`${API}/recommend`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key, k: 4 }),
      });
      const d = await r.json();
      if (d.error) { setErr(d.error); return; }
      const q = d.query ?? {};
      setSel({
        key, summary: q.summary ?? "", status: q.status ?? "", chip: q.chip ?? "",
        category: q.category ?? "기타", priority: "", severity: "", symptom: q.symptom ?? "",
      });
      setReco(d);
      runExplain(key);
    } catch (e: any) {
      setErr(e.message);
    } finally { setLoading(false); }
  };

  return (
    <div className="h-full flex bg-slate-50 text-slate-900">
      {/* 좌: 미해결 이슈 목록 (너비 조정 + 접기) */}
      {leftOpen ? (
      <aside style={{ width: leftW }} className="shrink-0 border-r border-slate-200 bg-white flex flex-col">
        <div className="p-4 border-b border-slate-200">
          <div className="flex items-center justify-between mb-2">
            <div className="text-xs font-semibold text-slate-500">미해결 이슈 {issues.length}건</div>
            <button onClick={() => setLeftOpen(false)} title="목록 접기"
              className="text-slate-400 hover:text-indigo-600 px-1 leading-none">◀</button>
          </div>
          <input
            value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="이슈 검색 (키/칩/증상)"
            className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 focus:border-indigo-500 focus:outline-none"
          />
          <div className="flex flex-wrap gap-1 mt-2">
            <button onClick={() => setCat("")}
              className={`text-xs px-2 py-0.5 rounded-full ${!cat ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600"}`}>전체</button>
            {cats.map((c) => (
              <button key={c} onClick={() => setCat(c === cat ? "" : c)}
                className={`text-xs px-2 py-0.5 rounded-full ${c === cat ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600"}`}>{c}</button>
            ))}
          </div>
        </div>
        <div className="flex-1 overflow-y-auto">
          {filtered.map((i) => (
            <button key={i.key} onClick={() => select(i)}
              className={`w-full text-left px-4 py-3 border-b border-slate-100 hover:bg-indigo-50 transition ${sel?.key === i.key ? "bg-indigo-50" : ""}`}>
              <div className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full ${statusBadge(i.status)}`} />
                <span className="font-mono text-xs text-slate-500">{i.key}</span>
                <span className={`text-[10px] px-1.5 py-0.5 rounded ${CAT_COLOR[i.category] ?? "bg-slate-100 text-slate-600"}`}>{i.category}</span>
              </div>
              <div className="text-sm mt-1 leading-snug line-clamp-2">{i.summary}</div>
            </button>
          ))}
        </div>
      </aside>
      ) : (
        <button onClick={() => setLeftOpen(true)} title="이슈 목록 펼치기"
          className="w-7 shrink-0 border-r border-slate-200 bg-white hover:bg-indigo-50 flex flex-col items-center justify-center gap-2 text-slate-400 hover:text-indigo-600">
          <span>▶</span>
          <span className="text-[10px] [writing-mode:vertical-rl]">이슈 목록</span>
        </button>
      )}
      {leftOpen && (
        <div onPointerDown={(e) => startDrag("left", e)} title="드래그하여 너비 조정"
          className="w-1.5 shrink-0 cursor-col-resize bg-slate-200 hover:bg-indigo-400 active:bg-indigo-500 transition-colors" />
      )}

      {/* 우: 추천 결과 */}
      <main className="flex-1 min-w-0 overflow-y-auto">
        <header className="bg-gradient-to-r from-indigo-600 to-violet-600 text-white px-8 py-5">
          <h1 className="text-xl font-bold">LSI 불량 분석 어시스턴트</h1>
          <p className="text-indigo-100 text-sm mt-1">
            과거 해결 이슈 기반 root-cause·해결책 추천 · graph/BM25 hybrid retrieval
          </p>
          {stats && (
            <div className="flex gap-4 mt-3 text-xs">
              <span className="bg-white/15 rounded px-2 py-1">해결 KB {stats.resolved}건</span>
              <span className="bg-white/15 rounded px-2 py-1">고장 템플릿 {stats.templates}종</span>
              <span className="bg-white/15 rounded px-2 py-1">미해결 {stats.unresolved}건</span>
              <span className="bg-white/15 rounded px-2 py-1">검색정확도 P@1 1.0</span>
            </div>
          )}
          <form
            onSubmit={(e) => { e.preventDefault(); goKey(keyInput); }}
            className="mt-3 flex gap-2 max-w-md">
            <input
              value={keyInput} onChange={(e) => setKeyInput(e.target.value)}
              placeholder="Jira 이슈 번호 입력 (예: LSI-7 또는 7)"
              className="flex-1 px-3 py-2 text-sm rounded-lg text-slate-900 bg-white focus:outline-none focus:ring-2 focus:ring-white/60"
            />
            <button type="submit" disabled={loading || explaining}
              className="px-4 py-2 text-sm rounded-lg bg-white/20 hover:bg-white/30 font-semibold disabled:opacity-50 transition">
              🤖 에이전트 분석
            </button>
          </form>
        </header>

        {err && (
          <div className="m-8 bg-red-50 border border-red-200 rounded-xl p-5 text-red-700 text-sm">
            ⚠️ {err}
          </div>
        )}

        {!sel && !err ? (
          <div className="p-16 text-center text-slate-400">
            위에 Jira 이슈 번호(예: LSI-7)를 입력하거나, ← 왼쪽에서 미해결 이슈를 선택하면
            과거 해결 사례 기반 근본원인·해결책을 에이전트가 분석합니다.
          </div>
        ) : !sel ? null : (
          <div className="p-8 space-y-6 w-full">
            {/* 선택 이슈 */}
            <section className="bg-white rounded-xl border border-slate-200 p-5">
              <div className="flex items-center gap-2 mb-2">
                <span className={`w-2.5 h-2.5 rounded-full ${statusBadge(sel.status)}`} />
                <span className="font-mono text-sm text-slate-500">{sel.key}</span>
                <span className="text-xs text-slate-500">{sel.status}</span>
                <span className={`text-xs px-2 py-0.5 rounded ${CAT_COLOR[sel.category] ?? "bg-slate-100"}`}>{sel.category}</span>
                <span className="text-xs px-2 py-0.5 rounded bg-slate-100 text-slate-600">{sel.chip}</span>
              </div>
              <h2 className="font-semibold text-lg leading-snug">{sel.summary}</h2>
              <p className="text-sm text-slate-600 mt-2">{sel.symptom}</p>
            </section>

            {loading && <div className="text-slate-400 text-sm">유사 사례 검색 중…</div>}

            {reco && !loading && (
              <>
                {!reco.coverage ? (
                  <div className="bg-amber-50 border border-amber-200 rounded-xl p-5 text-amber-800 text-sm">
                    ⚠️ 유사한 과거 해결 사례를 찾지 못했습니다. 이 고장 유형은 처음 보고된 것일 수 있어 시니어 검토가 필요합니다.
                  </div>
                ) : (
                  <>
                    {/* AI 제안 */}
                    {reco.proposal && (
                      <section className="bg-white rounded-xl border-2 border-indigo-200 p-5">
                        <div className="flex items-center justify-between mb-3">
                          <h3 className="font-bold text-indigo-700">🤖 AI 제안 (근거: {reco.proposal.based_on})</h3>
                          <div className="w-40"><Bar value={reco.proposal.confidence} /></div>
                        </div>
                        <div className="space-y-3 text-sm">
                          <div><span className="font-semibold text-red-600">🔍 예상 근본원인</span>
                            <div className="mt-1 prose prose-sm max-w-none text-slate-700 prose-p:my-1 prose-li:my-0.5 prose-ol:my-1 prose-ul:my-1">
                              <ReactMarkdown remarkPlugins={[remarkGfm]}>{reco.proposal.root_cause || "—"}</ReactMarkdown></div></div>
                          <div><span className="font-semibold text-emerald-600">✅ 권장 해결책</span>
                            <div className="mt-1 prose prose-sm max-w-none text-slate-700 prose-p:my-1 prose-li:my-0.5 prose-ol:my-1 prose-ul:my-1">
                              <ReactMarkdown remarkPlugins={[remarkGfm]}>{reco.proposal.resolution || "—"}</ReactMarkdown></div></div>
                          <div><span className="font-semibold text-slate-500">↪ 임시 우회책</span>
                            <div className="mt-1 prose prose-sm max-w-none text-slate-600 prose-p:my-1 prose-li:my-0.5 prose-ol:my-1 prose-ul:my-1">
                              <ReactMarkdown remarkPlugins={[remarkGfm]}>{reco.proposal.workaround || "—"}</ReactMarkdown></div></div>
                        </div>
                        <div className="mt-4 flex items-center gap-2 flex-wrap">
                          <button onClick={explain} disabled={explaining}
                            className="text-sm px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 transition">
                            {explaining ? "AI 심층 분석 생성 중…" : "✨ AI 심층 분석 (LLM)"}
                          </button>
                          {sel && sel.status !== "완료" && (
                            <button onClick={draftRca} disabled={drafting}
                              title="RCA 댓글 초안을 만들어 승인 대기 큐에 추가 (게시는 승인 시에만)"
                              className="text-sm px-4 py-2 rounded-lg border border-indigo-300 text-indigo-600 hover:bg-indigo-50 disabled:opacity-50 transition">
                              {drafting ? "초안 생성 중…" : "🤖 RCA 댓글 초안 → 승인 대기"}
                            </button>
                          )}
                        </div>
                        {draftMsg && <div className="mt-2 text-xs text-slate-500">{draftMsg}</div>}
                      </section>
                    )}

                    {/* LLM 설명 (agno 구조화 출력) */}
                    {reco.explanation && (
                      <section className="bg-indigo-50 rounded-xl border border-indigo-200 p-5">
                        <div className="prose prose-sm max-w-none prose-headings:text-indigo-800 prose-headings:my-2 prose-p:my-1">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{reco.explanation}</ReactMarkdown>
                        </div>
                        {reco.explanation_citations && reco.explanation_citations.length > 0 && (
                          <div className="mt-3 pt-3 border-t border-indigo-200 flex items-center gap-1.5 flex-wrap">
                            <span className="text-[11px] text-slate-500">📎 근거(검증됨):</span>
                            {reco.explanation_citations.map((k) => (
                              <button key={k} onClick={() => goKey(k)}
                                className="text-[11px] font-mono px-1.5 py-0.5 rounded bg-white border border-indigo-200 text-indigo-600 hover:bg-indigo-100">{k}</button>
                            ))}
                          </div>
                        )}
                        {reco.explanation_dropped_citations && reco.explanation_dropped_citations.length > 0 && (
                          <div className="mt-1.5 text-[11px] text-rose-500">⚠ 매치 외 인용 제거됨: {reco.explanation_dropped_citations.join(", ")}</div>
                        )}
                        {sel && sel.status !== "완료" && !explaining && (
                          <div className="mt-3 pt-3 border-t border-indigo-200">
                            <button onClick={draftFromAnalysis} disabled={draftingAn}
                              title="이 심층 분석을 RCA 댓글로 승인 대기 큐에 추가 (사람 승인 후에만 Jira 게시)"
                              className="text-sm px-4 py-2 rounded-lg border border-indigo-300 text-indigo-600 hover:bg-indigo-100 disabled:opacity-50 transition">
                              {draftingAn ? "추가 중…" : "📤 이 심층 분석을 RCA 댓글로 → 승인 대기"}
                            </button>
                            {draftAnMsg && <div className="mt-2 text-xs text-slate-500">{draftAnMsg}</div>}
                          </div>
                        )}
                      </section>
                    )}

                    {/* 유사 사례 */}
                    <section>
                      <div className="flex items-center gap-2 mb-3">
                        <h3 className="font-semibold text-slate-700">유사 과거 해결 사례 {reco.matches.length}건</h3>
                        {reco.matches.filter((m) => !m.known_issue).length >= 2 && (
                          <button onClick={promoteMatches} disabled={promoting}
                            title="아직 기사에 속하지 않은 매치들을 하나의 고장모드(Known-Issue) 기사로 묶습니다"
                            className="ml-auto text-[11px] px-2 py-0.5 rounded border border-indigo-300 text-indigo-600 hover:bg-indigo-50 disabled:opacity-50">
                            {promoting ? "묶는 중…" : "📚 고장모드 기사로 묶기"}
                          </button>
                        )}
                      </div>
                      {promoteMsg && <div className="text-[11px] text-slate-500 mb-2">{promoteMsg}</div>}
                      {(() => {
                        const arts = Array.from(new Map(reco.matches.filter((m) => m.known_issue)
                          .map((m) => [m.known_issue!.id, m.known_issue!])).values());
                        return arts.length > 0 ? (
                          <div className="mb-3 text-[11px] text-indigo-700 bg-indigo-50 border border-indigo-200 rounded-lg px-3 py-2">
                            📚 이 사례들은 고장모드 기사로 묶여 있습니다: {arts.map((a) => `${a.id} ${a.title}`).join(" · ")}
                          </div>
                        ) : null;
                      })()}
                      <div className="space-y-3">
                        {reco.matches.map((m, mi) => (
                          <div key={m.key} className="bg-white rounded-xl border border-slate-200 p-4">
                            <div className="flex items-center gap-2 mb-1">
                              <span className="font-mono text-xs text-indigo-600 font-semibold">{m.key}</span>
                              <span className={`text-[10px] px-1.5 py-0.5 rounded ${CAT_COLOR[m.category] ?? "bg-slate-100"}`}>{m.category}</span>
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-500">{m.chip}</span>
                              {m.verified && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-600" title="해결 검증 + 고객 확인 완료">✓ 검증됨</span>
                              )}
                              {m.known_issue && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-600" title={`고장모드 기사: ${m.known_issue.title}`}>📚 {m.known_issue.id}</span>
                              )}
                              {m.lifecycle && m.lifecycle.warnings.length > 0 && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-700"
                                  title={`${m.lifecycle.warnings.join(" · ")}${m.lifecycle.fw_version ? ` · FW ${m.lifecycle.fw_version}` : ""}`}>
                                  ⚠ {m.lifecycle.warnings[0]}
                                </span>
                              )}
                              <span className="ml-auto text-[10px] text-slate-400 flex items-center gap-2" title="관련도=reranker 재순위 점수(카드 정렬 기준) · 임베딩=bi-encoder 코사인">
                                {m.rerank_score != null ? (
                                  <>
                                    <span>관련도 <b className={`font-bold ${pctText(Math.round(m.rerank_score * 100))}`}>{Math.round(m.rerank_score * 100)}%</b></span>
                                    {m.embed_cos != null && (
                                      <span>임베딩 <b className={`font-bold ${pctText(Math.round(m.embed_cos * 100))}`}>{Math.round(m.embed_cos * 100)}%</b></span>
                                    )}
                                  </>
                                ) : m.embed_cos != null ? (
                                  <span>유사도 <b className={`font-bold ${pctText(Math.round(m.embed_cos * 100))}`}>{Math.round(m.embed_cos * 100)}%</b></span>
                                ) : (
                                  <span>유사도 {m.score.toFixed(3)}</span>
                                )}
                              </span>
                            </div>
                            <div className="text-sm font-medium leading-snug">{m.summary}</div>
                            <details className="mt-2 text-xs text-slate-600">
                              <summary className="cursor-pointer text-slate-500 hover:text-indigo-600">근본원인 · 해결책 보기</summary>
                              <div className="mt-2 space-y-1.5 pl-2 border-l-2 border-slate-200">
                                <p><b className="text-red-600">근본원인:</b> {m.root_cause}</p>
                                <p><b className="text-emerald-600">해결책:</b> {m.resolution}</p>
                                {m.workaround && <p><b className="text-slate-500">우회책:</b> {m.workaround}</p>}
                              </div>
                            </details>
                            {/* P1-3 유용성 피드백 */}
                            <div className="mt-2 pt-2 border-t border-slate-100 flex items-center gap-2 text-[11px]">
                              <span className="text-slate-400">이 사례가</span>
                              <button onClick={() => sendFeedback(m, mi + 1, { rating: "helpful" })}
                                title="이 추천이 도움됨"
                                className={`px-2 py-0.5 rounded-full border ${fb[m.key]?.rating === "helpful" ? "bg-emerald-50 border-emerald-300 text-emerald-700" : "border-slate-200 text-slate-500 hover:border-emerald-300"}`}>
                                👍 도움됨
                              </button>
                              <button onClick={() => sendFeedback(m, mi + 1, { rating: "not_helpful" })}
                                title="이 추천이 도움 안 됨"
                                className={`px-2 py-0.5 rounded-full border ${fb[m.key]?.rating === "not_helpful" ? "bg-rose-50 border-rose-300 text-rose-700" : "border-slate-200 text-slate-500 hover:border-rose-300"}`}>
                                👎 아님
                              </button>
                              <label className="ml-auto flex items-center gap-1 text-slate-500 cursor-pointer" title="이 사례가 실제 근본원인이었음(ROI·평가셋 정답)">
                                <input type="checkbox" checked={!!fb[m.key]?.actual}
                                  onChange={(e) => sendFeedback(m, mi + 1, { actual: e.target.checked, rating: fb[m.key]?.rating ?? "helpful" })}
                                  className="accent-indigo-600" />
                                실제 근본원인
                              </label>
                            </div>
                          </div>
                        ))}
                      </div>
                    </section>
                  </>
                )}
              </>
            )}
          </div>
        )}
      </main>

      {/* 우: 이슈 관계 그래프 (너비 조정 + 접기) */}
      {rightOpen && (
        <div onPointerDown={(e) => startDrag("right", e)} title="드래그하여 너비 조정"
          className="w-1.5 shrink-0 cursor-col-resize bg-slate-200 hover:bg-indigo-400 active:bg-indigo-500 transition-colors" />
      )}
      {rightOpen ? (
      <aside style={{ width: rightW }} className="shrink-0 border-l border-slate-200 bg-white flex flex-col">
        <div className="p-4 border-b border-slate-200">
          <div className="flex items-center justify-between">
            <div className="text-sm font-semibold text-slate-700">🔗 이슈 관계 그래프</div>
            <button onClick={() => setRightOpen(false)} title="그래프 접기"
              className="text-slate-400 hover:text-indigo-600 px-1 leading-none">▶</button>
          </div>
          <div className="text-xs text-slate-400 mt-0.5">공유 엔티티(칩·분류·기술용어) 기반 · 노드 클릭 시 이동</div>
        </div>
        <div className="flex-1 overflow-y-auto p-3">
          {!sel ? (
            <div className="text-xs text-slate-400 p-4 text-center">이슈를 선택하면 관련 이슈들의 관계가 그래프로 표시됩니다.</div>
          ) : !graph || graph.nodes.length <= 1 ? (
            <div className="text-xs text-slate-400 p-4 text-center">관계 그래프 로딩 중… (또는 관련 이슈 없음)</div>
          ) : (
            <>
              <RelationGraph data={graph} onSelect={goKey} />
              <div className="mt-3 space-y-1.5 text-[11px] text-slate-500">
                <div className="flex items-center gap-2">
                  <svg width="26" height="8"><line x1="0" y1="4" x2="26" y2="4" stroke="#6366f1" strokeWidth="5" strokeLinecap="round" /></svg>
                  선 굵기·노드 크기 = <b className="text-indigo-600">rerank 관련도</b>
                </div>
                <div className="flex items-center gap-2">
                  <svg width="26" height="10"><circle cx="13" cy="5" r="4" fill="none" stroke="#6366f1" strokeWidth="1.2" strokeDasharray="2 2" /></svg>
                  점선 테두리 = 같은 근본원인(동일 템플릿)
                </div>
                <div className="flex items-center gap-3 pt-1">
                  <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full" style={{ background: "#10b981" }} />진행 중</span>
                  <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full" style={{ background: "#94a3b8" }} />해야 할 일</span>
                  <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full" style={{ background: "#3b82f6" }} />완료</span>
                </div>
                <div className="pt-1 text-slate-400">
                  중심 <span className="font-mono">{graph.center}</span> · 관련 {graph.nodes.length - 1}건
                  {graph.has_rerank === false && <span className="text-amber-500"> · (rerank 미적용: 엔티티 기반)</span>}
                </div>
              </div>
            </>
          )}
        </div>
      </aside>
      ) : (
        <button onClick={() => setRightOpen(true)} title="관계 그래프 펼치기"
          className="w-7 shrink-0 border-l border-slate-200 bg-white hover:bg-indigo-50 flex flex-col items-center justify-center gap-2 text-slate-400 hover:text-indigo-600">
          <span>◀</span>
          <span className="text-[10px] [writing-mode:vertical-rl]">관계 그래프</span>
        </button>
      )}
    </div>
  );
}
