import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import FailureAnalysis from './FailureAnalysis.tsx'
import Onboarding from './Onboarding.tsx'
import RcaQueue from './RcaQueue.tsx'

const API = (import.meta as any).env?.VITE_API ?? "http://127.0.0.1:8001";

function Root() {
  const [status, setStatus] = useState<any | undefined>(undefined); // undefined = 로딩
  const [forceCfg, setForceCfg] = useState(false);                  // 설정 다시 열기
  const [view, setView] = useState<"app" | "rca">("app");
  const [pending, setPending] = useState(0);
  const load = () => fetch(`${API}/config/status`).then((r) => r.json())
    .then((s) => { setStatus(s); setForceCfg(false); })
    .catch(() => setStatus({ ready: false, _err: true }));
  const loadPending = () => fetch(`${API}/rca/pending`).then((r) => r.json())
    .then((d) => setPending(d.counts?.pending ?? 0)).catch(() => {});
  useEffect(() => { load(); loadPending(); }, []);

  const showOnboarding = status !== undefined && (forceCfg || !status.ready);

  return (
    <div className="h-screen flex flex-col">
      <nav className="flex items-center gap-1 px-4 h-11 bg-slate-900 text-slate-300 text-sm shrink-0">
        <span className="font-semibold text-white mr-3">LSI Error Analysis</span>
        <span className="px-3 py-1 rounded bg-indigo-600 text-white">불량 분석 추천</span>
        {status?.ready && !showOnboarding && (
          <div className="ml-auto flex items-center gap-2">
            <button onClick={() => { setView(view === "rca" ? "app" : "rca"); loadPending(); }}
              title="RCA 댓글 승인 대기 (HITL)"
              className={`px-2 py-0.5 rounded ${view === "rca" ? "bg-indigo-600 text-white" : "text-slate-300 hover:text-white"}`}>
              📤 승인 대기{pending > 0 ? ` ${pending}` : ""}
            </button>
            <button onClick={() => setForceCfg(true)} title="설정 변경"
              className="text-slate-400 hover:text-white px-2">⚙️ 설정</button>
          </div>
        )}
      </nav>
      <div className="flex-1 min-h-0">
        {status === undefined ? (
          <div className="h-full flex items-center justify-center text-slate-400 text-sm">설정 확인 중…</div>
        ) : showOnboarding ? (
          <Onboarding status={status} onDone={load} />
        ) : view === "rca" ? (
          <RcaQueue onBack={() => { setView("app"); loadPending(); }} onChange={loadPending} />
        ) : (
          <FailureAnalysis onQueueChange={loadPending} />
        )}
      </div>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
