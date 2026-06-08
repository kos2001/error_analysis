"""해결책 추천기 — 과거 *해결된* 이슈로 미해결 이슈의 root-cause/해결책을 제안.

입력 질의(미해결 이슈의 관찰 가능한 부분: 요약/증상/칩/분류/라벨)에 대해
지식베이스(해결 이슈)에서 가장 유사한 사례를 검색하고, 상위 사례의
근본원인·해결책·우회책을 제안으로 반환한다.

검색 방법(method):
    graph  : 엔티티 중첩(레코드 entities) — repo GraphRetriever 방식 (baseline)
    bm25   : 이슈 텍스트 BM25 (rank_bm25)
    hybrid : bm25 + graph RRF 융합 + 동일 칩/분류 부스트  (기본)
    embed  : 다국어 임베딩 코사인 (fastembed, 선택)
    hybrid_embed : bm25 + graph + embed RRF + 부스트

설계상 단일 책임: 인덱싱 + 랭킹 + 제안. 데이터 적재/전처리는 ingest/preprocess 담당.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rank_bm25 import BM25Okapi

import sys as _sys
from pathlib import Path
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from preprocess import tokenize, extract_entities  # noqa: E402  (단일 소스)

# 같은 템플릿(=같은 근본원인 클래스) 식별: 요약에서 칩 prefix와 변형 suffix 제거.
# 변형 suffix는 항상 " (고객사 / 호스트)" 형태(공백+슬래시 포함)이므로,
# 본문에 포함된 "(ghosting)" 같은 괄호와 구분하여 그것만 제거한다.
_PREFIX = re.compile(r"^\s*\[[^\]]*\]\s*")
_SUFFIX = re.compile(r"\s*\([^()]*\s/\s[^()]*\)\s*$")


def template_key(summary: str) -> str:
    s = _PREFIX.sub("", summary or "")
    s = _SUFFIX.sub("", s)
    return s.strip()


def query_entities(rec: dict) -> set[str]:
    blob = " ".join([rec.get("summary", ""), rec.get("symptom", ""),
                     rec.get("chip", ""), rec.get("category", "")])
    ents = extract_entities(blob)
    ents.update(rec.get("labels", []) or [])
    ents.update(rec.get("components", []) or [])
    ents.discard("customer-report")
    return {e for e in ents if len(e) >= 2}


def _doc_text(rec: dict) -> str:
    """KB 문서 표현 (검색 대상)."""
    return " ".join([
        rec.get("summary", ""), rec.get("symptom", ""),
        " ".join(rec.get("labels", []) or []),
        rec.get("chip", ""), rec.get("category", ""),
    ])


def _query_text(rec: dict) -> str:
    """질의 표현 (관찰 가능한 부분만 — root_cause/resolution은 사용 안 함)."""
    return " ".join([rec.get("summary", ""), rec.get("symptom", ""),
                     rec.get("chip", ""), rec.get("category", "")])


@dataclass
class Recommender:
    kb: list[dict]                       # 해결(Resolved) 이슈 레코드
    method: str = "hybrid"
    rrf_k: int = 60
    boost: float = 0.15                  # 동일 칩/분류 가산
    _embedder: object = field(default=None, repr=False)
    _kb_emb: object = field(default=None, repr=False)

    def __post_init__(self):
        self._docs = [_doc_text(r) for r in self.kb]
        self._bm25 = BM25Okapi([tokenize(d) for d in self._docs])
        self._kb_ents = [query_entities(r) for r in self.kb]
        self._keys = [r["key"] for r in self.kb]
        if self.method in ("embed", "hybrid_embed"):
            self._init_embed()

    # ---------- 임베딩(선택) ----------
    def _init_embed(self):
        from fastembed import TextEmbedding
        import numpy as np
        # 다국어 소형 모델 (한국어 포함)
        self._embedder = TextEmbedding(
            model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
        embs = list(self._embedder.embed(self._docs))
        self._kb_emb = np.array(embs)
        self._np = np

    def _embed_rank(self, q: str) -> list[tuple[int, float]]:
        qv = next(iter(self._embedder.embed([q])))
        qv = self._np.array(qv)
        sims = self._kb_emb @ qv / (
            self._np.linalg.norm(self._kb_emb, axis=1) * self._np.linalg.norm(qv) + 1e-9)
        order = self._np.argsort(-sims)
        return [(int(i), float(sims[i])) for i in order]

    # ---------- 개별 랭커 ----------
    def _bm25_rank(self, q: str) -> list[tuple[int, float]]:
        scores = self._bm25.get_scores(tokenize(q))
        return sorted(((i, float(s)) for i, s in enumerate(scores)), key=lambda x: -x[1])

    def _graph_rank(self, q_ents: set[str]) -> list[tuple[int, float]]:
        scored = []
        for i, ents in enumerate(self._kb_ents):
            ov = len(q_ents & ents)
            if ov:
                scored.append((i, float(ov)))
        return sorted(scored, key=lambda x: -x[1])

    @staticmethod
    def _rrf(ranks: list[list[tuple[int, float]]], k: int,
             weights: list[float] | None = None) -> dict[int, float]:
        out: dict[int, float] = {}
        weights = weights or [1.0] * len(ranks)
        for w, rank_list in zip(weights, ranks):
            for pos, (idx, _) in enumerate(rank_list):
                out[idx] = out.get(idx, 0.0) + w / (k + pos)
        return out

    def _apply_boost(self, fused: dict[int, float], query_rec: dict) -> None:
        qchip, qcat = query_rec.get("chip", ""), query_rec.get("category", "")
        for i, r in enumerate(self.kb):
            if i in fused:
                if qchip and r.get("chip") == qchip:
                    fused[i] += self.boost
                if qcat and r.get("category") == qcat:
                    fused[i] += self.boost

    # ---------- 추천 ----------
    def rank(self, query_rec: dict, exclude_key: str | None = None) -> list[tuple[int, float]]:
        qtext = _query_text(query_rec)
        qents = query_entities(query_rec)

        if self.method == "graph":
            fused = dict(self._graph_rank(qents))
        elif self.method == "bm25":
            fused = dict(self._bm25_rank(qtext))
        elif self.method == "embed":
            fused = dict(self._embed_rank(qtext))
        elif self.method == "bm25_boost":
            fused = dict(self._bm25_rank(qtext))
            self._apply_boost(fused, query_rec)
        elif self.method == "hybrid_embed":
            # bm25 우세 + embed 보조 + graph 약하게, 동일 칩/분류 부스트
            lists = [self._bm25_rank(qtext), self._embed_rank(qtext), self._graph_rank(qents)]
            fused = self._rrf(lists, self.rrf_k, weights=[2.0, 1.5, 0.5])
            self._apply_boost(fused, query_rec)
        else:  # hybrid (bm25 우세 + graph 약하게 + 부스트)
            lists = [self._bm25_rank(qtext), self._graph_rank(qents)]
            fused = self._rrf(lists, self.rrf_k, weights=[2.0, 0.5])
            self._apply_boost(fused, query_rec)

        ranked = sorted(fused.items(), key=lambda x: -x[1])
        if exclude_key is not None:
            ranked = [(i, s) for i, s in ranked if self._keys[i] != exclude_key]
        return ranked

    def recommend(self, query_rec: dict, k: int = 3, exclude_key: str | None = None) -> dict:
        ranked = self.rank(query_rec, exclude_key)[:k]
        matches = []
        for i, score in ranked:
            r = self.kb[i]
            matches.append({
                "key": r["key"], "score": round(score, 4),
                "summary": r["summary"], "chip": r.get("chip", ""),
                "category": r.get("category", ""),
                "root_cause": r.get("root_cause", ""),
                "resolution": r.get("resolution", ""),
                "workaround": r.get("workaround", ""),
                "debug_approach": r.get("debug_approach", ""),
            })
        # 제안: 상위 매치들의 다수결 클래스 → 그 클래스 대표의 해결책
        proposal = None
        confidence = 0.0
        if matches:
            from collections import Counter
            classes = Counter(template_key(m["summary"]) for m in matches)
            top_class, cnt = classes.most_common(1)[0]
            rep = next(m for m in matches if template_key(m["summary"]) == top_class)
            confidence = cnt / len(matches)
            proposal = {
                "root_cause": rep["root_cause"], "resolution": rep["resolution"],
                "workaround": rep["workaround"], "based_on": rep["key"],
                "confidence": round(confidence, 2),
            }
        return {"matches": matches, "proposal": proposal}


if __name__ == "__main__":
    import json
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent
    recs = json.load((ROOT / "data" / "processed_issues.json").open())
    rec = Recommender(recs, method="hybrid")
    demo = {"summary": "[PM9C3-NVMe] 고온 지속 쓰기 중 timeout", "symptom": "장시간 쓰기 후 link down, AER 폭주",
            "chip": "PM9C3-NVMe", "category": "Thermal", "labels": ["Thermal", "SSD-Controller"]}
    out = rec.recommend(demo, k=3)
    for m in out["matches"]:
        print(f"{m['key']} ({m['score']}) {m['summary'][:40]}")
    print("제안 근본원인:", (out["proposal"] or {}).get("root_cause", "")[:120])
