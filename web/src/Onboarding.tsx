import { useEffect, useState } from "react";

const API = (import.meta as any).env?.VITE_API ?? "http://127.0.0.1:8001";

type TestResult = { ok: boolean; error?: string; user?: string; project?: string; models?: number } | null;
type HStep = { key: string; label: string; done: boolean; auto: boolean; hint: string };
type HProbe = { installed: boolean; profile_exists: boolean; has_key: boolean; model: string; ready: boolean; steps: HStep[] } | null;

function Field({ label, hint, ...rest }: { label: string; hint?: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-slate-600">{label}</span>
      <input {...rest}
        className="mt-1 w-full px-3 py-2 text-sm rounded-lg border border-slate-300 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-400" />
      {hint && <span className="text-[11px] text-slate-400 mt-0.5 block">{hint}</span>}
    </label>
  );
}

function TestBadge({ r }: { r: TestResult }) {
  if (!r) return null;
  return r.ok
    ? <div className="text-xs text-emerald-600 bg-emerald-50 rounded px-2 py-1">✓ 연결 성공{r.user ? ` · ${r.user} / ${r.project}` : r.models != null ? ` · 모델 ${r.models}개` : ""}</div>
    : <div className="text-xs text-rose-600 bg-rose-50 rounded px-2 py-1">✗ {r.error || "실패"}</div>;
}

