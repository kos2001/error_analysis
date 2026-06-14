"""온톨로지 거버넌스 — 통제 어휘·동의어로 엔티티/분류를 정규화한다.

배경(지식자산화 갭 P2-6):
  category는 자유 Jira 필드(폴백 '기타'), 엔티티는 정규식 추출에 정규화가 없어
  같은 대상이 다른 토큰으로 흩어졌다(예: PM9C3 vs PM9C3-NVMe, throttle vs
  throttling). 장기 검색성·집계가 저하된다.

구성(git 추적 data/ontology.json — 버전·공유):
  - synonyms: {canonical: [alias, ...]}. normalize_entity로 alias→canonical 통합.
  - categories: 통제 분류 어휘(canonical 목록). canonical_category로 매핑.
  - review(records): 데이터에 등장하나 통제 어휘에 없는 용어를 빈도순으로 — 사람이
    canonical로 승격할 검토 큐.

빈 온톨로지면 모든 정규화는 항등(no-op) — 기존 동작과 하위호환. 사람이 동의어를
추가할 때만 통합이 일어난다(평가·임베딩 안정성 보존).
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from json_store import read_json, write_json_atomic

ROOT = Path(__file__).resolve().parent.parent
STORE_FILE = ROOT / "data" / "ontology.json"

_CACHE: dict | None = None


def _load() -> dict:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    loaded = read_json(STORE_FILE, {})
    d = {"synonyms": (loaded.get("synonyms") or {}) if isinstance(loaded, dict) else {},
         "categories": (loaded.get("categories") or []) if isinstance(loaded, dict) else []}
    _CACHE = d
    return d


def _save(d: dict) -> None:
    global _CACHE
    write_json_atomic(STORE_FILE, d)
    _CACHE = None  # 다음 _load에서 재적재


def _alias_map() -> dict:
    """alias(소문자) → canonical 역색인. canonical 자신도 포함."""
    out = {}
    for canon, aliases in _load()["synonyms"].items():
        out[canon.lower()] = canon
        for a in aliases:
            out[a.lower()] = canon
    return out


def normalize_entity(e: str) -> str:
    """alias면 canonical로, 아니면 원본 유지."""
    return _alias_map().get(e.lower(), e)


def normalize_entities(ents) -> set:
    return {normalize_entity(e) for e in ents}


def canonical_category(cat: str) -> str:
    """통제 분류로 매핑(동의어 경유). 통제 어휘에 없으면 원본 유지."""
    n = normalize_entity(cat)
    cats = _load()["categories"]
    return n if (not cats or n in cats) else n


def add_synonym(canonical: str, aliases: list[str]) -> dict:
    if not (canonical or "").strip():
        raise ValueError("canonical 필수")
    d = _load()
    cur = set(d["synonyms"].get(canonical, []))
    cur.update(a for a in (aliases or []) if a and a != canonical)
    d["synonyms"][canonical] = sorted(cur)
    _save(d)
    return {"canonical": canonical, "aliases": d["synonyms"][canonical]}


def set_categories(categories: list[str]) -> dict:
    d = _load()
    d["categories"] = sorted(set(c for c in (categories or []) if c))
    _save(d)
    return {"categories": d["categories"]}


def vocab() -> dict:
    return _load()


def review(records: list[dict], *, top: int = 40) -> dict:
    """통제 어휘에 없는 엔티티/분류를 빈도순으로 — 사람 검토(canonical 승격) 큐."""
    amap = _alias_map()
    cats_vocab = set(_load()["categories"])
    ent_freq, cat_freq = Counter(), Counter()
    for r in records:
        for e in r.get("entities", []):
            if e.lower() not in amap:                 # 아직 통제 어휘에 없음
                ent_freq[e] += 1
        c = r.get("category", "")
        if c and (cats_vocab and c not in cats_vocab):
            cat_freq[c] += 1
    return {
        "uncontrolled_entities": [{"term": t, "count": n} for t, n in ent_freq.most_common(top)],
        "uncontrolled_categories": [{"term": t, "count": n} for t, n in cat_freq.most_common(top)],
        "synonym_groups": len(_load()["synonyms"]),
        "controlled_categories": sorted(cats_vocab),
    }


def stats() -> dict:
    d = _load()
    return {"synonym_groups": len(d["synonyms"]),
            "total_aliases": sum(len(v) for v in d["synonyms"].values()),
            "controlled_categories": len(d["categories"]),
            "store_path": str(STORE_FILE.relative_to(ROOT))}
