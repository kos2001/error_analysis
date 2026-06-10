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
# 호스트명에 중첩 괄호가 올 수 있음 — 예: "(Vega / Android NFC stack (NCI 2.3))"
_PREFIX = re.compile(r"^\s*\[[^\]]*\]\s*")
_SUFFIX = re.compile(r"\s*\((?:[^()]|\([^()]*\))*\s/\s(?:[^()]|\([^()]*\))*\)\s*$")


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


def _doc_text(rec: dict, analysis: bool = True) -> str:
    """KB 문서 표현 (검색 대상).

    이슈는 제기 → 분석 → 해결 단계로 진행된다. 유사 이슈 매칭은
    "이슈 제기(요약/증상)"와 "문제 분석(디버깅 접근/근본 원인)" 내용으로 하고,
    해결 단계(resolution/workaround)는 질의(미해결 이슈)에 존재할 수 없는
    정보이므로 문서 표현에서 제외한다.
    """
    parts = [
        rec.get("summary", ""), rec.get("symptom", ""),
        " ".join(rec.get("labels", []) or []),
        rec.get("chip", ""), rec.get("category", ""),
    ]
    if analysis:
        parts += [rec.get("debug_approach", ""), rec.get("root_cause", "")]
    return " ".join(p for p in parts if p)


def _query_text(rec: dict) -> str:
    """질의 표현 — 이슈 제기 + (진행 중 이슈에 있다면) 분석 단계 내용.

    해결 필드(resolution/workaround)는 미해결 질의에 존재할 수 없으므로 사용 안 함.
    """
    return " ".join(p for p in [
        rec.get("summary", ""), rec.get("symptom", ""),
        rec.get("chip", ""), rec.get("category", ""),
        rec.get("debug_approach", ""), rec.get("root_cause", ""),
    ] if p)


@dataclass
class Recommender:
    kb: list[dict]                       # 해결(Resolved) 이슈 레코드
    method: str = "hybrid"
    rrf_k: int = 60
    boost: float = 0.15                  # 동일 칩/분류 가산
    signals: bool = True                 # 강도 신호(embed_cos 등) 산출 — 게이트/표시용
    gate_cos: float = 0.48               # coverage 게이트: 임베딩 코사인 임계
    # 0.48 근거: paraphrase 정답 중 0.485/0.496이 0.50 직하에서 차단(FN)되는 반면,
    # 무관 질의 분포는 0.474 이하(예외 n06 0.503은 어느 임계든 통과). 측정 2026-06-11.
    doc_analysis: bool = True            # KB 문서에 분석 단계(디버깅 접근/근본 원인) 포함
    _embedder: object = field(default=None, repr=False)
    _kb_emb: object = field(default=None, repr=False)

    def __post_init__(self):
        self._docs = [_doc_text(r, analysis=self.doc_analysis) for r in self.kb]
        self._bm25 = BM25Okapi([tokenize(d) for d in self._docs])
        self._kb_ents = [query_entities(r) for r in self.kb]
        self._keys = [r["key"] for r in self.kb]
        if self.method in ("embed", "hybrid_embed"):
            self._init_embed()
        elif self.signals:
            # 게이트/신뢰도 표시에 코사인이 필요. fastembed 미설치 시 신호 없이 동작.
            try:
                self._init_embed()
            except ImportError:
                self.signals = False

    # ---------- 임베딩(선택) ----------
    _EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

    def _init_embed(self):
        from fastembed import TextEmbedding
        import hashlib
        import numpy as np
        from pathlib import Path
        # 다국어 소형 모델 (한국어 포함)
        self._embedder = TextEmbedding(model_name=self._EMBED_MODEL)
        self._np = np
        # KB 임베딩 디스크 캐시 — 문서 내용+모델이 같으면 서버 재기동 시 재계산 생략
        digest = hashlib.md5(("\n".join(self._docs) + self._EMBED_MODEL).encode()).hexdigest()[:12]
        cache = Path(__file__).resolve().parent.parent / "tmp_db" / f"kb_emb_{digest}.npz"
        if cache.exists():
            self._kb_emb = np.load(cache)["emb"]
            return
        self._kb_emb = np.array(list(self._embedder.embed(self._docs)))
        try:
            cache.parent.mkdir(exist_ok=True)
            np.savez_compressed(cache, emb=self._kb_emb)
        except OSError:
            pass  # 캐시 실패는 치명적이지 않음

    def _cos_all(self, q: str):
        """질의 vs KB 전체 코사인 유사도 배열 (강도 신호)."""
        qv = next(iter(self._embedder.embed([q])))
        qv = self._np.array(qv)
        return self._kb_emb @ qv / (
            self._np.linalg.norm(self._kb_emb, axis=1) * self._np.linalg.norm(qv) + 1e-9)

    def _embed_rank(self, q: str) -> list[tuple[int, float]]:
        sims = self._cos_all(q)
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
        q_ents = query_entities(query_rec)
        # 강도 신호 — RRF(순위 기반) 점수와 별개. 게이트/신뢰도 표시는 이것만 사용한다.
        cos = bm25_raw = None
        if self.signals and self._embedder is not None:
            qtext = _query_text(query_rec)
            cos = self._cos_all(qtext)
            bm25_raw = self._bm25.get_scores(tokenize(qtext))
        matches = []
        for i, score in ranked:
            r = self.kb[i]
            m = {
                "key": r["key"], "score": round(score, 4),
                "summary": r["summary"], "chip": r.get("chip", ""),
                "category": r.get("category", ""),
                "root_cause": r.get("root_cause", ""),
                "resolution": r.get("resolution", ""),
                "workaround": r.get("workaround", ""),
                "debug_approach": r.get("debug_approach", ""),
                "entity_overlap": len(q_ents & self._kb_ents[i]),
            }
            if cos is not None:
                m["embed_cos"] = round(float(cos[i]), 3)
                m["bm25_raw"] = round(float(bm25_raw[i]), 2)
            matches.append(m)
        # coverage 게이트: 무관 질의 차단.
        # 측정(2026-06: claudedocs/similarity_search_plan.md): 무관 질의 max_cos<=0.474 &
        # 엔티티 겹침 0, 정답 paraphrase는 cos>=0.529 — 코사인 0.50 또는 엔티티 1개로 통과.
        coverage = bool(matches)
        gate = None
        if cos is not None and len(cos):
            masked = cos.copy()
            if exclude_key is not None and exclude_key in self._keys:
                masked[self._keys.index(exclude_key)] = -1.0
            max_cos = float(masked.max())
            top_overlap = max((m["entity_overlap"] for m in matches), default=0)
            coverage = bool(matches) and (max_cos >= self.gate_cos or top_overlap >= 1)
            gate = {"max_cos": round(max_cos, 3), "top_entity_overlap": top_overlap,
                    "cos_threshold": self.gate_cos, "passed": coverage}
        # 제안: 상위 매치들의 다수결 클래스 → 그 클래스 대표의 해결책 (게이트 통과 시에만)
        proposal = None
        if matches and coverage:
            from collections import Counter
            classes = Counter(template_key(m["summary"]) for m in matches)
            top_class, cnt = classes.most_common(1)[0]
            rep = next(m for m in matches if template_key(m["summary"]) == top_class)
            proposal = {
                "root_cause": rep["root_cause"], "resolution": rep["resolution"],
                "workaround": rep["workaround"], "based_on": rep["key"],
                "confidence": round(cnt / len(matches), 2),
            }
        return {"matches": matches, "proposal": proposal, "coverage": coverage, "gate": gate}


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
