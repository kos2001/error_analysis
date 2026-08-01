/** 지식 현황 대시보드.
 *
 * 백엔드가 이미 계산하지만 화면이 없던 신호들을 한곳에 모은다 — KB 구성, 인입 품질,
 * 중복 클러스터, 지식 모순, 지식 공백, 추천 효능, 수명주기, 개선 큐, 기여자, 고장모드 기사.
 *
 * 카드마다 독립적으로 fetch 한다: 엔드포인트 하나가 실패해도 나머지는 보이고, 실패한
 * 카드만 재시도 버튼을 띄운다(전체 화면이 백지가 되는 것을 막는다).
 */

import { useCallback, useEffect, useState } from "react";
import { BarList, Donut, RateBar, StatTile, type BarItem } from "./charts";
import { ErrorNote, PageHeader, SectionTitle } from "./ui";

const API = (import.meta as any).env?.VITE_API ?? "http://127.0.0.1:8001";

/** 카드 하나의 로딩/실패/성공 상태를 담는 훅.
 *
 * rev 를 올리면 다시 읽는다 — 목록을 바꾸는 조작(개선 큐 상태 변경) 후 갱신용.
 */
function useEndpoint<T>(path: string, rev = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true); setError("");
    fetch(`${API}${path}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError(e.message || "불러오기 실패"); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [path, rev, tick]);
  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, error, loading, reload };
}

function Card({ title, hint, wide, children, error, loading, onRetry }: {
  title: string; hint?: string; wide?: boolean; children: React.ReactNode;
  error?: string; loading?: boolean; onRetry?: () => void;
}) {
  return (
    <section className={`rounded-xl border border-zinc-800 bg-zinc-900/60 p-5 ${wide ? "xl:col-span-2" : ""}`}>
      <SectionTitle hint={hint}>{title}</SectionTitle>
      {loading ? (
        <div className="space-y-2" aria-busy="true">
          {[0, 1, 2].map((i) => <div key={i} className="h-3 rounded bg-zinc-800 animate-pulse" />)}
        </div>
      ) : error ? (
        <ErrorNote message={error} action={onRetry && (
          <button onClick={onRetry} className="underline hover:text-red-300">다시 시도</button>
        )} />
      ) : children}
    </section>
  );
}

/** 값이 0인 것과 데이터가 없는 것을 구분해서 알린다. */
function Ok({ text }: { text: string }) {
  return <div className="text-xs text-emerald-400">✓ {text}</div>;
}

export default function Dashboard({ onOpenIssue }: { onOpenIssue: (key: string) => void }) {
  const reco = useEndpoint<any>("/reco/stats");
  const quality = useEndpoint<any>("/knowledge/quality");
  const clusters = useEndpoint<any>("/knowledge/clusters?threshold=0.80&min_size=2");
  const contra = useEndpoint<any>("/knowledge/contradictions");
  const gaps = useEndpoint<any>("/knowledge/gaps?top=8");
  const fb = useEndpoint<any>("/reco/feedback/stats");
  const outcomes = useEndpoint<any>("/knowledge/outcomes");
  const life = useEndpoint<any>("/knowledge/lifecycle/stats");
  const experts = useEndpoint<any>("/knowledge/experts?top=6");
  const articles = useEndpoint<any>("/knowledge/known-issues");
  const kstore = useEndpoint<any>("/knowledge/stats");
  const cache = useEndpoint<any>("/explain/cache");
  const [queueRev, setQueueRev] = useState(0);
  const queue = useEndpoint<any>("/improve/queue", queueRev);

  const setQueueState = async (id: string, state: string) => {
    try {
      await fetch(`${API}/improve/queue/state`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, state }),
      });
    } finally { setQueueRev((n) => n + 1); }
  };

  const cats: BarItem[] = Object.entries(reco.data?.by_category ?? {})
    .map(([k, v]) => ({ label: k || "(미분류)", value: v as number }))
    .sort((a, b) => b.value - a.value);

  const fillRows: [string, number][] = (() => {
    const f = quality.data?.report?.fill;
    if (!f) return [];
    const out: [string, number][] = [];
    for (const group of ["all_required", "resolved_critical", "resolved_recommended"] as const) {
      for (const [k, v] of Object.entries(f[group] ?? {})) out.push([k, v as number]);
    }
    if (typeof f.category_classified === "number") out.push(["category_classified", f.category_classified]);
    if (typeof f.resolved_verified === "number") out.push(["resolved_verified", f.resolved_verified]);
    return out;
  })();

  const openQueue = (queue.data?.items ?? []).filter((it: any) => it.state === "open");

  return (
    <div className="h-full overflow-y-auto bg-zinc-950 px-6 pb-10 pt-8 sm:px-8">
      <PageHeader title="지식 현황"
        description="KB 구성·품질·중복·모순·공백·효능 — 추천 품질을 좌우하는 지식 자산의 상태" />

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {/* KB 구성 */}
        <Card title="KB 구성" hint="분류별 해결 사례 분포" loading={reco.loading} error={reco.error}
          onRetry={reco.reload}>
          <div className="grid grid-cols-3 gap-2 mb-3">
            <StatTile label="해결 KB" value={reco.data?.resolved ?? "—"} sub="검색 대상" />
            <StatTile label="미해결" value={reco.data?.unresolved ?? "—"} sub="분석 대기" />
            <StatTile label="고장 템플릿" value={reco.data?.templates ?? "—"} sub="근본원인 클래스" />
          </div>
          <Donut items={cats} centerLabel="해결 사례" centerValue={reco.data?.resolved} />
          <div className="mt-2 text-[11px] text-zinc-400">검색 방식: {reco.data?.method ?? "—"}</div>
        </Card>

        {/* 인입 품질 */}
        <Card title="인입 품질" hint="필드 추출 성공률 — 낮으면 검색이 약해진다"
          loading={quality.loading} error={quality.error} onRetry={quality.reload}>
          <div className="space-y-1.5">
            {fillRows.length
              ? fillRows.map(([k, v]) => <RateBar key={k} label={k} value={v} />)
              : <div className="text-xs text-zinc-400">데이터 없음</div>}
          </div>
          {quality.data && (
            <div className="mt-3 pt-2 border-t border-zinc-800 text-xs">
              {quality.data.violations?.length ? (
                <div className="text-amber-400">⚠ 위반 {quality.data.violations.length}건: {quality.data.violations.join(" / ")}</div>
              ) : <Ok text="품질 게이트 위반 없음" />}
              {quality.data.report?.deficient_resolved_keys?.length > 0 && (
                <div className="mt-1 text-zinc-400">
                  필드 결손: {quality.data.report.deficient_resolved_keys.map((k: string) => (
                    <button key={k} onClick={() => onOpenIssue(k)}
                      className="font-mono text-sky-400 hover:underline mr-1.5">{k}</button>
                  ))}
                </div>
              )}
            </div>
          )}
        </Card>

        {/* 추천 효능 */}
        <Card title="추천 효능" hint="사람 피드백 + 실제 해결 여부"
          loading={fb.loading || outcomes.loading} error={fb.error || outcomes.error}
          onRetry={() => { fb.reload(); outcomes.reload(); }}>
          <div className="grid grid-cols-2 gap-2">
            <StatTile label="도움됨률" tone={(fb.data?.stats?.helpful_rate ?? 0) >= 0.7 ? "good" : "warn"}
              value={fb.data?.stats?.total ? `${Math.round((fb.data.stats.helpful_rate ?? 0) * 100)}%` : "—"}
              sub={`피드백 ${fb.data?.stats?.total ?? 0}건`}
              title="매치 카드의 👍/👎 집계" />
            <StatTile label="RCA 효능"
              tone={(outcomes.data?.efficacy_rate ?? 0) >= 0.5 ? "good" : "warn"}
              value={outcomes.data?.total_tracked ? `${Math.round((outcomes.data.efficacy_rate ?? 0) * 100)}%` : "—"}
              sub={`추적 ${outcomes.data?.total_tracked ?? 0}건 · 대기 ${outcomes.data?.pending ?? 0}`}
              title="게시된 RCA 이후 이슈가 실제로 해결된 비율" />
          </div>
          {fb.data?.stats?.top_helpful_matches?.length > 0 && (
            <div className="mt-3">
              <div className="text-[11px] text-zinc-400 mb-1">가장 도움된 사례</div>
              <BarList items={fb.data.stats.top_helpful_matches.map((m: any) => ({
                label: m.match_key, value: m.net, hint: `${m.match_key} 순추천 ${m.net}`,
                onClick: () => onOpenIssue(m.match_key),
              }))} unit="점" labelW={72} />
            </div>
          )}
          {fb.data?.stats?.total === 0 && (
            <div className="mt-3 text-[11px] text-zinc-400">
              아직 피드백이 없습니다 — 분석 화면의 매치 카드에서 👍/👎 를 누르면 쌓입니다.
            </div>
          )}
        </Card>

        {/* 중복 지식 */}
        <Card title="중복 지식 (클러스터)" wide
          hint="유사도 0.80 이상으로 뭉친 사례 — 고장모드 기사로 승격 대상"
          loading={clusters.loading} error={clusters.error} onRetry={clusters.reload}>
          {clusters.data?.count ? (
            <>
              <div className="text-xs text-zinc-400 mb-2">
                클러스터 {clusters.data.count}개 — 같은 근본원인이 여러 이슈에 흩어져 있다는 신호
              </div>
              <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
                {clusters.data.clusters.slice(0, 12).map((c: any) => (
                  <div key={c.representative} className="border border-zinc-800 rounded-lg p-2">
                    <div className="flex items-center gap-2 text-[11px] mb-1">
                      <span className="font-semibold text-zinc-300">{c.size}건</span>
                      <span className="text-zinc-400">평균 유사도 {c.avg_similarity}</span>
                      {c.chips?.length > 0 && (
                        <span className="px-1.5 rounded bg-zinc-800 text-zinc-300">{c.chips.join(", ")}</span>
                      )}
                      {c.categories?.length > 0 && (
                        <span className="px-1.5 rounded bg-sky-500/10 text-sky-400">{c.categories.join(", ")}</span>
                      )}
                      {c.verified_count > 0 && (
                        <span className="text-emerald-400">✓ 검증 {c.verified_count}</span>
                      )}
                    </div>
                    <div className="text-xs text-zinc-300 line-clamp-2 mb-1">
                      {c.sample_summaries?.[0]?.summary}
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {c.members.map((k: string) => (
                        <button key={k} onClick={() => onOpenIssue(k)}
                          className={`text-[10px] font-mono px-1.5 py-0.5 rounded border hover:bg-zinc-800 ${
                            k === c.representative
                              ? "border-sky-700/60 text-sky-300 font-semibold"
                              : "border-zinc-800 text-zinc-400"}`}
                          title={k === c.representative ? "대표 사례" : "클릭하면 분석 화면에서 엽니다"}>{k}</button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
              {clusters.data.count > 12 && (
                <div className="mt-2 text-[11px] text-zinc-400">상위 12개만 표시 (전체 {clusters.data.count}개)</div>
              )}
            </>
          ) : <Ok text="중복 클러스터 없음" />}
        </Card>

        {/* 개선 큐 */}
        <Card title="개선 큐" hint="자기개선 루프가 제안한 조치"
          loading={queue.loading} error={queue.error} onRetry={queue.reload}>
          {openQueue.length ? (
            <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
              {openQueue.map((it: any) => (
                <div key={it.id} className="border border-zinc-800 rounded-lg p-2">
                  <div className="flex items-center gap-1.5 text-[11px] mb-1">
                    <span className={`px-1.5 rounded font-semibold ${
                      it.priority === "P1" ? "bg-red-950/40 text-red-400"
                        : it.priority === "P2" ? "bg-amber-50 text-amber-400"
                        : "bg-zinc-800 text-zinc-300"}`}>{it.priority}</span>
                    <span className="text-zinc-400">{it.type}</span>
                    {it.target && (
                      <button onClick={() => onOpenIssue(it.target)}
                        className="font-mono text-sky-400 hover:underline">{it.target}</button>
                    )}
                  </div>
                  <div className="text-xs text-zinc-300">{it.rationale}</div>
                  <div className="mt-1.5 flex gap-1.5">
                    <button onClick={() => setQueueState(it.id, "done")}
                      className="text-[10px] px-2 py-0.5 rounded border border-emerald-800/60 text-emerald-300 hover:bg-emerald-950/40">
                      완료 처리
                    </button>
                    <button onClick={() => setQueueState(it.id, "dismissed")}
                      className="text-[10px] px-2 py-0.5 rounded border border-zinc-800 text-zinc-400 hover:bg-zinc-800">
                      보류
                    </button>
                  </div>
                </div>
              ))}
            </div>
          ) : <Ok text={`열린 제안 없음 (전체 ${queue.data?.items?.length ?? 0}건)`} />}
        </Card>

        {/* 지식 모순 */}
        <Card title="지식 모순" hint="같은 고장모드인데 근본원인이 엇갈리는 쌍"
          loading={contra.loading} error={contra.error} onRetry={contra.reload}>
          {contra.data?.count ? (
            <div className="space-y-2 max-h-60 overflow-y-auto pr-1">
              {contra.data.contradictions.map((c: any, i: number) => (
                <div key={i} className="border border-amber-900/60 bg-amber-950/40 rounded-lg p-2 text-xs">
                  <div className="flex items-center gap-1.5 mb-1">
                    <span className="text-zinc-400">사례 유사 {c.doc_similarity}</span>
                    <span className="text-amber-400 ml-auto">근본원인 유사 {c.root_cause_similarity}</span>
                  </div>
                  {([["a", c.a, c.summary_a, c.root_cause_a], ["b", c.b, c.summary_b, c.root_cause_b]] as const)
                    .map(([slot, key, summary, rc]) => (
                    <div key={slot} className="mt-1 pl-2 border-l-2 border-amber-300">
                      <button onClick={() => onOpenIssue(key)}
                        className="font-mono text-[11px] text-sky-400 hover:underline">{key}</button>
                      <div className="text-zinc-300 line-clamp-1">{summary}</div>
                      <div className="text-zinc-400 line-clamp-2">근본원인: {rc}</div>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          ) : (
            <>
              <Ok text="모순 없음" />
              <div className="mt-1 text-[11px] text-zinc-400">
                기준: 사례 유사도 ≥ {contra.data?.params?.sim_hi ?? "—"} 이면서 근본원인 유사도 ≤ {contra.data?.params?.rc_lo ?? "—"}
              </div>
            </>
          )}
        </Card>

        {/* 지식 공백 */}
        <Card title="지식 공백" hint="coverage 게이트를 통과 못한 질의 — KB가 비어 있는 영역"
          loading={gaps.loading} error={gaps.error} onRetry={gaps.reload}>
          {gaps.data?.total_gap_events ? (
            <>
              <div className="grid grid-cols-2 gap-2 mb-2">
                <StatTile label="공백 이벤트" value={gaps.data.total_gap_events} tone="warn" />
                <StatTile label="미충족 템플릿" value={gaps.data.top_underserved_templates?.length ?? 0} />
              </div>
              <BarList items={Object.entries(gaps.data.by_category ?? {})
                .map(([k, v]) => ({ label: k || "(미분류)", value: v as number }))
                .sort((a, b) => b.value - a.value)} />
            </>
          ) : (
            <>
              <Ok text="기록된 지식 공백 없음" />
              <div className="mt-1 text-[11px] text-zinc-400">
                게이트를 통과하지 못한 질의가 여기 쌓입니다 — 어떤 고장 유형의 사례가 부족한지 알려줍니다.
              </div>
            </>
          )}
        </Card>

        {/* 수명주기 + 큐레이션 저장소 */}
        <Card title="지식 수명주기" hint="폐기·대체된 사례와 신선도 반감기"
          loading={life.loading || kstore.loading} error={life.error || kstore.error}
          onRetry={() => { life.reload(); kstore.reload(); }}>
          <div className="grid grid-cols-2 gap-2">
            <StatTile label="폐기(deprecated)" value={life.data?.stats?.deprecated ?? "—"}
              tone={(life.data?.stats?.deprecated ?? 0) > 0 ? "warn" : "good"} />
            <StatTile label="대체(superseded)" value={life.data?.stats?.superseded ?? "—"}
              tone={(life.data?.stats?.superseded ?? 0) > 0 ? "warn" : "good"} />
            <StatTile label="신선도 반감기" value={`${life.data?.stats?.halflife_days ?? "—"}일`}
              title="이 기간이 지나면 사례의 신선도 점수가 절반이 된다" />
            <StatTile label="큐레이션 지식" value={kstore.data?.knowledge?.total ?? "—"}
              sub={kstore.data?.knowledge?.tracked_in_git ? "git 추적됨" : "git 미추적"}
              title="사람이 승인·수정해 KB에 환류된 RCA" />
          </div>
        </Card>

        {/* 고장모드 기사 */}
        <Card title="고장모드 기사 (Known-Issue)" hint="중복 사례를 하나로 묶은 정규 문서"
          loading={articles.loading} error={articles.error} onRetry={articles.reload}>
          {articles.data?.articles?.length ? (
            <div className="space-y-1.5 max-h-60 overflow-y-auto pr-1">
              {articles.data.articles.map((a: any) => (
                <div key={a.id} className="border border-zinc-800 rounded-lg p-2">
                  <div className="text-[11px] font-semibold text-sky-300">{a.id}</div>
                  <div className="text-xs text-zinc-300 line-clamp-2">{a.title}</div>
                </div>
              ))}
            </div>
          ) : (
            <>
              <Ok text="기사 없음" />
              <div className="mt-1 text-[11px] text-zinc-400">
                분석 화면에서 "📚 고장모드 기사로 묶기"로 만들 수 있습니다.
              </div>
            </>
          )}
        </Card>

        {/* 분석 캐시·예열 */}
        <Card title="분석 캐시 · 예열" hint="같은 입력이면 다시 생성하지 않는다"
          loading={cache.loading} error={cache.error} onRetry={cache.reload}>
          <div className="grid grid-cols-2 gap-2">
            <StatTile label="저장된 분석" value={cache.data?.cache?.entries ?? "—"}
              sub={cache.data?.cache?.bytes != null ? `${Math.round(cache.data.cache.bytes / 1024)} KB` : ""}
              title="질의·근거·모델·프롬프트 버전으로 주소가 정해지는 콘텐츠 캐시" />
            <StatTile label="예열 생성" value={cache.data?.prewarm?.done ?? "—"}
              tone={cache.data?.prewarm?.failed ? "warn" : "neutral"}
              sub={`건너뜀 ${cache.data?.prewarm?.skipped ?? 0} · 실패 ${cache.data?.prewarm?.failed ?? 0}`}
              title="이미 캐시에 있으면 건너뛴다" />
          </div>
          <div className="mt-2 text-[11px] text-zinc-400">
            프롬프트 버전 {cache.data?.cache?.prompt_version ?? "—"}
            {cache.data?.prewarm?.running && <span className="ml-2 text-sky-400">예열 진행 중…</span>}
            {cache.data?.prewarm?.error && <span className="ml-2 text-amber-400">최근 오류: {cache.data.prewarm.error}</span>}
          </div>
          <div className="mt-3 flex gap-1.5">
            <button onClick={async () => {
                await fetch(`${API}/explain/prewarm`, { method: "POST" }).catch(() => {});
                cache.reload();
              }}
              className="rounded border border-zinc-600 px-2 py-0.5 text-[10px] text-zinc-300 hover:bg-zinc-800">
              지금 예열
            </button>
            <button onClick={async () => {
                await fetch(`${API}/explain/cache`, { method: "DELETE" }).catch(() => {});
                cache.reload();
              }}
              title="프롬프트를 바꿨는데 버전을 안 올렸을 때만 필요합니다"
              className="rounded border border-zinc-700 px-2 py-0.5 text-[10px] text-zinc-400 hover:bg-zinc-800">
              캐시 비우기
            </button>
          </div>
        </Card>

        {/* 기여 전문가 */}
        <Card title="기여 전문가" hint="RCA 승인·수정 기여자"
          loading={experts.loading} error={experts.error} onRetry={experts.reload}>
          {experts.data?.experts?.length ? (
            <BarList items={experts.data.experts.map((e: any) => ({
              label: e.author, value: e.contributions, hint: `${e.author}: ${e.keys?.join(", ")}`,
            }))} unit="건" labelW={150} />
          ) : <div className="text-xs text-zinc-400">데이터 없음</div>}
        </Card>
      </div>
    </div>
  );
}
