import { useEffect, useMemo, useState } from "react";
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
  embed_cos?: number; entity_overlap?: number; bm25_raw?: number;
};
type Proposal = { root_cause: string; resolution: string; workaround: string; based_on: string; confidence: number };
type RecoResp = { query: any; matches: Match[]; proposal: Proposal | null; coverage: boolean; explanation?: string };

const CAT_COLOR: Record<string, string> = {
  Firmware: "bg-blue-100 text-blue-700", Thermal: "bg-red-100 text-red-700",
  "Signal Integrity": "bg-lime-100 text-lime-700", Timing: "bg-violet-100 text-violet-700",
  Hardware: "bg-orange-100 text-orange-700", Power: "bg-amber-100 text-amber-700",
  Security: "bg-cyan-100 text-cyan-700",
};
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

export default function FailureAnalysis() {
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

  useEffect(() => {
    fetch(`${API}/reco/stats`).then((r) => r.json()).then(setStats).catch(() => {});
    fetch(`${API}/issues/unresolved`).then((r) => r.json()).then((d) => setIssues(d.issues ?? [])).catch(() => {});
  }, []);

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

  const runExplain = async (key: string) => {
    setExplaining(true);
    try {
      const r = await fetch(`${API}/recommend`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key, k: 4, explain: true }),
      });
      const d: RecoResp = await r.json();
      setReco((prev) => (prev ? { ...prev, explanation: d.explanation } : d));
    } finally { setExplaining(false); }
  };

  const explain = () => { if (sel) runExplain(sel.key); };

  // Jira 번호 직접 입력 → 유사 사례 검색 + 에이전트(LLM) 종합 분석 자동 실행
  const analyzeByKey = async () => {
    const t = keyInput.trim().toUpperCase();
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
      {/* 좌: 미해결 이슈 목록 */}
      <aside className="w-80 border-r border-slate-200 bg-white flex flex-col">
        <div className="p-4 border-b border-slate-200">
          <div className="text-xs font-semibold text-slate-500 mb-2">미해결 이슈 {issues.length}건</div>
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

      {/* 우: 추천 결과 */}
      <main className="flex-1 overflow-y-auto">
        <header className="bg-gradient-to-r from-indigo-600 to-violet-600 text-white px-8 py-5">
          <h1 className="text-xl font-bold">LSI 고장 분석 어시스턴트</h1>
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
            onSubmit={(e) => { e.preventDefault(); analyzeByKey(); }}
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
          <div className="m-8 bg-red-50 border border-red-200 rounded-xl p-5 text-red-700 text-sm max-w-4xl">
            ⚠️ {err}
          </div>
        )}

        {!sel && !err ? (
          <div className="p-16 text-center text-slate-400">
            위에 Jira 이슈 번호(예: LSI-7)를 입력하거나, ← 왼쪽에서 미해결 이슈를 선택하면
            과거 해결 사례 기반 근본원인·해결책을 에이전트가 분석합니다.
          </div>
        ) : !sel ? null : (
          <div className="p-8 space-y-6 max-w-4xl">
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
                            <p className="mt-1 text-slate-700">{reco.proposal.root_cause}</p></div>
                          <div><span className="font-semibold text-emerald-600">✅ 권장 해결책</span>
                            <p className="mt-1 text-slate-700">{reco.proposal.resolution}</p></div>
                          <div><span className="font-semibold text-slate-500">↪ 임시 우회책</span>
                            <p className="mt-1 text-slate-600">{reco.proposal.workaround || "—"}</p></div>
                        </div>
                        <button onClick={explain} disabled={explaining}
                          className="mt-4 text-sm px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 transition">
                          {explaining ? "시니어 종합 분석 생성 중…" : "✨ 시니어 종합 분석 생성 (LLM)"}
                        </button>
                      </section>
                    )}

                    {/* LLM 설명 */}
                    {reco.explanation && (
                      <section className="bg-indigo-50 rounded-xl border border-indigo-200 p-5 prose prose-sm max-w-none prose-headings:text-indigo-800 prose-headings:my-2 prose-p:my-1">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{reco.explanation}</ReactMarkdown>
                      </section>
                    )}

                    {/* 유사 사례 */}
                    <section>
                      <h3 className="font-semibold text-slate-700 mb-3">유사 과거 해결 사례 {reco.matches.length}건</h3>
                      <div className="space-y-3">
                        {reco.matches.map((m) => (
                          <div key={m.key} className="bg-white rounded-xl border border-slate-200 p-4">
                            <div className="flex items-center gap-2 mb-1">
                              <span className="font-mono text-xs text-indigo-600 font-semibold">{m.key}</span>
                              <span className={`text-[10px] px-1.5 py-0.5 rounded ${CAT_COLOR[m.category] ?? "bg-slate-100"}`}>{m.category}</span>
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-500">{m.chip}</span>
                              <span className="ml-auto text-[10px] text-slate-400">
                                {m.embed_cos != null ? `유사도 ${Math.round(m.embed_cos * 100)}%` : `유사도 ${m.score.toFixed(3)}`}
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
    </div>
  );
}
