import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const API = (import.meta as any).env?.VITE_API ?? "http://127.0.0.1:8001";

type QItem = {
  key: string; summary: string; status: string; body: string;
  confidence: number | null; based_on_verified: boolean; needs_review: boolean;
  based_on: string; created_at: string; state: string; comment_id?: string; source?: string;
};

export default function RcaQueue({ onBack, onChange }: { onBack: () => void; onChange?: () => void }) {
  const [items, setItems] = useState<QItem[]>([]);
  const [busy, setBusy] = useState<string>("");
  const [msg, setMsg] = useState<{ key: string; ok: boolean; text: string } | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});   // key → 수정 본문
  const [editing, setEditing] = useState<string>("");                // 본문 패널 열린 key
  const [panelTab, setPanelTab] = useState<Record<string, "edit" | "preview">>({}); // 편집/미리보기
  const [valid, setValid] = useState<Record<string, any>>({});       // key → 검증 결과
  const [validating, setValidating] = useState<string>("");

  const load = () => fetch(`${API}/rca/pending`).then((r) => r.json()).then((d) => setItems(d.items ?? [])).catch(() => {});
  useEffect(() => { load(); }, []);

  const bodyOf = (it: QItem) => (edits[it.key] ?? it.body);
  const isEdited = (it: QItem) => (it.key in edits) && edits[it.key].trim() !== it.body.trim();

  const validate = async (it: QItem) => {
    setValidating(it.key);
    try {
      const d = await fetch(`${API}/rca/validate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: it.key, body: bodyOf(it) }),
      }).then((r) => r.json());
      setValid((v) => ({ ...v, [it.key]: d }));
    } finally { setValidating(""); }
  };

  const act = async (it: QItem, action: "approve" | "reject") => {
    const key = it.key;
    setBusy(key + action); setMsg(null);
    try {
      const payload: any = { key };
      if (action === "approve" && isEdited(it)) payload.body = edits[key];  // 수정본 게시
      const d = await fetch(`${API}/rca/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      }).then((r) => r.json());
      if (action === "approve") {
        setMsg(d.ok ? { key, ok: true, text: `Jira 게시 완료${d.edited ? " (수정본, 메모리 저장됨)" : ""}${d.item?.comment_id ? ` · 댓글 #${d.item.comment_id}` : ""}` }
                    : { key, ok: false, text: `게시 실패: ${d.error || ""}` });
      } else {
        setMsg({ key, ok: true, text: "거부됨 (게시 안 함)" });
      }
      await load(); onChange?.();
    } finally { setBusy(""); }
  };

  return (
    <div className="h-full overflow-y-auto bg-zinc-800">
      <div className="max-w-3xl mx-auto p-6">
        <div className="flex items-center gap-3 mb-1">
          <button onClick={onBack} className="text-sm text-zinc-400 hover:text-sky-400">← 홈(분석 화면)</button>
          <h1 className="text-xl font-bold text-zinc-100">📤 RCA 댓글 승인 대기 (HITL)</h1>
        </div>
        <p className="text-sm text-zinc-400 mb-5">사람이 승인할 때만 Jira에 게시됩니다. 거부하면 게시되지 않습니다.</p>

        {items.length === 0 ? (
          <div className="text-center text-zinc-400 py-16 text-sm">대기 중인 초안이 없습니다. 분석 화면에서 미해결 이슈의 "RCA 초안 생성"으로 추가하세요.</div>
        ) : (
          <div className="space-y-4">
            {items.map((it) => (
              <section key={it.key} className="bg-zinc-900/60 rounded-xl border border-zinc-800 p-5">
                <div className="flex items-center gap-2 mb-2 flex-wrap">
                  <span className="font-mono text-sm text-sky-400 font-semibold">{it.key}</span>
                  <span className="text-xs text-zinc-400">{it.status}</span>
                  <span className={`text-[11px] px-2 py-0.5 rounded-full ${it.needs_review ? "bg-amber-950/60 text-amber-400" : "bg-emerald-950/60 text-emerald-400"}`}>
                    {it.needs_review ? "⚠ 검토 필요" : "자동 게시 적합"}
                  </span>
                  <span className="text-[11px] px-2 py-0.5 rounded-full bg-sky-950/40 text-sky-400">
                    {it.source === "analysis" ? "LLM 종합" : "제안 기반"}
                  </span>
                  {it.confidence != null && (
                    <span className="text-[11px] text-zinc-400">신뢰도 {Math.round(it.confidence * 100)}%{it.based_on_verified ? " · 검증 근거" : ""}</span>
                  )}
                </div>
                <div className="text-sm font-medium leading-snug mb-2">{it.summary}</div>
                <div className="flex items-center gap-2 mb-1">
                  <button onClick={() => {
                    const open = editing === it.key;
                    setEditing(open ? "" : it.key);
                    if (!open) {
                      if (!(it.key in edits)) setEdits((e) => ({ ...e, [it.key]: it.body }));
                      if (!(it.key in panelTab)) setPanelTab((t) => ({ ...t, [it.key]: "preview" }));
                    }
                  }}
                    className="text-[11px] text-zinc-400 hover:text-sky-400">
                    {editing === it.key ? "▾ 본문 닫기" : "📝 게시 본문 미리보기·수정"}{isEdited(it) ? " (수정됨)" : ""}
                  </button>
                  {isEdited(it) && <span className="text-[11px] text-amber-400">● 수정본이 게시·저장됩니다</span>}
                </div>
                {editing === it.key && (
                  <div className="border border-zinc-800 rounded-lg overflow-hidden">
                    <div className="flex items-center text-[11px] border-b border-zinc-800 bg-zinc-950">
                      {(["preview", "edit"] as const).map((t) => (
                        <button key={t} onClick={() => setPanelTab((s) => ({ ...s, [it.key]: t }))}
                          className={`px-3 py-1.5 ${(panelTab[it.key] ?? "preview") === t ? "bg-zinc-900/60 text-sky-400 font-semibold border-b-2 border-sky-500" : "text-zinc-400 hover:text-sky-400"}`}>
                          {t === "preview" ? "👁 미리보기" : "✏️ 편집"}
                        </button>
                      ))}
                      <span className="ml-auto px-2 text-[10px] text-zinc-400">Markdown</span>
                    </div>
                    {(panelTab[it.key] ?? "preview") === "edit" ? (
                      <textarea value={bodyOf(it)} onChange={(e) => setEdits((s) => ({ ...s, [it.key]: e.target.value }))}
                        rows={14} spellCheck={false}
                        className="w-full text-xs font-mono p-3 focus:outline-none resize-y bg-zinc-900/60" />
                    ) : (
                      <div className="p-3 bg-zinc-900/60 prose prose-sm max-w-none max-h-96 overflow-y-auto">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{bodyOf(it)}</ReactMarkdown>
                      </div>
                    )}
                  </div>
                )}
                {valid[it.key] && (
                  <div className="mt-2 text-[11px] bg-zinc-950 border border-zinc-800 rounded p-2 space-y-0.5">
                    <div className={valid[it.key].citations_ok ? "text-emerald-400" : "text-red-400"}>
                      {valid[it.key].citations_ok ? "✓ 인용 모두 KB에 존재" : `✗ 매치 외 인용: ${(valid[it.key].invalid_citations || []).join(", ")}`}
                    </div>
                    <div className={valid[it.key].lang_ok ? "text-emerald-400" : "text-red-400"}>
                      {valid[it.key].lang_ok ? "✓ 언어 규칙 OK(한자 없음)" : "✗ 한자/CJK 검출"}
                    </div>
                    {valid[it.key].judge_score != null && (
                      <div className={valid[it.key].judge_passed ? "text-emerald-400" : "text-amber-400"}>
                        🧑‍⚖️ 품질 점수 {valid[it.key].judge_score}/10 {valid[it.key].judge_passed ? "(통과)" : "(검토 권장)"}
                        {valid[it.key].judge_reasoning ? ` — ${valid[it.key].judge_reasoning}` : ""}
                      </div>
                    )}
                    {valid[it.key].judge_error && <div className="text-zinc-400">판정 생략: {valid[it.key].judge_error}</div>}
                  </div>
                )}
                {msg && msg.key === it.key && (
                  <div className={`mt-2 text-xs ${msg.ok ? "text-emerald-400" : "text-red-400"}`}>{msg.ok ? "✓" : "✗"} {msg.text}</div>
                )}
                <div className="mt-3 flex gap-2">
                  <button onClick={() => validate(it)} disabled={!!validating}
                    className="text-sm px-4 py-2 rounded-lg border border-zinc-600 text-sky-400 hover:bg-zinc-800 disabled:opacity-50">
                    {validating === it.key ? "검증 중…" : "🔎 검증"}
                  </button>
                  <button onClick={() => act(it, "approve")} disabled={!!busy}
                    className="text-sm px-4 py-2 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50">
                    {busy === it.key + "approve" ? "게시 중…" : (isEdited(it) ? "✅ 수정본 승인·게시" : "✅ 승인하고 Jira 게시")}
                  </button>
                  <button onClick={() => act(it, "reject")} disabled={!!busy}
                    className="text-sm px-4 py-2 rounded-lg border border-zinc-700 text-zinc-300 hover:bg-zinc-950 disabled:opacity-50">
                    {busy === it.key + "reject" ? "처리 중…" : "거부"}
                  </button>
                </div>
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
