import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import FailureAnalysis from './FailureAnalysis.tsx'
import Onboarding from './Onboarding.tsx'
import RcaQueue from './RcaQueue.tsx'
import VocPage from './VocPage.tsx'
import Dashboard from './Dashboard.tsx'
import { useRoute } from './useDeepLink'

const API = (import.meta as any).env?.VITE_API ?? "http://127.0.0.1:8001";

function Root() {
  const [status, setStatus] = useState<any | undefined>(undefined); // undefined = 로딩
  const [pending, setPending] = useState(0);
  // 화면 상태를 URL 해시로 옮겼다 — 새로고침·뒤로가기·링크 공유가 동작하고,
  // 대시보드에서 이슈로 바로 들어오는 드릴다운도 같은 경로를 쓴다.
  const [route, go] = useRoute();
  const load = () => fetch(`${API}/config/status`).then((r) => r.json())
    .then((s) => { setStatus(s); if (route.view === "settings") go({ view: "app" }, false); })
    .catch(() => setStatus({ ready: false, _err: true }));
  const loadPending = () => fetch(`${API}/rca/pending`).then((r) => r.json())
    .then((d) => setPending(d.counts?.pending ?? 0)).catch(() => {});
  useEffect(() => { load(); loadPending(); }, []);

  // 설정이 안 끝났으면 온보딩을 강제한다(라우트와 무관).
  const showOnboarding = status !== undefined && (route.view === "settings" || !status.ready);
  const jiraBase = status?.jira?.base_url ?? "";

  const tab = (view: "app" | "dashboard", label: string, title: string) => (
    <button onClick={() => go({ view })} title={title}
      className={`px-3 py-1 rounded ${route.view === view && !showOnboarding
        ? "bg-indigo-600 text-white" : "text-slate-300 hover:text-white"}`}>
      {label}
    </button>
  );

  return (
    <div className="h-screen flex flex-col">
      <nav className="flex items-center gap-1 px-4 h-11 bg-slate-900 text-slate-300 text-sm shrink-0">
        <button onClick={() => go({ view: "app" })} title="홈(분석 화면)으로"
          className="font-semibold text-white mr-3 hover:opacity-80">🏠 LSI Error Analysis</button>
        {tab("app", "불량 분석 추천", "미해결 이슈의 근본원인·해결책 추천")}
        {status?.ready && tab("dashboard", "📊 지식 현황", "KB 구성·품질·중복·모순·공백·효능")}
        {status?.ready && !showOnboarding && (
          <div className="ml-auto flex items-center gap-2">
            <button onClick={() => { go({ view: route.view === "rca" ? "app" : "rca" }); loadPending(); }}
              title="RCA 댓글 승인 대기 (HITL)"
              className={`px-2 py-0.5 rounded ${route.view === "rca" ? "bg-indigo-600 text-white" : "text-slate-300 hover:text-white"}`}>
              📤 승인 대기{pending > 0 ? ` ${pending}` : ""}
            </button>
            <button onClick={() => go({ view: route.view === "voc" ? "app" : "voc" })}
              title="서비스 의견 (VOC)"
              className={`px-2 py-0.5 rounded ${route.view === "voc" ? "bg-indigo-600 text-white" : "text-slate-300 hover:text-white"}`}>
              💬 VOC
            </button>
            <button onClick={() => go({ view: "settings" })} title="설정 변경"
              className="text-slate-400 hover:text-white px-2">⚙️ 설정</button>
          </div>
        )}
      </nav>
      <div className="flex-1 min-h-0">
        {status === undefined ? (
          <div className="h-full flex items-center justify-center text-slate-400 text-sm">설정 확인 중…</div>
        ) : showOnboarding ? (
          <Onboarding status={status} onDone={load} />
        ) : route.view === "rca" ? (
          <RcaQueue onBack={() => { go({ view: "app" }); loadPending(); }} onChange={loadPending} />
        ) : route.view === "voc" ? (
          <VocPage onBack={() => go({ view: "app" })} />
        ) : route.view === "dashboard" ? (
          <Dashboard onOpenIssue={(key) => go({ view: "app", key })} />
        ) : (
          <FailureAnalysis
            onQueueChange={loadPending}
            routeKey={route.view === "app" ? route.key : undefined}
            onSelectKey={(key) => go({ view: "app", key }, false)}
            jiraBase={jiraBase}
          />
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
