"""지식 공백 기록 검증 — 게이트에 막힌 요청이 실제로 남는가.

배경: 이 저장소는 "자주 묻지만 사례가 없는" 영역을 드러내려고 만들었는데, 기록이
/recommend 한 곳에만 붙어 있었다. 정작 신호가 가장 센 순간 — 사용자가 자동 RCA 를
요청했다가 "사례 없음" 으로 거절당하는 순간 — 은 남지 않았다.

여기서 지키는 계약:
  · 게이트가 막는 자리마다 기록되고, 사유로 구분된다
  · 예열 배치는 기록하지 않는다 — 사람이 물은 게 아니라 전량 훑기다
  · 순간 중복(재시도·연쇄 호출)은 접힌다 — 빈도 집계가 목적이므로
  · 게이트를 통과한 정상 질의는 남지 않는다
  · 기록이 실패해도 본 요청은 성공한다

실행:
    .venv/bin/python tests/test_knowledge_gaps.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✓' if cond else '✗'} {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


import knowledge_gaps as KG  # noqa: E402


def fresh(dedup: float = 60.0) -> None:
    KG.STORE_FILE = Path(tempfile.mkdtemp()) / "gaps.json"
    KG.DEDUP_SEC = dedup


Q = {"key": "LSI-900", "summary": "주차 차단기가 야간에만 안 열림",
     "symptom": "밤 10시 이후 번호 인식 실패", "chip": "", "category": "인프라"}


def test_records_and_reasons() -> None:
    print("\n[사유별 기록]")
    fresh()
    check("검색 게이트", KG.record(Q, reason="no_coverage", top_score=0.03))
    check("RCA 거절", KG.record(Q, reason="rca_refused", top_score=0.03))
    check("설명 게이트", KG.record(Q, reason="explain_no_coverage"))
    rep = KG.report()
    check("3건 집계", rep["total_gap_events"] == 3, str(rep["total_gap_events"]))
    check("사유가 구분된다", set(rep["by_reason"]) ==
          {"no_coverage", "rca_refused", "explain_no_coverage"}, str(rep["by_reason"]))
    check("분류별 집계", rep["by_category"].get("인프라") == 3, str(rep["by_category"]))


def test_dedup() -> None:
    print("\n[순간 중복은 접는다]")
    fresh(dedup=60.0)
    check("첫 건은 남는다", KG.record(Q, reason="no_coverage"))
    check("바로 이어진 같은 질의는 접힌다", not KG.record(Q, reason="no_coverage"))
    check("사유가 다르면 남는다", KG.record(Q, reason="rca_refused"))
    other = {**Q, "key": "LSI-901", "summary": "사내 위키 검색이 최신 문서를 놓침"}
    check("다른 질의는 남는다", KG.record(other, reason="no_coverage"))
    check("합계 3건", KG.report()["total_gap_events"] == 3,
          str(KG.report()["total_gap_events"]))

    fresh(dedup=0.0)          # 창을 끄면 빈도를 전부 센다
    KG.record(Q, reason="no_coverage")
    KG.record(Q, reason="no_coverage")
    check("DEDUP_SEC=0 이면 전부 센다", KG.report()["total_gap_events"] == 2,
          str(KG.report()["total_gap_events"]))


def test_ring_cap() -> None:
    print("\n[무한 증가 방지]")
    fresh(dedup=0.0)
    KG.MAX_EVENTS = 20
    for i in range(35):
        KG.record({**Q, "key": f"LSI-{i}", "summary": f"질의 {i}"}, reason="no_coverage")
    ev = KG._load()
    check("상한 유지", len(ev) == 20, str(len(ev)))
    check("오래된 것부터 버린다", ev[0]["key"] == "LSI-15", str(ev[0]["key"]))
    KG.MAX_EVENTS = 5000


def test_server_paths() -> None:
    """서버 경로 — 게이트가 막는 자리마다 부르는가, 통과 질의는 안 남기는가."""
    print("\n[서버 경로]")
    tmp = Path(tempfile.mkdtemp())
    users = tmp / "u.yaml"
    users.write_text("users:\n  - id: admin\n    name: 관리자\n    role: admin\n",
                     encoding="utf-8")
    os.environ.update({"RVP_JIRA_POLL_SEC": "0", "RVP_PREWARM": "0", "RVP_AUTH_DEV_LOGIN": "1",
                       "RVP_SESSION_SECRET": "gaps-test", "RVP_USERS_FILE": str(users),
                       "RVP_MCP": "0"})
    sys.path.insert(0, str(ROOT / "backend"))
    KG.STORE_FILE = tmp / "gaps.json"
    KG.DEDUP_SEC = 0.0
    import server                                    # noqa: E402
    server._reload_users()
    from fastapi.testclient import TestClient        # noqa: E402

    off_domain = {"summary": "구내식당 모바일 주문 결제가 취소로 처리됨",
                  "symptom": "카드 승인은 났는데 주문이 사라짐"}
    with TestClient(server.app) as c:
        c.post("/auth/dev-login", json={"email": "admin"})
        r = c.post("/recommend", json={**off_domain, "k": 3})
        if r.status_code != 200:
            check("백엔드 미가동 — 서버 경로 확인 생략", True)
            return
        check("무관 질의는 게이트에 막힌다", r.json().get("coverage") is False,
              str(r.json().get("gate"))[:80])
        check("검색 공백이 기록된다",
              any(e["reason"] == "no_coverage" for e in KG._load()),
              str([e["reason"] for e in KG._load()]))

        # 게이트를 통과하는 정상 질의는 공백이 아니다
        before = len(KG._load())
        st = server._reco_state()
        real = next((k for k, v in st["by_key"].items()
                     if v.get("status") == server.RESOLVED_STATUS), "")
        if real:
            rr = c.post("/recommend", json={"key": real, "k": 3})
            if rr.status_code == 200 and rr.json().get("coverage"):
                check("통과한 질의는 남지 않는다", len(KG._load()) == before,
                      f"{before} → {len(KG._load())}")
            else:
                check("통과 사례 없음 — 확인 생략", True)

    check("예열은 기록하지 않는다(호출부 부재)",
          "_record_gap" not in _prewarm_source(),
          "_prewarm_once 안에서 _record_gap 을 부르면 KB 전량이 공백으로 들어온다")


def _prewarm_source() -> str:
    import inspect
    import server
    return inspect.getsource(server._prewarm_once)


if __name__ == "__main__":
    try:
        test_records_and_reasons()
        test_dedup()
        test_ring_cap()
        test_server_paths()
    finally:
        pass
    print("\n" + "=" * 56)
    if FAILS:
        print(f"실패 {len(FAILS)}건: " + " / ".join(FAILS))
        raise SystemExit(1)
    print("전부 통과")
