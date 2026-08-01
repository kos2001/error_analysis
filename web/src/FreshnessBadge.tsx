/** KB 신선도 배지 — 지금 보는 데이터가 언제 Jira와 맞춰진 것인지 보여준다.
 *
 * 폴러(RVP_JIRA_POLL_SEC)가 도는 상태에서도 화면에는 아무 표시가 없어서 사용자가
 * 데이터 시점을 알 수 없었다. 폴러가 꺼져 있거나 오류 중이면 경고로 바꾸고,
 * 기다리지 않고 즉시 맞출 수 있는 수동 동기화 버튼을 함께 둔다.
 */

import { useCallback, useEffect, useState } from "react";

const API = (import.meta as any).env?.VITE_API ?? "http://127.0.0.1:8001";

type SyncStatus = {
  poll_interval_sec: number;
  running: boolean;
  error: string | null;
  last: { changed: boolean; upserted: number; deleted: number; mode: string } | null;
  state: { last_sync_ts?: number } | null;
};

function ago(sec: number): string {
  if (sec < 60) return `${Math.max(0, Math.round(sec))}초 전`;
  if (sec < 3600) return `${Math.round(sec / 60)}분 전`;
  if (sec < 86400) return `${Math.round(sec / 3600)}시간 전`;
  return `${Math.round(sec / 86400)}일 전`;
}

export default function FreshnessBadge({ onSynced }: { onSynced?: () => void }) {
  const [st, setSt] = useState<SyncStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(() => {
    fetch(`${API}/jira/sync/status`).then((r) => r.json()).then(setSt).catch(() => setSt(null));
  }, []);

  useEffect(() => {
    load();
    // 표시용 갱신 — 폴러 주기와 무관하게 10초마다 상태를 다시 읽고 경과시간을 다시 그린다.
    const t = setInterval(() => { setNow(Date.now()); load(); }, 10_000);
    return () => clearInterval(t);
  }, [load]);

  const syncNow = async () => {
    setBusy(true);
    try {
      const d = await fetch(`${API}/jira/sync`, { method: "POST" }).then((r) => r.json());
      if (d?.changed) onSynced?.();
    } catch { /* 배지 실패는 분석 흐름을 막지 않는다 */ } finally {
      setBusy(false); setNow(Date.now()); load();
    }
  };

  if (!st) return null;
  const ts = st.state?.last_sync_ts;
  const elapsed = ts ? (now / 1000 - ts) : null;
  const off = st.poll_interval_sec <= 0;
  const broken = !!st.error || (!st.running && !off);
  // 하네스 배지 규칙: rounded-full border, <색>-950/60 배경 + <색>-400 글자.
  const tone = broken ? "border-red-900/60 bg-red-950/60 text-red-400"
    : off ? "border-zinc-700/60 bg-zinc-800/80 text-zinc-300"
    : "border-emerald-900/60 bg-emerald-950/60 text-emerald-400";
  const label = broken ? "동기화 오류"
    : off ? "자동 동기화 꺼짐"
    : elapsed != null ? `${ago(elapsed)} 동기화` : "동기화 대기";

  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${tone}`}
      title={[
        `Jira 자동 동기화 ${off ? "비활성" : `${st.poll_interval_sec}초 주기`}`,
        st.error ? `오류: ${st.error}` : "",
        st.last ? `최근: ${st.last.mode} · 갱신 ${st.last.upserted}건 · 삭제 ${st.last.deleted}건` : "",
      ].filter(Boolean).join("\n")}>
      <span aria-hidden="true">{broken ? "⚠" : off ? "⏸" : "●"}</span>
      {label}
      <button onClick={syncNow} disabled={busy}
        className="underline decoration-dotted underline-offset-2 hover:text-zinc-100 disabled:opacity-60"
        title="폴 주기를 기다리지 않고 지금 Jira와 맞춥니다">
        {busy ? "동기화 중…" : "지금"}
      </button>
    </span>
  );
}
