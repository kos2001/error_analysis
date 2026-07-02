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
    investigation: 진행 중 이슈의 조사·트리아지 코멘트에서 추출한 관찰/분석 단계
    신호(관찰 가능 — 확정 근본원인 아님). 이슈가 진행될수록 질의 표현이 풍부해진다.
    """
    return " ".join(p for p in [
        rec.get("summary", ""), rec.get("symptom", ""),
        rec.get("chip", ""), rec.get("category", ""),
        rec.get("debug_approach", ""), rec.get("root_cause", ""),
        rec.get("investigation", ""),
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
    verified_tiebreak: float = 1e-4      # 검증 완료(✅+🙌) 사례 동점 시 우선 노출(M2).
    # 1e-4 근거: 텍스트/메타 신호(RRF≈0.03, boost 0.15)를 절대 덮지 않는 크기 —
    # 점수가 사실상 같을 때만 검증 사례를 위로 올리는 순수 tie-breaker.
    embed_model: str = ""                 # "" → 기본 MiniLM. 교체 가능(e5-large, bge-m3 등).
    embed_backend: str = "fastembed"      # fastembed(로컬) | openrouter(API)
    # ---- 2차 재순위(reranker) — cross-encoder로 1차 top-N 재채점 ----
    rerank: bool = False                  # True → recommend()에서 top-N 재순위 + 강도 게이트
    rerank_model: str = "cohere/rerank-v3.5"
    rerank_top_n: int = 20                # 1차에서 재순위에 넘길 후보 수
    rerank_gate: float = 0.20             # coverage 게이트 임계(rerank relevance_score).
    # 0.20 근거: 측정(scripts/ab_reranker.py, 2026-06-13) — 정답 최상위 점수 최소 0.384,
    # 무관 질의 최상위 최대 0.042. 둘 사이 양쪽 마진(≈0.16/0.18) 확보하는 값.
    rerank_timeout: int = 10              # /rerank 호출 타임아웃(초). 실측 호출당 ≈0.6s —
    # 게이트웨이가 /rerank 미지원일 때 질의가 기본 60s에 묶이지 않게 짧게 제한.
    rerank_fail_limit: int = 3            # 연속 실패 시 rerank 자동 비활성(circuit breaker) —
    # 미지원 게이트웨이에서 질의마다 실패 요청을 반복 지불하지 않기 위함.
    _rerank_fails: int = field(default=0, repr=False)
    _embedder: object = field(default=None, repr=False)
    _kb_emb: object = field(default=None, repr=False)

    def __post_init__(self):
        self._docs = [_doc_text(r, analysis=self.doc_analysis) for r in self.kb]
        self._bm25 = BM25Okapi([tokenize(d) for d in self._docs])
        self._kb_ents = [query_entities(r) for r in self.kb]
        self._keys = [r["key"] for r in self.kb]
        self._kb_verified = [bool(r.get("verified")) for r in self.kb]
        if self.method in ("embed", "hybrid_embed"):
            # 임베딩 백엔드(fastembed 미설치 / openrouter 게이트웨이 /embeddings 미지원·오류)
            # 실패 시 BM25 경로로 우아하게 폴백한다. embed→bm25, hybrid_embed→hybrid.
            try:
                self._init_embed()
            except Exception as e:
                fallback = "bm25" if self.method == "embed" else "hybrid"
                print(f"[recommender] 임베딩({self.embed_backend}/{self._model_name()}) "
                      f"초기화 실패 → '{self.method}'→'{fallback}' 폴백: {str(e)[:120]}")
                self.method = fallback
                self.signals = False
                self._embedder = None
                self._kb_emb = None
        elif self.signals:
            # 게이트/신뢰도 표시에 코사인이 필요. 실패 시 신호 없이 동작.
            try:
                self._init_embed()
            except Exception:
                self.signals = False

    # ---------- 임베딩(선택, 교체 가능) ----------
    _EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

    def _model_name(self) -> str:
        return self.embed_model or self._EMBED_MODEL

    def _prefixed(self, texts: list[str], is_query: bool) -> list[str]:
        """e5 계열은 query:/passage: 프리픽스를 요구한다(다른 모델은 원문 그대로)."""
        if "e5" in self._model_name().lower():
            tag = "query: " if is_query else "passage: "
            return [tag + t for t in texts]
        return texts

    def _embed_texts(self, texts: list[str], is_query: bool):
        import numpy as np
        texts = self._prefixed(texts, is_query)
        if self.embed_backend == "openrouter":
            return np.array(self._openrouter_embed(texts))
        return np.array(list(self._embedder.embed(texts)))

    def _openrouter_embed(self, texts: list[str]) -> list[list[float]]:
        """OpenRouter /embeddings 호출(배치). OPENROUTER_API_KEY 필요."""
        import os
        import requests
        from llm_headers import custom_headers
        key = os.environ["OPENROUTER_API_KEY"]
        base = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        hdrs = {"Authorization": f"Bearer {key}", **custom_headers()}
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):  # rate/payload 여유 배치
            chunk = texts[i:i + 64]
            r = requests.post(f"{base}/embeddings", headers=hdrs,
                              json={"model": self._model_name(), "input": chunk}, timeout=120)
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda x: x["index"])
            out.extend(d["embedding"] for d in data)
        return out

    def _init_embed(self):
        import hashlib
        import numpy as np
        from pathlib import Path
        self._np = np
        if self.embed_backend == "fastembed":
            from fastembed import TextEmbedding
            self._embedder = TextEmbedding(model_name=self._model_name())
        else:
            self._embedder = None  # API 백엔드는 객체 불필요(_openrouter_embed 사용)
        # KB 임베딩 디스크 캐시 — 문서+모델(+비기본 백엔드)이 같으면 재계산 생략
        sig = self._model_name() + ("" if self.embed_backend == "fastembed" else f"@{self.embed_backend}")
        digest = hashlib.md5(("\n".join(self._docs) + sig).encode()).hexdigest()[:12]
        cache = Path(__file__).resolve().parent.parent / "tmp_db" / f"kb_emb_{digest}.npz"
        if cache.exists():
            self._kb_emb = np.load(cache)["emb"]
            return
        self._kb_emb = self._embed_texts(self._docs, is_query=False)
        try:
            cache.parent.mkdir(exist_ok=True)
            np.savez_compressed(cache, emb=self._kb_emb)
        except OSError:
            pass  # 캐시 실패는 치명적이지 않음

    def _cos_all(self, q: str):
        """질의 vs KB 전체 코사인 유사도 배열 (강도 신호)."""
        qv = self._embed_texts([q], is_query=True)[0]
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

        # 검증 완료 사례 동점 tie-break(M2) — 실신호를 못 덮는 크기로만 가산.
        if self.verified_tiebreak:
            for i in list(fused):
                if self._kb_verified[i]:
                    fused[i] += self.verified_tiebreak
        ranked = sorted(fused.items(), key=lambda x: -x[1])
        if exclude_key is not None:
            ranked = [(i, s) for i, s in ranked if self._keys[i] != exclude_key]
        return ranked

    def recommend(self, query_rec: dict, k: int = 3, exclude_key: str | None = None) -> dict:
        ranked_all = self.rank(query_rec, exclude_key)
        qtext = _query_text(query_rec)
        # ---- 2차 재순위(reranker): 1차 top-N을 cross-encoder로 재채점 ----
        rr_scores: dict[int, float] = {}
        if self.rerank and ranked_all:
            from reranker import rerank as _rerank  # 지연 임포트(선택 의존)
            cand = [i for i, _ in ranked_all[:self.rerank_top_n]]
            docs = [_doc_text(self.kb[i], analysis=self.doc_analysis) for i in cand]
            try:
                order = _rerank(qtext, docs, model=self.rerank_model,
                                timeout=self.rerank_timeout)
                rr_scores = {cand[idx]: sc for idx, sc in order}
                reranked = [(cand[idx], sc) for idx, sc in order]
                tail = [(i, s) for i, s in ranked_all if i not in rr_scores]
                ranked_all = reranked + tail
                self._rerank_fails = 0
            except Exception as e:
                # rerank 실패 시 1차 순위로 폴백(파이프라인 무중단).
                self._rerank_fails += 1
                if self._rerank_fails >= self.rerank_fail_limit:
                    self.rerank = False
                    print(f"[recommender] rerank {self._rerank_fails}회 연속 실패 → "
                          f"비활성(1차 순위+embed_cos 게이트로 폴백): {str(e)[:120]}")
        ranked = ranked_all[:k]
        q_ents = query_entities(query_rec)
        # 강도 신호 — RRF(순위 기반) 점수와 별개. 게이트/신뢰도 표시는 이것만 사용한다.
        cos = bm25_raw = None
        if self.signals and self._embedder is not None:
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
                "verified": self._kb_verified[i],
            }
            if cos is not None:
                m["embed_cos"] = round(float(cos[i]), 3)
                m["bm25_raw"] = round(float(bm25_raw[i]), 2)
            if i in rr_scores:
                m["rerank_score"] = round(rr_scores[i], 4)
            matches.append(m)
        # coverage 게이트: 무관 질의 차단.
        coverage = bool(matches)
        gate = None
        if self.rerank and rr_scores:
            # rerank 게이트는 rerank가 실제 점수를 냈을 때만. /rerank 실패(rr_scores 빈
            # dict)면 아래 embed_cos 게이트, 그것도 없으면 기본(매치 있으면 통과)로 폴백.
            # 강도 기반 게이트 — rerank relevance_score(재보정 임계 rerank_gate).
            top_rr = max(rr_scores.values(), default=0.0)
            coverage = bool(matches) and top_rr >= self.rerank_gate
            gate = {"signal": "rerank", "rerank_top": round(top_rr, 3),
                    "threshold": self.rerank_gate, "passed": coverage}
        elif cos is not None and len(cos):
            # 측정(2026-06): 무관 질의 max_cos<=0.474 & 엔티티 겹침 0, 정답 cos>=0.529.
            masked = cos.copy()
            if exclude_key is not None and exclude_key in self._keys:
                masked[self._keys.index(exclude_key)] = -1.0
            max_cos = float(masked.max())
            top_overlap = max((m["entity_overlap"] for m in matches), default=0)
            coverage = bool(matches) and (max_cos >= self.gate_cos or top_overlap >= 1)
            gate = {"signal": "embed_cos", "max_cos": round(max_cos, 3),
                    "top_entity_overlap": top_overlap,
                    "cos_threshold": self.gate_cos, "passed": coverage}
        # 제안: 상위 매치들의 다수결 클래스 → 그 클래스 대표의 해결책 (게이트 통과 시에만)
        proposal = None
        if matches and coverage:
            from collections import Counter
            classes = Counter(template_key(m["summary"]) for m in matches)
            top_class, cnt = classes.most_common(1)[0]
            in_class = [m for m in matches if template_key(m["summary"]) == top_class]
            # 대표 사례: 동일 클래스 내 '검증 완료' 사례를 우선(없으면 최상위)(M2).
            rep = next((m for m in in_class if m["verified"]), in_class[0])
            # 신뢰도 = 합의(agreement) × 관련도(rerank) + 검증(verified) 소폭 상향.
            #  - agreement: top-k 중 다수결 클래스 비율(사례들이 한 원인으로 수렴하는가)
            #  - relevance: 대표 사례 rerank relevance(0~1, 보정 점수) — 합의는 높아도
            #    실제 관련도가 낮으면(약한 매칭) 신뢰도를 끌어내려 과신을 방지.
            #    rerank 미사용 시 None → 기존(합의만)으로 폴백(회귀 없음).
            #  - verified: 고객 검증까지 끝난 근거면 (1-conf)*0.1 만큼 상향(상한 1.0).
            agreement = cnt / len(matches)
            relevance = rep.get("rerank_score")
            conf = agreement * relevance if relevance is not None else agreement
            if rep["verified"]:
                conf = conf + (1.0 - conf) * 0.10
            proposal = {
                "root_cause": rep["root_cause"], "resolution": rep["resolution"],
                "workaround": rep["workaround"], "based_on": rep["key"],
                "confidence": round(conf, 2),
                "based_on_verified": rep["verified"],
                # 신뢰도 근거 분해(UI/댓글 톤·디버깅용)
                "confidence_basis": {
                    "agreement": round(agreement, 2),
                    "rerank_relevance": round(relevance, 3) if relevance is not None else None,
                    "verified_bonus": rep["verified"],
                },
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
