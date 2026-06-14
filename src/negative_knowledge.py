"""부정 지식(기각된 가설) 저장소 — "시도했지만 아니었던 것"을 자산화한다.

배경(지식자산화 갭 P2-7):
  자산이 Jira 필드 위생에 종속돼, 시니어의 추론 경로와 기각된 가설(고가치
  부정지식)이 포착되지 않았다. "X를 의심했으나 Y 때문에 아니었다"는 재조사
  낭비를 막는 핵심 지식이다.

구성(git 추적 data/negative_knowledge.json — 버전·공유):
  - issue_key → [{hypothesis, reason, author, created_at}].
  - for_keys(keys): 질의/근거 사례의 기각 가설을 모아 심층 분석 프롬프트에 주입
    → LLM이 이미 기각된 가설을 재안하지 않도록.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE_FILE = ROOT / "data" / "negative_knowledge.json"


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


def add(key: str, hypothesis: str, reason: str, *, author: str = "") -> dict:
    """기각된 가설 1건 기록. 동일 (key, hypothesis)는 최신 사유로 갱신."""
    if not (key or "").strip() or not (hypothesis or "").strip():
        raise ValueError("key·hypothesis 필수")
    d = _load()
    items = [h for h in d.get(key, []) if h.get("hypothesis") != hypothesis]
    items.append({"hypothesis": hypothesis.strip(), "reason": (reason or "").strip(),
                  "author": author or "", "created_at": _dt.datetime.now().isoformat(timespec="seconds")})
    d[key] = items
    _save(d)
    return {"key": key, "count": len(items)}


def get(key: str) -> list[dict]:
    return _load().get(key, [])


def for_keys(keys) -> list[dict]:
    """여러 이슈의 기각 가설을 평탄화 [{key, hypothesis, reason}]."""
    d = _load()
    out = []
    for k in keys:
        for h in d.get(k, []):
            out.append({"key": k, "hypothesis": h.get("hypothesis", ""), "reason": h.get("reason", "")})
    return out


def prompt_block(keys) -> str:
    """심층 분석 프롬프트용 '기각된 가설(재안 금지)' 섹션. 없으면 빈 문자열."""
    rejected = for_keys(keys)
    if not rejected:
        return ""
    lines = "\n".join(f"- [{r['key']}] {r['hypothesis']}"
                      + (f" — 기각 사유: {r['reason']}" if r["reason"] else "") for r in rejected)
    return ("\n\n## 이미 기각된 가설 (재안 금지 — 같은 결론으로 되돌아가지 말 것)\n" + lines + "\n")


def stats() -> dict:
    d = _load()
    return {"issues_with_negatives": len(d),
            "total_rejected_hypotheses": sum(len(v) for v in d.values()),
            "store_path": str(STORE_FILE.relative_to(ROOT))}
