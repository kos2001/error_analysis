import { useEffect, useState } from "react";
import { inputCls } from "./ui";

const API = (import.meta as any).env?.VITE_API ?? "";   // 빈 값 = 같은 오리진(개발은 vite 프록시)

type Voc = {
  id: string; category: string; message: string; author: string;
  context: string; state: string; created_at: string;
};

const CATS: { key: string; label: string; icon: string }[] = [
  { key: "bug", label: "버그", icon: "🐞" },
  { key: "improvement", label: "개선 요청", icon: "💡" },
  { key: "praise", label: "칭찬", icon: "👍" },
  { key: "question", label: "문의", icon: "❓" },
  { key: "other", label: "기타", icon: "💬" },
];
const CAT = (k: string) => CATS.find((c) => c.key === k) ?? CATS[4];
const STATE_BADGE: Record<string, string> = {
  open: "bg-amber-950/60 text-amber-400", triaged: "bg-sky-950/60 text-sky-400",
  resolved: "bg-emerald-950/60 text-emerald-400", wont_fix: "bg-zinc-800 text-zinc-400",
};
const STATE_LABEL: Record<string, string> = {
  open: "접수", triaged: "분류됨", resolved: "해결", wont_fix: "보류",
};

export default function VocPage({ onBack }: { onBack: () => void }) {
  const [items, setItems] = useState<Voc[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [category, setCategory] = useState("improvement");
  const [message, setMessage] = useState("");
  const [author, setAuthor] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = () => fetch(`${API}/voc`).then((r) => r.json())
    .then((d) => { setItems(d.items ?? []); setStats(d.stats ?? null); }).catch(() => {});
  useEffect(() => { load(); }, []);

  const submit = async () => {
    if (!message.trim()) { setMsg({ ok: false, text: "내용을 입력하세요." }); return; }
    setBusy(true); setMsg(null);
    try {
      const d = await fetch(`${API}/voc`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category, message, author, context: "voc-page" }),
      }).then((r) => r.json());
      if (d.ok) { setMsg({ ok: true, text: `접수되었습니다 (${d.item.id}) — 감사합니다!` }); setMessage(""); load(); }
      else setMsg({ ok: false, text: d.error || "등록 실패" });
    } catch (e: any) { setMsg({ ok: false, text: e.message }); } finally { setBusy(false); }
  };

  const setState = async (id: string, state: string) => {
    await fetch(`${API}/voc/state`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, state }),
    }).then((r) => r.json()).catch(() => {});
    load();
  };

  return (
    <div className="h-full overflow-y-auto bg-zinc-800">
      <div className="max-w-3xl mx-auto p-6">
        <div className="flex items-center gap-3 mb-1">
          <button onClick={onBack} className="text-sm text-zinc-400 hover:text-sky-400">← 홈(분석 화면)</button>
          <h1 className="text-xl font-bold text-zinc-100">💬 VOC — 서비스 의견</h1>
        </div>
        <p className="text-sm text-zinc-400 mb-5">이 서비스에 대한 버그·개선 요청·문의·칭찬을 남겨주세요. 제품 개선에 반영됩니다.</p>

        {/* 작성 폼 */}
        <section className="bg-zinc-900/60 rounded-xl border border-zinc-800 p-5 mb-6">
          <div className="flex flex-wrap gap-2 mb-3">
            {CATS.map((c) => (
              <button key={c.key} onClick={() => setCategory(c.key)}
                className={`text-sm px-3 py-1 rounded-full border ${category === c.key
                  ? "bg-zinc-100 text-zinc-900 border-sky-600" : "border-zinc-700 text-zinc-300 hover:border-sky-600"}`}>
                {c.icon} {c.label}
              </button>
            ))}
          </div>
          <textarea value={message} onChange={(e) => setMessage(e.target.value)} rows={4}
            placeholder="무엇이 불편했나요? 어떤 기능이 필요한가요?"
            className={`${inputCls} resize-y p-3`} />
          <div className="flex items-center gap-3 mt-3">
            <input value={author} onChange={(e) => setAuthor(e.target.value)} placeholder="이름(선택)"
              className={`${inputCls} w-40`} />
            <button onClick={submit} disabled={busy}
              className="text-sm px-4 py-2 rounded-lg bg-zinc-100 text-zinc-900 hover:bg-white disabled:opacity-50">
              {busy ? "등록 중…" : "의견 보내기"}
            </button>
            {msg && <span className={`text-xs ${msg.ok ? "text-emerald-400" : "text-red-400"}`}>{msg.ok ? "✓" : "✗"} {msg.text}</span>}
          </div>
        </section>

        {/* 집계 */}
        {stats && stats.total > 0 && (
          <div className="flex flex-wrap gap-2 mb-3 text-[11px] text-zinc-400">
            <span className="font-medium text-zinc-300">총 {stats.total}건</span>
            {Object.entries(stats.by_category || {}).map(([k, v]) => (
              <span key={k} className="px-2 py-0.5 rounded-full bg-zinc-900/60 border border-zinc-800">{CAT(k).icon} {CAT(k).label} {v as number}</span>
            ))}
          </div>
        )}

        {/* 목록 */}
        <div className="space-y-3">
          {items.length === 0 ? (
            <div className="text-center text-zinc-400 py-12 text-sm">아직 접수된 의견이 없습니다. 첫 의견을 남겨주세요.</div>
          ) : items.map((it) => (
            <section key={it.id} className="bg-zinc-900/60 rounded-xl border border-zinc-800 p-4">
              <div className="flex items-center gap-2 mb-1 flex-wrap">
                <span className="text-sm">{CAT(it.category).icon} {CAT(it.category).label}</span>
                <span className={`text-[11px] px-2 py-0.5 rounded-full ${STATE_BADGE[it.state] ?? "bg-zinc-800"}`}>{STATE_LABEL[it.state] ?? it.state}</span>
                <span className="font-mono text-[10px] text-zinc-400">{it.id}</span>
                {it.author && <span className="text-[11px] text-zinc-400">· {it.author}</span>}
                <span className="ml-auto text-[10px] text-zinc-400">{it.created_at?.replace("T", " ")}</span>
              </div>
              <div className="text-sm text-zinc-300 whitespace-pre-wrap leading-relaxed">{it.message}</div>
              <div className="mt-2 flex gap-1.5">
                {(["triaged", "resolved", "wont_fix"] as const).map((s) => (
                  <button key={s} onClick={() => setState(it.id, s)} disabled={it.state === s}
                    className="text-[11px] px-2 py-0.5 rounded border border-zinc-800 text-zinc-400 hover:border-sky-600 hover:text-sky-400 disabled:opacity-40">
                    {STATE_LABEL[s]}로
                  </button>
                ))}
              </div>
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
