"""Reranker I/O — OpenRouter /rerank 호출(단일 책임).

cross-encoder 재순위: (query, documents) → 관련도 점수로 재정렬. bi-encoder
(임베딩) top-k 후보를 다시 채점해 1순위 정밀도와 게이트 신뢰도를 올린다.

relevance_score 는 RRF(순위 기반)와 달리 강도가 있는 0~1 점수라, 표시/게이트
신호로도 쓸 수 있다(claudedocs/similarity_search_plan.md의 'RRF는 유사도가 아니다' 해소).

인증(.env): OPENROUTER_API_KEY (+ OPENROUTER_BASE_URL). jira_commenter 와 동일 컨벤션.
"""
from __future__ import annotations

import os

import requests

DEFAULT_MODEL = "cohere/rerank-v3.5"


def rerank(query: str, documents: list[str], model: str = DEFAULT_MODEL,
           top_n: int | None = None, timeout: int = 60) -> list[tuple[int, float]]:
    """documents 를 query 관련도로 재정렬 → [(원본 index, relevance_score)] 내림차순.

    빈 documents 는 빈 리스트 반환. OPENROUTER_API_KEY 필요.
    """
    if not documents:
        return []
    key = os.environ["OPENROUTER_API_KEY"]
    base = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    payload: dict = {"model": model, "query": query, "documents": documents}
    if top_n is not None:
        payload["top_n"] = top_n
    r = requests.post(f"{base}/rerank",
                      headers={"Authorization": f"Bearer {key}"},
                      json=payload, timeout=timeout)
    r.raise_for_status()
    results = r.json().get("results", [])
    return [(int(x["index"]), float(x["relevance_score"])) for x in results]
