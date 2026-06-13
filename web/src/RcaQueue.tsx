import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const API = (import.meta as any).env?.VITE_API ?? "http://127.0.0.1:8001";

type QItem = {
  key: string; summary: string; status: string; body: string;
  confidence: number; based_on_verified: boolean; needs_review: boolean;
  based_on: string; created_at: string; state: string; comment_id?: string;
};

export default function RcaQueue({ onBack, onChange }: { onBack: () => void; onChange?: () => void }) {
  const [items, setItems] = useState<QItem[]>([]);
  const [busy, setBusy] = useState<string>("");
  const [msg, setMsg] = useState<{ key: string; ok: boolean; text: string } | null>(null);

  const load = () => fetch(`${API}/rca/pending`).then((r) => r.json()).then((d) => setItems(d.items ?? [])).catch(() => {});
  useEffect(() => { load(); }, []);

  const act = async (key: string, action: "approve" | "reject") => {
    setBusy(key + action); setMsg(null);
    try {
      const d = await fetch(`${API}/rca/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key }),
      }).then((r) => r.json());
      if (action === "approve") {
        setMsg(d.ok ? { key, ok: true, text: `Jira 게시 완료${d.item?.comment_id ? ` (댓글 #${d.item.comment_id})` : ""}` }
                    : { key, ok: false, text: `게시 실패: ${d.error || ""}` });
      } else {
        setMsg({ key, ok: true, text: "거부됨 (게시 안 함)" });
      }
      await load(); onChange?.();
    } finally { setBusy(""); }
  };

  return (
    <div className="h-full overflow-y-auto bg-slate-100">
      <div className="max-w-3xl mx-auto p-6">
        <div className="flex items-center gap-3 mb-1">
          <button onClick={onBack} className="text-sm text-slate-500 hover:text-indigo-600">← 분석으로</button>
          <h1 className="text-xl font-bold text-slate-800">📤 RCA 댓글 승인 대기 (HITL)</h1>
        </div>
        <p className="text-sm text-slate-500 mb-5">사람이 승인할 때만 Jira에 게시됩니다. 거부하면 게시되지 않습니다.</p>

        {items.length === 0 ? (
          <div className="text-center text-slate-400 py-16 text-sm">대기 중인 초안이 없습니다. 분석 화면에서 미해결 이슈의 "RCA 초안 생성"으로 추가하세요.</div>
        ) : (
          <div className="space-y-4">
            {items.map((it) => (
              <section key={it.key} className="bg-white rounded-xl border border-slate-200 p-5">
                <div className="flex items-center gap-2 mb-2 flex-wrap">
                  <span className="font-mono text-sm text-indigo-600 font-semibold">{it.key}</span>
                  <span className="text-xs text-slate-500">{it.status}</span>
                  <span className={`text-[11px] px-2 py-0.5 rounded-full ${it.needs_review ? "bg-amber-100 text-amber-700" : "bg-emerald-100 text-emerald-700"}`}>
                    {it.needs_review ? "⚠ 검토 필요" : "자동 게시 적합"}
                  </span>
                  <span className="text-[11px] text-slate-500">신뢰도 {Math.round((it.confidence || 0) * 100)}%{it.based_on_verified ? " · 검증 근거" : ""}</span>
                </div>
                <div className="text-sm font-medium leading-snug mb-2">{it.summary}</div>
                <details className="text-xs text-slate-600">
                  <summary className="cursor-pointer text-slate-500 hover:text-indigo-600">게시 본문 미리보기</summary>
                  <div className="mt-2 p-3 bg-slate-50 rounded border border-slate-200 prose prose-sm max-w-none whitespace-pre-wrap">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{it.body}</ReactMarkdown>
                  </div>
                </details>
                {msg && msg.key === it.key && (
                  <div className={`mt-2 text-xs ${msg.ok ? "text-emerald-600" : "text-rose-600"}`}>{msg.ok ? "✓" : "✗"} {msg.text}</div>
                )}
                <div className="mt-3 flex gap-2">
                  <button onClick={() => act(it.key, "approve")} disabled={!!busy}
                    className="text-sm px-4 py-2 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50">
                    {busy === it.key + "approve" ? "게시 중…" : "✅ 승인하고 Jira 게시"}
                  </button>
                  <button onClick={() => act(it.key, "reject")} disabled={!!busy}
                    className="text-sm px-4 py-2 rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 disabled:opacity-50">
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
