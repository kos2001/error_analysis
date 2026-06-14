"""저자·소유권 메타데이터 — 지식의 작성/검증 주체를 기록한다.

배경(지식자산화 갭 P3-9):
  레코드에 author/validator가 없어 find-the-expert(누구에게 물을지), 저자 신뢰
  가중, 책임성이 불가능했다.

구성(git 추적 data/ownership.json):
  - set_owner(key): 사례/기사의 author·validator·role 오버레이.
  - experts_for(category/template): 큐레이션 지식(knowledge_store)·고장모드 기사
    ·오버레이의 저자를 고장 클래스별로 집계해 전문가 후보를 제시.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE_FILE = ROOT / "data" / "ownership.json"


def _load() -> dict:
    if STORE_FILE.exists():
        try:
            d = json.loads(STORE_FILE.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    return {}


def _save(d: dict) -> None:
    STORE_FILE.parent.mkdir(exist_ok=True)
    tmp = STORE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STORE_FILE)


def set_owner(key: str, *, author: str = "", validator: str = "", role: str = "") -> dict:
    if not (key or "").strip():
        raise ValueError("key 필수")
    d = _load()
    cur = d.get(key, {})
    cur.update({k: v for k, v in (("author", author), ("validator", validator), ("role", role)) if v})
    cur["updated_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    d[key] = cur
    _save(d)
    return {"key": key, **cur}


def get_owner(key: str) -> dict:
    return _load().get(key, {})


def _contributions() -> list[dict]:
    """모든 출처의 (author, category, key) 기여 레코드 — 전문가 집계 입력."""
    out = []
    overlay = _load()
    # 1) 큐레이션 지식(knowledge_store) — 승인자(author)
    try:
        import knowledge_store
        for r in knowledge_store.records():
            a = (overlay.get(r["key"], {}).get("author")) or r.get("author", "")
            if a:
                out.append({"author": a, "category": r.get("category", ""),
                            "key": r["key"], "source": "curated"})
    except Exception:
        pass
    # 2) 고장모드 기사(known_issues) — 작성자
    try:
        import failure_modes
        for a_art in failure_modes.articles():
            a = a_art.get("author", "")
            if a:
                for c in (a_art.get("categories") or [""]):
                    out.append({"author": a, "category": c, "key": a_art["id"], "source": "known_issue"})
    except Exception:
        pass
    # 3) 명시 오버레이(author/validator)
    for key, info in overlay.items():
        for a in (info.get("author"), info.get("validator")):
            if a:
                out.append({"author": a, "category": "", "key": key, "source": "overlay"})
    return out


def experts_for(category: str = "", template: str = "", top: int = 5) -> dict:
    """고장 클래스(분류)별 전문가 후보 — 기여 빈도순. category 미지정 시 전체."""
    contribs = _contributions()
    if category:
        contribs = [c for c in contribs if c["category"] == category]
    by_author = Counter(c["author"] for c in contribs)
    keys_by_author = defaultdict(set)
    for c in contribs:
        keys_by_author[c["author"]].add(c["key"])
    experts = [{"author": a, "contributions": n, "keys": sorted(keys_by_author[a])[:10]}
               for a, n in by_author.most_common(top)]
    return {"category": category or "(전체)", "experts": experts}


def stats() -> dict:
    d = _load()
    contribs = _contributions()
    return {"owned_keys": len(d),
            "distinct_authors": len({c["author"] for c in contribs}),
            "store_path": str(STORE_FILE.relative_to(ROOT))}
