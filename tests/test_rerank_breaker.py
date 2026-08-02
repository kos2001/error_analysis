"""재순위 차단기(circuit breaker) 검증 — 장애가 영구 열화로 굳지 않는가.

배경: 리랭커가 연속 실패하면 차단기가 열려 embed_cos 폴백 게이트로 내려간다.
그 상태는 **더 나쁜 검색이 아니라 더 좁은 통과**다 — 메타 없는 자유 문장에서 정답
통과가 1.000 → 0.947 로 떨어진다(실측, claudedocs E-6). 그래서 두 가지가 중요하다:

  1. 일시 장애면 **스스로 복구**해야 한다. 예전에는 한 번 열리면 KB 를 다시 만들
     때까지 닫히지 않아, 게이트웨이가 5분 끊긴 것만으로 그날 내내 열화 상태였다.
  2. 열려 있는 동안 **운영자가 알아야 한다**. rerank_failures 지표만 보면 안 된다 —
     차단기가 열리면 시도 자체를 안 하므로 실패가 기록되지 않고, 링버퍼가 밀려나면
     0 이 된다. 문제가 영구화되는 순간 지표가 사라지는 셈이다.

네트워크를 쓰지 않는다 — reranker.rerank 를 가짜로 갈아 끼운다.

실행:
    .venv/bin/python tests/test_rerank_breaker.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✓' if cond else '✗'} {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


from preprocess import parse_issue            # noqa: E402
from recommender import Recommender           # noqa: E402
import reranker                               # noqa: E402

STATE = {"fail": True, "calls": 0}


def fake_rerank(q, docs, model="", timeout=0):
    STATE["calls"] += 1
    if STATE["fail"]:
        raise RuntimeError("게이트웨이 다운")
    return [(i, 1.0 / (i + 1)) for i in range(len(docs))]


reranker.rerank = fake_rerank

KB = [r for r in (parse_issue(x) for x in
                  json.loads((ROOT / "data" / "all_raw_issues.json").read_text(encoding="utf-8")))
      if r["status"] == "완료"][:30]
Q = {"summary": "NVMe 컨트롤러 timeout", "symptom": "link down", "chip": "", "category": ""}


def build(**kw) -> Recommender:
    # 임베딩은 로컬 고정 — 이 테스트는 차단기 로직만 본다(네트워크·키 불필요).
    return Recommender(KB, method="bm25", rerank=True, signals=False,
                       embed_backend="fastembed", embed_model="", **kw)


def test_trips_after_limit() -> None:
    print("\n[연속 실패 → 차단]")
    STATE.update(fail=True, calls=0)
    rec = build(rerank_fail_limit=2, rerank_retry_sec=60)
    rec.recommend(Q, k=3)
    check("1회 실패로는 안 닫힌다", rec.rerank, f"rerank={rec.rerank}")
    rec.recommend(Q, k=3)
    check("한계 도달 시 차단", not rec.rerank, f"rerank={rec.rerank}")
    before = STATE["calls"]
    rec.recommend(Q, k=3)
    check("닫힌 동안 호출하지 않는다", STATE["calls"] == before,
          f"{before} → {STATE['calls']}")
    check("열린 시각 기록", rec._rerank_tripped_at > 0)


def test_self_recovery() -> None:
    print("\n[일시 장애는 스스로 복구]")
    STATE.update(fail=True, calls=0)
    rec = build(rerank_fail_limit=2, rerank_retry_sec=0.5)
    for _ in range(2):
        rec.recommend(Q, k=3)
    check("차단됨", not rec.rerank)

    STATE["fail"] = False                       # 게이트웨이 복구
    time.sleep(0.6)
    res = rec.recommend(Q, k=3)
    check("대기 후 재시도로 복구", rec.rerank, f"rerank={rec.rerank}")
    check("실패 카운터 초기화", rec._rerank_fails == 0, str(rec._rerank_fails))
    check("게이트가 rerank 로 복귀", (res.get("gate") or {}).get("signal") == "rerank",
          str((res.get("gate") or {}).get("signal")))


def test_still_broken_retrips_without_storm() -> None:
    print("\n[장애가 계속되면 다시 닫힌다 — 요청 폭주 없이]")
    STATE.update(fail=True, calls=0)
    rec = build(rerank_fail_limit=2, rerank_retry_sec=0.5)
    for _ in range(2):
        rec.recommend(Q, k=3)
    calls_after_trip = STATE["calls"]
    time.sleep(0.6)
    for _ in range(5):                           # 대기 경과 후 질의 5건
        rec.recommend(Q, k=3)
    check("다시 닫힘", not rec.rerank)
    check("재시도는 대기당 1회뿐", STATE["calls"] == calls_after_trip + 1,
          f"{calls_after_trip} → {STATE['calls']} (5건 질의)")


def test_retry_disabled() -> None:
    print("\n[재시도 끄기]")
    STATE.update(fail=True, calls=0)
    rec = build(rerank_fail_limit=2, rerank_retry_sec=0)
    for _ in range(2):
        rec.recommend(Q, k=3)
    STATE["fail"] = False
    time.sleep(0.1)
    rec.recommend(Q, k=3)
    check("retry_sec=0 이면 복구하지 않는다", not rec.rerank, f"rerank={rec.rerank}")


def test_metrics_exposes_degraded() -> None:
    """/metrics 가 **살아 있는 추천기**에서 열화 상태를 읽는가."""
    print("\n[운영 가시성]")
    import os
    users = ROOT / "tests" / "_users_breaker_test.yaml"
    users.write_text("users:\n  - id: admin\n    name: 관리자\n    role: admin\n",
                     encoding="utf-8")
    os.environ.update({"RVP_JIRA_POLL_SEC": "0", "RVP_PREWARM": "0", "RVP_AUTH_DEV_LOGIN": "1",
                       "RVP_SESSION_SECRET": "breaker-test", "RVP_USERS_FILE": str(users),
                       "RVP_MCP": "0"})
    sys.path.insert(0, str(ROOT / "backend"))
    try:
        import server                                     # noqa: E402
        from fastapi.testclient import TestClient         # noqa: E402
        server._reload_users()
        with TestClient(server.app) as c:
            c.post("/auth/dev-login", json={"email": "admin"})
            m = c.get("/metrics").json()
            rr = m.get("rerank") or {}
            check("rerank 상태가 노출된다", "enabled" in rr, str(rr)[:120])
            check("degraded 플래그", "degraded" in rr, str(rr)[:120])
            check("어느 게이트인지 알려준다", "gate" in rr, str(rr)[:120])

            # 살아 있는 추천기를 강제로 열화시키면 지표가 따라와야 한다
            reco = server._reco_state()["reco"]
            reco.rerank = False
            reco._rerank_fails = 3
            reco._rerank_tripped_at = time.time()
            m2 = c.get("/metrics").json()["rerank"]
            check("열화가 즉시 보인다", m2["degraded"] is True and m2["enabled"] is False,
                  str(m2))
            check("폴백 게이트임을 명시", "embed_cos" in m2["gate"], m2["gate"])
            check("열린 시각 노출", bool(m2["tripped_at"]), str(m2))
    finally:
        users.unlink(missing_ok=True)


def test_fail_closed_without_signals() -> None:
    """판정 신호가 하나도 없으면 **닫힌 채로 실패**하는가.

    임베딩과 재순위는 둘 다 외부 API 다 — 게이트웨이가 죽으면 동시에 없어진다.
    예전에는 이때 gate=None, coverage=bool(matches) 라 **무조건 통과**였다.
    BM25 는 무관 질의에도 늘 무언가를 돌려주므로, 구내식당 결제 문의에 칩 고장 사례가
    붙고 그 위에 LLM 근본원인이 생성됐다 — 게이트가 막으려던 바로 그 환각이다.
    """
    print("\n[판정 신호 없음 → 차단]")
    STATE.update(fail=True, calls=0)
    orig = Recommender._init_embed
    Recommender._init_embed = lambda self: (_ for _ in ()).throw(RuntimeError("임베딩 다운"))
    try:
        rec = Recommender(KB, method="hybrid_embed", rerank=False, signals=True,
                          embed_backend="fastembed", embed_model="")
    finally:
        Recommender._init_embed = orig

    check("임베딩 실패 시 method 강등", rec.method == "hybrid", rec.method)
    junk = {"summary": "구내식당 모바일 주문 결제가 취소로 처리됨",
            "symptom": "카드 승인은 났는데 주문이 사라짐", "chip": "", "category": ""}
    res = rec.recommend(junk, k=3)
    g = res.get("gate") or {}
    check("무관 질의가 통과하지 않는다", res["coverage"] is False, str(res["coverage"]))
    check("판정 불가를 명시한다", g.get("signal") == "none", str(g))
    check("available=False", g.get("available") is False, str(g))
    check("이유를 남긴다", bool(g.get("reason")), str(g))
    check("후보는 숨기지 않는다", g.get("candidates", 0) > 0 and len(res["matches"]) > 0,
          f"candidates={g.get('candidates')} matches={len(res['matches'])}")
    check("제안은 만들지 않는다", res.get("proposal") is None, str(res.get("proposal"))[:60])

    # 정상 질의도 마찬가지로 막힌다 — 판정을 못 하는 것이지 봐주는 게 아니다
    real = {"summary": "[PM9C3-NVMe] 고온 지속 쓰기 중 timeout", "symptom": "link down",
            "chip": "", "category": ""}
    check("정상 질의도 동일하게 차단(일관성)",
          rec.recommend(real, k=3)["coverage"] is False)


def test_gap_not_recorded_when_undecidable() -> None:
    """판정 불가는 '지식 공백' 이 아니다 — 장애 트래픽이 통계를 오염시키면 안 된다."""
    print("\n[판정 불가는 공백으로 세지 않는다]")
    import os
    import tempfile
    users = Path(tempfile.mkdtemp()) / "u.yaml"
    users.write_text("users:\n  - id: admin\n    name: 관리자\n    role: admin\n",
                     encoding="utf-8")
    os.environ.update({"RVP_JIRA_POLL_SEC": "0", "RVP_PREWARM": "0", "RVP_AUTH_DEV_LOGIN": "1",
                       "RVP_SESSION_SECRET": "gap-undec", "RVP_USERS_FILE": str(users),
                       "RVP_MCP": "0"})
    sys.path.insert(0, str(ROOT / "backend"))
    import knowledge_gaps as KG                                # noqa: E402
    KG.STORE_FILE = users.parent / "gaps.json"
    KG.DEDUP_SEC = 0.0
    import server                                              # noqa: E402
    from fastapi.testclient import TestClient                  # noqa: E402
    server._reload_users()
    junk = {"summary": "주차 차단기가 야간에만 안 열림", "symptom": "번호 인식 실패", "k": 3}
    with TestClient(server.app) as c:
        c.post("/auth/dev-login", json={"email": "admin"})
        c.post("/recommend", json=junk)
        n_normal = len(KG._load())
        check("정상 상태의 무관 질의는 공백으로 기록", n_normal >= 1, str(n_normal))

        reco = server._reco_state()["reco"]
        reco.rerank, reco.method, reco.signals, reco._kb_emb = False, "hybrid", False, None
        c.post("/recommend", json={**junk, "summary": junk["summary"] + " 재질의"})
        check("판정 불가는 공백에 안 들어간다", len(KG._load()) == n_normal,
              f"{n_normal} → {len(KG._load())}")


if __name__ == "__main__":
    test_trips_after_limit()
    test_self_recovery()
    test_still_broken_retrips_without_storm()
    test_retry_disabled()
    test_fail_closed_without_signals()
    test_metrics_exposes_degraded()
    test_gap_not_recorded_when_undecidable()
    print("\n" + "=" * 56)
    if FAILS:
        print(f"실패 {len(FAILS)}건: " + " / ".join(FAILS))
        raise SystemExit(1)
    print("전부 통과")
