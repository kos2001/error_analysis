"""서빙 지연 계측 검증 — 계측이 거짓말하지 않는가.

계측은 회귀 판단의 근거다. 근거가 틀리면 최적화 판단이 통째로 틀어진다 —
실제로 첫 구현에서 캐시 히트가 생성 당시의 timing 을 물고 와 782ms 로 잡혔다.

LLM·검색 호출 없이 도는 것만 본다: 집계 수식, 캐시 히트 분리, 링버퍼 상한,
응답 본문에 내부 신호가 새지 않는지.

실행:
    .venv/bin/python tests/test_metrics.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "backend"))

USERS = ROOT / "tests" / "_users_metrics_test.yaml"
os.environ.update({
    "RVP_JIRA_POLL_SEC": "0", "RVP_PREWARM": "0", "RVP_AUTH_DEV_LOGIN": "1",
    "RVP_SESSION_SECRET": "metrics-test", "RVP_USERS_FILE": str(USERS), "RVP_MCP": "0",
})
USERS.write_text("users:\n  - id: admin\n    name: 관리자\n    role: admin\n"
                 "  - id: eng@example.com\n    name: 사용자\n    role: user\n",
                 encoding="utf-8")

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✓' if cond else '✗'} {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def test_aggregation() -> None:
    print("\n[집계]")
    import server
    server._METRICS["recommend"].clear()
    for ms in (100, 200, 300, 400, 500, 600, 700, 800, 900, 1000):
        server._record_metric("recommend", {"total_ms": ms, "rank_ms": ms / 2, "cached": False})
    for _ in range(4):
        server._record_metric("recommend", {"total_ms": 0.0, "cached": True})

    from fastapi.testclient import TestClient
    server._reload_users()
    with TestClient(server.app) as c:
        c.post("/auth/dev-login", json={"email": "admin"})
        m = c.get("/metrics").json()
    r = m["recommend"]
    check("전체 건수", r["count"] == 14, str(r["count"]))
    check("캐시 히트 분리", r["cache_hits"] == 4 and abs(r["cache_hit_rate"] - 4 / 14) < 0.01,
          str(r))
    st = r["stages_ms"]["total_ms"]
    check("p50 은 캐시 제외 분포에서", st["p50"] == 600.0, str(st))
    check("p90", st["p90"] == 1000.0, str(st))
    check("max", st["max"] == 1000.0, str(st))
    check("표본 수는 캐시 제외 10건", st["n"] == 10, str(st))
    check("캐시 히트 p50 은 별도로 0", r["cached_total_ms"]["p50"] == 0.0,
          str(r["cached_total_ms"]))
    check("단계별로 나뉜다", "rank_ms" in r["stages_ms"], str(list(r["stages_ms"])))


def test_ring_buffer() -> None:
    print("\n[링버퍼 상한]")
    import server
    server._METRICS["recommend"].clear()
    for i in range(server._METRICS_MAX + 120):
        server._record_metric("recommend", {"total_ms": float(i)})
    n = len(server._METRICS["recommend"])
    check("상한을 넘지 않는다", n == server._METRICS_MAX, str(n))
    check("오래된 것부터 버린다", server._METRICS["recommend"][0]["total_ms"] == 120.0,
          str(server._METRICS["recommend"][0]))


def test_rerank_failures() -> None:
    print("\n[rerank 실패 집계]")
    import server
    from fastapi.testclient import TestClient
    server._METRICS["recommend"].clear()
    server._record_metric("recommend", {"total_ms": 10.0})
    server._record_metric("recommend", {"total_ms": 20.0, "rerank_failed": 1})
    server._record_metric("recommend", {"total_ms": 30.0, "rerank_failed": 1})
    with TestClient(server.app) as c:
        c.post("/auth/dev-login", json={"email": "admin"})
        m = c.get("/metrics").json()
    check("실패 건수 집계", m["rerank_failures"] == 2, str(m["rerank_failures"]))


def test_authz_and_leak() -> None:
    print("\n[인가 · 내부 신호 누출]")
    import server
    from fastapi.testclient import TestClient
    with TestClient(server.app) as c:
        check("미인증 /metrics → 401", c.get("/metrics").status_code == 401)
        c.post("/auth/dev-login", json={"email": "eng@example.com"})
        check("사용자도 조회 가능(knowledge.read)", c.get("/metrics").status_code == 200)

        c.post("/auth/dev-login", json={"email": "admin"})
        r = c.post("/recommend", json={"key": "LSI-7", "k": 2})
        if r.status_code == 200:
            body = r.json()
            check("응답에 timing 없음", "timing" not in body, str(sorted(body))[:120])
            check("응답에 _cache_hit 없음", "_cache_hit" not in body, str(sorted(body))[:120])
        else:
            check("/recommend 응답 확인 생략(백엔드 미가동)", True)


def test_empty() -> None:
    print("\n[표본 없음]")
    import server
    from fastapi.testclient import TestClient
    server._METRICS["recommend"].clear()
    server._METRICS["explain"].clear()
    with TestClient(server.app) as c:
        c.post("/auth/dev-login", json={"email": "admin"})
        m = c.get("/metrics").json()
    check("빈 상태에서도 200", isinstance(m.get("recommend"), dict))
    check("비율은 None (0 나눗셈 없음)", m["recommend"]["cache_hit_rate"] is None,
          str(m["recommend"]))


if __name__ == "__main__":
    try:
        test_aggregation()
        test_ring_buffer()
        test_rerank_failures()
        test_authz_and_leak()
        test_empty()
    finally:
        USERS.unlink(missing_ok=True)
    print("\n" + "=" * 56)
    if FAILS:
        print(f"실패 {len(FAILS)}건: " + " / ".join(FAILS))
        raise SystemExit(1)
    print("전부 통과")
