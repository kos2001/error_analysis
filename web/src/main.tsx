import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import FailureAnalysis from './FailureAnalysis.tsx'
import Onboarding from './Onboarding.tsx'
import RcaQueue from './RcaQueue.tsx'
import VocPage from './VocPage.tsx'
import Dashboard from './Dashboard.tsx'
import { useRoute, type Route } from './useDeepLink'

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

  // 네비게이션 항목 — 사이드바를 하나 더 두면 3분할(목록/본문/그래프)이 좁아지므로
  // 상단바를 유지하되, 하네스의 활성/비활성 톤(zinc-800 채움 vs zinc-400 글자)을 쓴다.
  const navItem = (view: Route["view"], icon: string, label: string, title: string,
                   onClick?: () => void, badge?: number) => {
    const active = route.view === view && !showOnboarding;
    return (
      <button onClick={onClick ?? (() => go({ view } as Route))} title={title}
        className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm transition-colors outline-none focus-visible:ring-1 focus-visible:ring-sky-500 ${
          active ? "bg-zinc-800 font-medium text-zinc-50" : "text-zinc-300 hover:bg-zinc-900 hover:text-zinc-200"}`}>
        <span className="w-4 text-center text-zinc-400">{icon}</span>
        {label}
        {badge ? (
          <span className="rounded-full bg-sky-500/15 px-1.5 text-[11px] font-medium text-sky-300">{badge}</span>
        ) : null}
      </button>
    );
  };

  return (
    <div className="h-screen flex flex-col bg-zinc-950 text-zinc-200">
      <nav className="flex shrink-0 items-center gap-1 border-b border-zinc-800 bg-zinc-950 px-4 h-14">
        <button onClick={() => go({ view: "app" })} title="홈(분석 화면)으로"
          className="mr-4 text-left outline-none focus-visible:ring-1 focus-visible:ring-sky-500 rounded">
          <span className="block text-sm font-semibold tracking-tight text-zinc-50">LSI 불량 분석</span>
          <span className="block text-[11px] text-zinc-400">과거 해결 사례 기반 근본원인 추천</span>
        </button>
        {navItem("app", "◎", "분석", "미해결 이슈의 근본원인·해결책 추천")}
        {status?.ready && navItem("dashboard", "▤", "지식 현황", "KB 구성·품질·중복·모순·공백·효능")}
        {status?.ready && !showOnboarding && (
          <div className="ml-auto flex items-center gap-1">
            {navItem("rca", "↗", "승인 대기", "RCA 댓글 승인 대기 (HITL)",
              () => { go({ view: route.view === "rca" ? "app" : "rca" }); loadPending(); }, pending)}
            {navItem("voc", "✎", "VOC", "서비스 의견 (VOC)",
              () => go({ view: route.view === "voc" ? "app" : "voc" }))}
            {navItem("settings", "⚙", "설정", "설정 변경")}
          </div>
        )}
      </nav>
      <div className="flex-1 min-h-0">
        {status === undefined ? (
          <div className="h-full flex items-center justify-center text-zinc-400 text-sm">설정 확인 중…</div>
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
