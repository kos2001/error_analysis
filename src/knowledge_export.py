"""지식 export·상호운용 — 축적 지식을 도구 밖으로 내보낸다.

배경(지식자산화 갭 P3-10):
  지식이 본 도구에 갇혀 있었다(Confluence/위키/외부 API 없음). 자산 가치는
  공유될 때 배가된다.

구성:
  - bundle(): 큐레이션 지식 + 고장모드 기사를 구조화 JSON으로.
  - to_markdown(): 사람이 읽는 지식 기사 문서(위키 붙여넣기/리뷰용).
"""
from __future__ import annotations

import datetime as _dt


def bundle() -> dict:
    """구조화 export — 다른 도구가 소비할 수 있는 지식 묶음(JSON)."""
    import knowledge_store
    import failure_modes
    return {
        "exported_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "known_issues": failure_modes.articles(),
        "curated_knowledge": knowledge_store.records(),
        "counts": {"known_issues": len(failure_modes.articles()),
                   "curated_knowledge": len(knowledge_store.records())},
    }


def to_markdown() -> str:
    """사람이 읽는 지식 베이스 문서(Markdown) — 위키/Confluence 붙여넣기용."""
    import knowledge_store
    import failure_modes
    arts = failure_modes.articles()
    curated = knowledge_store.records()
    lines = [f"# LSI 지식 베이스 export ({_dt.datetime.now().isoformat(timespec='seconds')})", ""]

    lines.append(f"## 고장모드 (Known-Issue) {len(arts)}건\n")
    for a in arts:
        lines.append(f"### {a['id']} — {a.get('title','')}")
        if a.get("chips") or a.get("categories"):
            lines.append(f"- 칩: {', '.join(a.get('chips') or []) or '—'} · 분류: {', '.join(a.get('categories') or []) or '—'}")
        lines.append(f"- 연결 사례({len(a.get('members', []))}): {', '.join(a.get('members', []))}")
        if a.get("root_cause"):
            lines.append(f"\n**근본원인**: {a['root_cause']}")
        if a.get("resolution"):
            lines.append(f"\n**해결책**: {a['resolution']}")
        if a.get("workaround"):
            lines.append(f"\n**우회책**: {a['workaround']}")
        lines.append("")

    lines.append(f"## 큐레이션 RCA 지식 {len(curated)}건\n")
    for r in curated:
        lines.append(f"### {r['key']} — {r.get('summary','') or '(요약 없음)'}")
        prov = []
        if r.get("source_issue"):
            prov.append(f"출처 {r['source_issue']}")
        if r.get("comment_id"):
            prov.append(f"댓글 #{r['comment_id']}")
        if r.get("author"):
            prov.append(f"작성 {r['author']}")
        if prov:
            lines.append(f"- {' · '.join(prov)}")
        if r.get("root_cause"):
            lines.append(f"\n**근본원인**: {r['root_cause']}")
        if r.get("resolution"):
            lines.append(f"\n**해결책**: {r['resolution']}")
        lines.append("")
    return "\n".join(lines)
