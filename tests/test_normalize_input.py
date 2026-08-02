"""입력 정규화 검증 — 같은 말이 같은 토큰이 되는가.

배경(실측 2026-08-02): 토크나이저의 한글 클래스 `[가-힣]` 는 완성형만 잡는다.
같은 "펌웨어" 라도 분해형(NFD)으로 들어오면 **한글 토큰이 통째로 0개**가 되고,
BM25 는 융합에서 가중치가 가장 큰 랭커라 결과가 무너졌다:

    confusable 34건, 질의만 NFD → P@1 1.000 → 0.294 · 게이트 1.000 → 0.882
    (막지도 않는다 — 틀린 답을 자신 있게 내놓는다)

NFD 는 특이한 입력이 아니다. macOS 파일명·일부 클립보드·일부 Jira 내보내기가 그렇다.

네트워크·임베딩을 쓰지 않는다 — 토큰과 엔티티만 본다.

실행:
    .venv/bin/python tests/test_normalize_input.py
"""

from __future__ import annotations

import sys
import unicodedata as ud
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from preprocess import extract_entities, normalize_text, tokenize   # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✓' if cond else '✗'} {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


BASE = "PM9C3-NVMe 펌웨어 타이밍 마진 확보 85°C 0.01%"


def wide(s: str) -> str:
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in s)


VARIANTS = {
    "NFD 분해형": ud.normalize("NFD", BASE),
    "전각": wide("PM9C3-NVMe") + " 펌웨어 타이밍 마진 확보 85°C 0.01%",
    "비분리 하이픈(U+2011)": BASE.replace("-", "‑"),
    "엔대시(U+2013)": BASE.replace("-", "–"),
    "제로폭 삽입(U+200B)": BASE.replace("NVMe", "NV​Me"),
    "비분리 공백(U+00A0)": BASE.replace(" ", " "),
}


def test_variants_collapse() -> None:
    print("\n[같은 말은 같은 토큰으로]")
    base_tok, base_ent = tokenize(BASE), extract_entities(BASE)
    check("기준 토큰이 비지 않는다", len(base_tok) >= 5, str(base_tok))
    check("기준 엔티티 추출", "PM9C3-NVMe" in base_ent, str(sorted(base_ent)))
    for label, v in VARIANTS.items():
        check(f"{label} → 토큰 동일", tokenize(v) == base_tok,
              f"{tokenize(v)} != {base_tok}")
        check(f"{label} → 엔티티 동일", extract_entities(v) == base_ent,
              f"{sorted(extract_entities(v))} != {sorted(base_ent)}")


def test_korean_survives_nfd() -> None:
    """이 테스트가 원래 사고를 직접 겨눈다 — NFD 에서 한글이 사라졌다."""
    print("\n[NFD 에서 한글이 사라지지 않는다]")
    nfd = ud.normalize("NFD", "펌웨어 타이밍 마진 확보")
    toks = tokenize(nfd)
    ko = [t for t in toks if any("가" <= c <= "힣" for c in t)]
    check("한글 토큰 4개", len(ko) == 4, str(toks))
    check("완성형으로 접힌다", ko == ["펌웨어", "타이밍", "마진", "확보"], str(ko))


def test_preserves_domain_tokens() -> None:
    """정규화가 도메인 토큰을 망가뜨리면 안 된다 — 고치려다 깨는 게 더 나쁘다."""
    print("\n[도메인 토큰 보존]")
    cases = {
        "0x1F40": "0x1f40",
        "GDC7.4.2.684": "gdc7.4.2.684",
        "HS-G4": "hs-g4",
        "85°C": "85°c",
        "0.01%": "0.01%",
        "Gen1": "gen1",
    }
    for src, want in cases.items():
        check(f"{src} 보존", want in tokenize(src), f"{tokenize(src)}")
    check("한글 조사 분리 유지", tokenize("gen1로 684에서") == ["gen1", "로", "684", "에서"],
          str(tokenize("gen1로 684에서")))


def test_normalize_is_idempotent_and_safe() -> None:
    print("\n[정규화 자체의 성질]")
    for label, v in VARIANTS.items():
        once = normalize_text(v)
        check(f"{label} 멱등", normalize_text(once) == once)
    check("빈 문자열 안전", normalize_text("") == "")
    check("공백만 있는 값", normalize_text("     ") == "")
    check("개행·연속 공백 접힘", normalize_text("a\n\n  b") == "a b",
          repr(normalize_text("a\n\n  b")))


def test_real_kb_unchanged() -> None:
    """현 KB 는 이미 깨끗하다 — 정규화가 기존 문서를 바꾸지 않는지 확인.

    바꾼다면 임베딩 캐시가 통째로 무효화되고 검색 결과도 달라진다. 그런 변화는
    의도한 것이어야 하고, 여기서 눈에 띄어야 한다.
    """
    print("\n[기존 KB 에 대한 영향]")
    import json
    from preprocess import parse_issue                       # noqa: E402
    raw = json.loads((ROOT / "data" / "all_raw_issues.json").read_text(encoding="utf-8"))
    recs = [parse_issue(x) for x in raw][:60]
    changed = [r["key"] for r in recs
               if normalize_text(r.get("summary", "")) != r.get("summary", "")]
    check("요약은 그대로다(대시는 본문에만 있었다)", not changed, str(changed[:5]))
    both = sum(1 for r in recs
               if normalize_text(r.get("root_cause", "")) != (r.get("root_cause") or ""))
    print(f"   참고: 본문(root_cause)이 바뀌는 레코드 {both}/{len(recs)}건 "
          f"— em-dash·연속 공백 접힘(의도)")


if __name__ == "__main__":
    test_variants_collapse()
    test_korean_survives_nfd()
    test_preserves_domain_tokens()
    test_normalize_is_idempotent_and_safe()
    test_real_kb_unchanged()
    print("\n" + "=" * 56)
    if FAILS:
        print(f"실패 {len(FAILS)}건: " + " / ".join(FAILS))
        raise SystemExit(1)
    print("전부 통과")