export default function Onboarding({ status, onDone }: { status: any; onDone: () => void }) {
  const h = status?.hermes ?? {}, j = status?.jira ?? {};
  // Hermes Gateway
  const [gatewayUrl, setGatewayUrl] = useState<string>(h.gateway_url ?? "https://openrouter.ai/api/v1");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState<string>(h.model ?? "");
  // Jira
  const [authType, setAuthType] = useState<"basic" | "pat">(j.auth_mode === "pat" ? "pat" : "basic");
  const [baseUrl, setBaseUrl] = useState<string>(j.base_url ?? "");
  const [projectKey, setProjectKey] = useState<string>(j.project_key ?? "");
  const [email, setEmail] = useState<string>(j.email ?? "");
  const [apiToken, setApiToken] = useState("");
  const [pat, setPat] = useState("");

  const [hTest, setHTest] = useState<TestResult>(null);
  const [jTest, setJTest] = useState<TestResult>(null);
  const [hTesting, setHTesting] = useState(false);
  const [jTesting, setJTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  // Hermes Agent(CLI 프로필) 셋업 점검/처리
  const [probe, setProbe] = useState<HProbe>(null);
  const [probing, setProbing] = useState(false);
  const [ensuring, setEnsuring] = useState(false);
  const reprobe = async () => { setProbing(true); try { setProbe(await fetch(`${API}/config/hermes/probe`).then((r) => r.json())); } catch { /* noop */ } finally { setProbing(false); } };
  const ensureProfile = async () => {
    setEnsuring(true);
    try { const r = await fetch(`${API}/config/hermes/ensure-profile`, { method: "POST" }).then((x) => x.json()); if (r.probe) setProbe(r.probe); }
    finally { setEnsuring(false); }
  };
  useEffect(() => { reprobe(); }, []);

  const hermesBody = () => ({ hermes: { gateway_url: gatewayUrl, api_key: apiKey, model } });
  const jiraBody = () => ({
    jira: {
      base_url: baseUrl, project_key: projectKey, email,
      api_token: authType === "basic" ? apiToken : "",
      pat: authType === "pat" ? pat : "",
    },
  });

  const post = (path: string, body: any) =>
    fetch(`${API}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => r.json());

  const testHermes = async () => { setHTesting(true); setHTest(null); try { setHTest(await post("/config/test/hermes", hermesBody())); } catch (e: any) { setHTest({ ok: false, error: e.message }); } finally { setHTesting(false); } };
  const testJira = async () => { setJTesting(true); setJTest(null); try { setJTest(await post("/config/test/jira", jiraBody())); } catch (e: any) { setJTest({ ok: false, error: e.message }); } finally { setJTesting(false); } };

  const hermesReady = !!gatewayUrl && !!model && (!!apiKey || h.has_key);
  const jiraReady = !!baseUrl && !!projectKey && (authType === "basic" ? (!!email && (!!apiToken || j.has_secret)) : (!!pat || j.has_secret));

  const save = async () => {
    setSaving(true); setErr("");
    try {
      const st = await post("/config", { ...hermesBody(), ...jiraBody() });
      if (st.ready) onDone();
      else setErr("설정이 완료되지 않았습니다. 필수 항목을 모두 채워주세요.");
    } catch (e: any) { setErr(e.message); } finally { setSaving(false); }
  };

  return (
    <div className="h-full overflow-y-auto bg-slate-100">
      <div className="max-w-2xl mx-auto p-6 sm:p-10">
        <div className="text-center mb-6">
          <h1 className="text-2xl font-bold text-slate-800">초기 설정</h1>
          <p className="text-sm text-slate-500 mt-1">서비스를 시작하려면 아래 두 가지 연동을 설정하세요.</p>
        </div>

        {/* Hermes Gateway */}
        <section className="bg-white rounded-2xl border border-slate-200 p-6 mb-5 shadow-sm">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-bold text-indigo-700">⚙️ Hermes Gateway (LLM)</h2>
            <span className={`text-xs px-2 py-0.5 rounded-full ${hermesReady ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-500"}`}>{hermesReady ? "준비됨" : "미설정"}</span>
          </div>
          <div className="space-y-3">
            <Field label="Gateway URL" value={gatewayUrl} onChange={(e) => setGatewayUrl(e.target.value)} placeholder="https://openrouter.ai/api/v1" />
            <Field label="API Key" type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
              placeholder={h.has_key ? "설정됨 — 변경 시에만 입력" : "sk-or-..."} hint={h.has_key ? "비워두면 기존 키 유지" : undefined} />
            <Field label="Model" value={model} onChange={(e) => setModel(e.target.value)} placeholder="deepseek/deepseek-v4-flash" />
            <div className="flex items-center gap-3">
              <button onClick={testHermes} disabled={hTesting}
                className="text-sm px-3 py-1.5 rounded-lg border border-indigo-300 text-indigo-600 hover:bg-indigo-50 disabled:opacity-50">{hTesting ? "테스트 중…" : "연결 테스트"}</button>
              <TestBadge r={hTest} />
            </div>

            {/* Hermes Agent(CLI 프로필) 셋업 — RVP_ENGINE=hermes 사용 시 */}
            <div className="mt-2 rounded-xl bg-slate-50 border border-slate-200 p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-slate-600">🤖 Hermes Agent (CLI 프로필) 셋업</span>
                <button onClick={reprobe} disabled={probing}
                  className="text-[11px] text-slate-500 hover:text-indigo-600 disabled:opacity-50">{probing ? "확인 중…" : "↻ 다시 확인"}</button>
              </div>
              {!probe ? (
                <div className="text-[11px] text-slate-400">상태 확인 중…</div>
              ) : (
                <>
                  <ul className="space-y-1.5">
                    {probe.steps.map((s) => (
                      <li key={s.key} className="text-xs">
                        <div className="flex items-center gap-2">
                          <span className={s.done ? "text-emerald-600" : "text-slate-300"}>{s.done ? "✓" : "○"}</span>
                          <span className={s.done ? "text-slate-600" : "text-slate-700 font-medium"}>{s.label}</span>
                          {!s.done && s.auto && (
                            <button onClick={ensureProfile} disabled={ensuring || !probe.installed}
                              className="ml-auto text-[11px] px-2 py-0.5 rounded bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40">{ensuring ? "등록 중…" : "자동 등록"}</button>
                          )}
                        </div>
                        {!s.done && (
                          <code className="block mt-0.5 ml-5 text-[10px] text-slate-500 bg-white border border-slate-200 rounded px-1.5 py-0.5">{s.hint}</code>
                        )}
                      </li>
                    ))}
                  </ul>
                  <div className="mt-2 text-[11px]">
                    {probe.ready
                      ? <span className="text-emerald-600">✓ Hermes agent 준비 완료{probe.model ? ` · 모델 ${probe.model}` : ""} (프로필 'lsi')</span>
                      : <span className="text-slate-400">설치·인증은 터미널에서 위 명령으로 진행하고, 프로필 등록은 '자동 등록'으로 처리하세요. (OpenRouter 게이트웨이만 쓰면 생략 가능)</span>}
                  </div>
                </>
              )}
            </div>
          </div>
        </section>

        {/* Jira (서비스 계정) */}
        <section className="bg-white rounded-2xl border border-slate-200 p-6 mb-5 shadow-sm">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-bold text-indigo-700">🔗 Jira 연동 (서비스 계정)</h2>
            <span className={`text-xs px-2 py-0.5 rounded-full ${jiraReady ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-500"}`}>{jiraReady ? "준비됨" : "미설정"}</span>
          </div>
          <div className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg p-2.5 mb-3 leading-relaxed">
            ⚠️ <b>전용 서비스(봇) 계정</b>을 사용하세요. RCA 댓글이 이 계정 명의로 게시됩니다.<br />
            최소 권한만 부여: <b>해당 프로젝트 조회 + 댓글 추가(Add Comments)</b>. 개인 계정·관리자 권한은 지양하세요.
          </div>
          <div className="space-y-3">
            <Field label="Base URL" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://your-domain.atlassian.net" />
            <Field label="Project Key" value={projectKey} onChange={(e) => setProjectKey(e.target.value)} placeholder="LSI" />
            <div className="flex gap-2 text-xs">
              <button onClick={() => setAuthType("basic")} className={`px-3 py-1 rounded-full ${authType === "basic" ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600"}`}>Cloud (email + API token)</button>
              <button onClick={() => setAuthType("pat")} className={`px-3 py-1 rounded-full ${authType === "pat" ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600"}`}>Server/DC (PAT)</button>
            </div>
            {authType === "basic" ? (
              <>
                <Field label="서비스 계정 Email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="rca-bot@company.com" />
                <Field label="API Token" type="password" value={apiToken} onChange={(e) => setApiToken(e.target.value)}
                  placeholder={j.has_secret ? "설정됨 — 변경 시에만 입력" : "서비스 계정 Atlassian API token"} hint={j.has_secret ? "비워두면 기존 토큰 유지" : undefined} />
              </>
            ) : (
              <Field label="Personal Access Token" type="password" value={pat} onChange={(e) => setPat(e.target.value)}
                placeholder={j.has_secret ? "설정됨 — 변경 시에만 입력" : "Bearer PAT"} hint={j.has_secret ? "비워두면 기존 PAT 유지" : undefined} />
            )}
            <div className="flex items-center gap-3">
              <button onClick={testJira} disabled={jTesting}
                className="text-sm px-3 py-1.5 rounded-lg border border-indigo-300 text-indigo-600 hover:bg-indigo-50 disabled:opacity-50">{jTesting ? "테스트 중…" : "연결 테스트"}</button>
              <TestBadge r={jTest} />
            </div>
          </div>
        </section>

        {err && <div className="text-sm text-rose-600 bg-rose-50 border border-rose-200 rounded-lg p-3 mb-4">{err}</div>}

        <button onClick={save} disabled={saving || !hermesReady || !jiraReady}
          className="w-full py-3 rounded-xl bg-gradient-to-r from-indigo-600 to-violet-600 text-white font-semibold hover:opacity-90 disabled:opacity-40 transition">
          {saving ? "저장 중…" : "저장하고 시작하기"}
        </button>
        <p className="text-center text-[11px] text-slate-400 mt-2">설정은 서버에 안전하게 저장되며, 다음 실행부터 이 화면은 생략됩니다.</p>
      </div>
    </div>
  );
}
