import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Msg = { role: "user" | "assistant"; content: string; violations?: string[] };

const API = (import.meta as any).env?.VITE_API ?? "http://127.0.0.1:8001";

function uid() {
  const k = "rvp_user_id";
  let v = localStorage.getItem(k);
  if (!v) {
    v = "web-" + Math.random().toString(36).slice(2, 10);
    localStorage.setItem(k, v);
  }
  return v;
}

export default function App() {
  const [messages, setMessages] = useState<Msg[]>([
    { role: "assistant", content: "안녕하세요! Robot Vision Platform 고객 지원입니다. 무엇을 도와드릴까요?" },
  ]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [memories, setMemories] = useState<string[]>([]);
  const [sessionId, setSessionId] = useState(() => "s-" + Date.now());
  const userId = useRef(uid()).current;
  const scrollRef = useRef<HTMLDivElement>(null);

  const refreshMemories = async () => {
    try {
      const r = await fetch(`${API}/memories?user_id=${userId}`);
      const j = await r.json();
      setMemories(j.memories ?? []);
    } catch {}
  };

  useEffect(() => {
    refreshMemories();
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const send = async () => {
    const text = input.trim();
    if (!text || streaming) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }, { role: "assistant", content: "" }]);
    setStreaming(true);
    try {
      const res = await fetch(`${API}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, user_id: userId, session_id: sessionId }),
      });
      if (!res.body) throw new Error("no stream");
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const payload = line.slice(6);
          if (payload === "[DONE]") continue;
          try {
            const event = JSON.parse(payload);
            if (event.delta) {
              setMessages((m) => {
                const last = m[m.length - 1];
                return [...m.slice(0, -1), { ...last, content: last.content + event.delta }];
              });
            } else if (event.replace) {
              setMessages((m) => {
                const last = m[m.length - 1];
                return [
                  ...m.slice(0, -1),
                  { ...last, content: event.replace, violations: event.violations },
                ];
              });
            } else if (event.violations !== undefined) {
              setMessages((m) => {
                const last = m[m.length - 1];
                return [...m.slice(0, -1), { ...last, violations: event.violations }];
              });
            }
          } catch {}
        }
      }
    } catch (e: any) {
      setMessages((m) => [...m.slice(0, -1), { role: "assistant", content: "[error] " + e.message }]);
    } finally {
      setStreaming(false);
      refreshMemories();
    }
  };

  const newSession = () => setSessionId("s-" + Date.now());

  return (
    <div className="h-full flex bg-slate-50 text-slate-900">
      <aside className="w-72 bg-slate-900 text-slate-100 p-5 flex flex-col gap-4">
        <div>
          <div className="text-xs uppercase tracking-widest text-slate-400">Robot Vision Platform</div>
          <div className="text-xl font-semibold mt-1">Support Agent</div>
        </div>
        <div className="text-xs text-slate-400 space-y-1">
          <div><span className="text-slate-500">user:</span> {userId}</div>
          <div><span className="text-slate-500">session:</span> {sessionId.slice(0, 14)}…</div>
        </div>
        <button
          onClick={newSession}
          className="text-sm bg-slate-700 hover:bg-slate-600 rounded-md py-2 px-3 transition"
        >
          + 새 세션
        </button>
        <div className="border-t border-slate-700 pt-4">
          <div className="text-xs uppercase tracking-widest text-slate-400 mb-2">기억된 정보</div>
          {memories.length === 0 ? (
            <div className="text-xs text-slate-500 italic">아직 학습된 정보가 없습니다.</div>
          ) : (
            <ul className="space-y-2 text-sm">
              {memories.map((m, i) => (
                <li key={i} className="bg-slate-800/60 rounded px-2 py-1.5 leading-snug">
                  {m}
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>

      <main className="flex-1 flex flex-col">
        <header className="border-b border-slate-200 bg-white px-6 py-4">
          <h1 className="font-semibold">RVP 고객 지원 (Agentic RAG + Memory)</h1>
          <p className="text-xs text-slate-500 mt-1">
            Agno · OpenRouter · LanceDB hybrid search · SQLite memory
          </p>
        </header>

        <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-6 space-y-4">
          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div
                className={`max-w-2xl px-4 py-3 rounded-2xl leading-relaxed text-[15px] shadow-sm ${
                  m.role === "user"
                    ? "bg-slate-900 text-white rounded-br-sm whitespace-pre-wrap"
                    : "bg-white text-slate-800 border border-slate-200 rounded-bl-sm prose prose-sm max-w-none prose-p:my-2 prose-headings:my-3 prose-pre:my-2 prose-li:my-0"
                }`}
              >
                {m.role === "assistant" ? (
                  m.content ? (
                    <>
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                      {m.violations && m.violations.length > 0 && (
                        <div className="mt-2 pt-2 border-t border-amber-200 text-xs text-amber-700">
                          ⚠️ 한자/중국어 {m.violations.length}자 감지 → 자동 한국어로 교정됨 ({m.violations.join(" ")})
                        </div>
                      )}
                    </>
                  ) : streaming && i === messages.length - 1 ? "…" : ""
                ) : (
                  m.content
                )}
              </div>
            </div>
          ))}
        </div>

        <div className="border-t border-slate-200 bg-white px-6 py-4">
          <div className="flex gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && (e.preventDefault(), send())}
              placeholder={streaming ? "응답 받는 중…" : "에러 코드, 설치, 빌링 등 무엇이든 물어보세요"}
              disabled={streaming}
              className="flex-1 px-4 py-3 rounded-lg border border-slate-300 focus:border-slate-900 focus:outline-none disabled:opacity-50"
            />
            <button
              onClick={send}
              disabled={streaming || !input.trim()}
              className="px-5 py-3 bg-slate-900 text-white rounded-lg disabled:opacity-40 hover:bg-slate-800 transition"
            >
              전송
            </button>
          </div>
          <div className="text-xs text-slate-400 mt-2">
            예: "E033이 떴어요" · "Pro 환불 정책은?" · "웹훅 서명은 어떻게 검증하나요?"
          </div>
        </div>
      </main>
    </div>
  );
}
