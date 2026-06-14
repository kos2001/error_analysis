"""지식 모순 탐지 (자기개선 #2) — 같은 고장모드인데 근본원인이 엇갈리는 쌍을 찾는다.

배경(고려사항 #2):
  큐레이션 지식·사례가 쌓이면 서로 모순할 수 있다 — 같은 증상/고장모드에 다른
  근본원인을 주장하는 두 레코드. 방치하면 추천 신뢰도를 갉아먹는다.

방법(추가 모델 없이 recommender 임베딩 자산 재사용):
  - 문서 임베딩(증상/요약/분석) 코사인 ≥ sim_hi  → 같은 고장모드로 본다.
  - 그 쌍의 근본원인(root_cause) 임베딩 코사인 ≤ rc_lo → 결론이 엇갈림 = 모순 후보.
  사람이 검토해 'disputed' 판정·중재(최신·효능 기반)하도록 큐/제안에 올린다.

정직한 한계: 임계는 휴리스틱(실데이터 보정 필요). 합성 KB는 템플릿별 근본원인이
일관되게 생성돼 모순이 0에 가까울 수 있다(= 일관성 양호 신호).
"""
from __future__ import annotations


def detect(reco, *, sim_hi: float = 0.85, rc_lo: float = 0.60, max_pairs: int = 50) -> list[dict]:
    """문서 유사 ≥ sim_hi 이면서 근본원인 유사 ≤ rc_lo 인 쌍 = 모순 후보."""
    emb = getattr(reco, "_kb_emb", None)
    kb = getattr(reco, "kb", None)
    keys = getattr(reco, "_keys", None)
    if emb is None or not kb or not keys:
        return []
    np = reco._np
    X = np.asarray(emb, dtype=np.float32)
    Xn = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)
    doc_sim = Xn @ Xn.T

    # 근본원인 임베딩(있는 것만) — 같은 임베더 재사용
    rcs = [(r.get("root_cause") or "").strip() for r in kb]
    has_rc = [bool(t) for t in rcs]
    rc_emb = reco._embed_texts([t if t else " " for t in rcs], is_query=False)
    R = np.asarray(rc_emb, dtype=np.float32)
    Rn = R / (np.linalg.norm(R, axis=1, keepdims=True) + 1e-9)
    rc_sim = Rn @ Rn.T

    n = len(keys)
    out = []
    for i in range(n):
        for j in range(i + 1, n):
            if not (has_rc[i] and has_rc[j]):
                continue
            ds = float(doc_sim[i, j])
            if ds < sim_hi:
                continue
            rs = float(rc_sim[i, j])
            if rs <= rc_lo:
                out.append({
                    "a": keys[i], "b": keys[j],
                    "doc_similarity": round(ds, 3), "root_cause_similarity": round(rs, 3),
                    "summary_a": kb[i].get("summary", "")[:80],
                    "summary_b": kb[j].get("summary", "")[:80],
                    "root_cause_a": rcs[i][:160], "root_cause_b": rcs[j][:160],
                })
    out.sort(key=lambda p: (-p["doc_similarity"], p["root_cause_similarity"]))
    return out[:max_pairs]


def report(reco, **kw) -> dict:
    pairs = detect(reco, **kw)
    return {"count": len(pairs), "contradictions": pairs,
            "params": {"sim_hi": kw.get("sim_hi", 0.85), "rc_lo": kw.get("rc_lo", 0.60)}}
