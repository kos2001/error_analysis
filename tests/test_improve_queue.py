"""개선 제안 큐 검증 — 사람이 보는 목록이 실제로 정리되는가.

실제로 났던 문제: 생성 규칙을 고쳐 제안을 53건 → 7건으로 줄였는데, 저장된 큐는
그대로 53건이었다. sync() 가 추가만 하고 회수하지 않아 근거를 잃은 제안이 남았다.
수정이 사용자에게 전달되지 않은 것이다.

여기서 지키는 계약:
  · 사람의 결정(done/dismissed)은 어떤 경우에도 되살아나지 않는다
  · 근거를 잃은 open 제안은 superseded 로 회수된다 (지우지 않는다 — 추적 가능해야)
  · 생성이 통째로 실패했을 때(빈 목록) 멀쩡한 제안을 쓸어버리지 않는다
  · id 는 항목이 빠져도 겹치지 않는다

실행:
    .venv/bin/python tests/test_improve_queue.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import improve_queue as Q  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✓' if cond else '✗'} {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def fresh() -> None:
    """큐를 임시 파일로 격리 — 실제 data/improve_queue.json 을 건드리지 않는다."""
    Q.STORE_FILE = Path(tempfile.mkdtemp()) / "queue.json"


def prop(t: str, target: str, prio: str = "P3") -> dict:
    return {"type": t, "target": target, "priority": prio, "rationale": f"{t} {target}",
            "evidence": {"size": 3}}


def test_add_and_prune() -> None:
    print("\n[추가와 회수]")
    fresh()
    Q.sync([prop("promote_known_issue", f"LSI-{i}") for i in range(5)])
    check("5건 등록", len(Q.items("open")) == 5, str(Q.counts()))

    # 생성 규칙이 바뀌어 2건만 나온 상황
    r = Q.sync([prop("promote_known_issue", "LSI-0"), prop("promote_known_issue", "LSI-1")])
    check("회수 3건", r["superseded"] == 3, str(r))
    check("open 은 2건만 남는다", len(Q.items("open")) == 2, str(Q.counts()))
    check("지우지 않고 상태로 남긴다", len(Q.items()) == 5, str(len(Q.items())))
    gone = [it for it in Q.items("superseded")]
    check("회수 사유 기록", all(it.get("superseded_reason") for it in gone), str(gone[:1]))


def test_human_decisions_survive() -> None:
    print("\n[사람의 결정은 되살아나지 않는다]")
    fresh()
    Q.sync([prop("promote_known_issue", "LSI-9"), prop("normalize_ontology", "PLL")])
    done = Q.items("open")[0]["id"]
    Q.set_state(done, "dismissed")

    # 같은 제안이 다시 생성돼도 open 으로 돌아오면 안 된다
    r = Q.sync([prop("promote_known_issue", "LSI-9"), prop("normalize_ontology", "PLL")])
    check("거부는 유지", Q.counts()["dismissed"] == 1, str(Q.counts()))
    check("중복 추가 없음", r["added"] == 0, str(r))
    check("거부는 회수 대상도 아니다", r["superseded"] == 0, str(r))

    # 생성분에서 빠져도 거부 상태 그대로
    Q.sync([prop("normalize_ontology", "PLL")])
    check("빠져도 거부 유지", Q.counts()["dismissed"] == 1, str(Q.counts()))


def test_empty_generation_is_not_a_wipe() -> None:
    print("\n[생성 실패가 큐를 쓸어버리지 않는다]")
    fresh()
    Q.sync([prop("promote_known_issue", f"LSI-{i}") for i in range(4)])
    r = Q.sync([])          # 예: LLM/KB 로딩 실패로 아무것도 못 만든 경우
    check("빈 생성분은 회수하지 않는다", r["superseded"] == 0, str(r))
    check("open 4건 그대로", len(Q.items("open")) == 4, str(Q.counts()))

    r = Q.sync([prop("promote_known_issue", "LSI-0")], prune=False)
    check("prune=False 면 회수 안 함", r["superseded"] == 0 and len(Q.items("open")) == 4,
          str(Q.counts()))


def test_refresh_keeps_id() -> None:
    print("\n[근거 갱신]")
    fresh()
    Q.sync([prop("promote_known_issue", "LSI-5")])
    sid = Q.items("open")[0]["id"]
    g = prop("promote_known_issue", "LSI-5")
    g["rationale"], g["evidence"] = "군집이 9건으로 늘었다", {"size": 9}
    Q.sync([g])
    it = Q.items("open")[0]
    check("id 유지", it["id"] == sid, f"{sid} → {it['id']}")
    check("근거는 최신으로", it["evidence"]["size"] == 9 and "9건" in it["rationale"], str(it))


def test_id_collision() -> None:
    print("\n[id 충돌]")
    fresh()
    Q.sync([prop("promote_known_issue", f"LSI-{i}") for i in range(3)])
    # 과거 항목이 외부에서 삭제된 상황을 흉내 (len 기반 id 였다면 여기서 겹친다)
    its = json.loads(Q.STORE_FILE.read_text(encoding="utf-8"))
    Q.STORE_FILE.write_text(json.dumps(its[:1], ensure_ascii=False), encoding="utf-8")
    Q.sync([prop("promote_known_issue", "LSI-0"), prop("promote_known_issue", "LSI-77")])
    ids = [it["id"] for it in Q.items()]
    check("id 중복 없음", len(ids) == len(set(ids)), str(ids))


if __name__ == "__main__":
    test_add_and_prune()
    test_human_decisions_survive()
    test_empty_generation_is_not_a_wipe()
    test_refresh_keeps_id()
    test_id_collision()
    print("\n" + "=" * 56)
    if FAILS:
        print(f"실패 {len(FAILS)}건: " + " / ".join(FAILS))
        raise SystemExit(1)
    print("전부 통과")
