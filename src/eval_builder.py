"""평가셋 빌더 — (1) 실신호 누적 평가셋, (2) 변별력 있는 hard 평가셋.

배경(고려사항 #0): 기존 평가셋은 합성·포화(P@1=1.0)라 개선 신호가 안 나온다.
두 갈래로 '진짜 평가 기질'을 확보한다.

(1) real_pairs() — 사용·현장에서 나온 검증된 정답쌍(시간이 갈수록 성장):
    · outcome_tracker: 게시 RCA가 달린 이슈가 '완료'된 건(현장 검증) → query=그 이슈,
      gold=같은 템플릿. 사람이 RCA를 승인했고 현장에서 해결된, 가장 신뢰도 높은 신호.
    · reco_feedback: 사용자가 '실제 근본원인'으로 라벨한 (질의→정답 사례) 쌍.

(2) hard_set() — 신호를 줄인(요약 제거, 증상만) 질의로 포화를 깬다:
    요약에는 고장모드 표현이 그대로 있어 BM25/임베딩이 쉽게 맞춘다. 증상만 남기면
    더 oblique해져 변별력이 생긴다(정답=템플릿 불변이라 ground truth 유효).

둘 다 evaluate_paraphrase 포맷({positives, negatives})으로 저장 → 기존 하네스 재사용.
"""
from __future__ import annotations

from pathlib import Path

from json_store import read_json, write_json_atomic

ROOT = Path(__file__).resolve().parent.parent
REAL_FILE = ROOT / "data" / "eval_real.json"
HARD_FILE = ROOT / "data" / "eval_hard.json"
RESOLVED_STATUS = "완료"


def _template(summary: str) -> str:
    from recommender import template_key
    return template_key(summary)


def real_pairs(by_key: dict) -> dict:
    """현장 검증 정답쌍을 evaluate_paraphrase positives 포맷으로 누적.

    by_key: {issue_key: record}. negatives는 비움(실신호는 positives 위주).
    """
    import outcome_tracker
    import reco_feedback

    seen, positives = set(), []

    # (a) 게시 RCA가 달리고 '완료'된 이슈 — 현장 검증된 질의
    for si in sorted(outcome_tracker.resolved_source_issues()):
        rec = by_key.get(si)
        if not rec or si in seen:
            continue
        seen.add(si)
        positives.append({"id": f"real-{si}", "template": _template(rec.get("summary", "")),
                          "summary": rec.get("summary", ""), "symptom": rec.get("symptom", ""),
                          "source": "outcome_resolved"})

    # (b) 사용자 '실제 근본원인' 라벨 — query→gold 쌍
    for p in reco_feedback.eval_pairs():
        qk = p.get("query_key")
        rec = by_key.get(qk) or {"summary": p.get("query_summary", "")}
        sig = f"fb-{qk}-{p.get('gold_match_key')}"
        if sig in seen:
            continue
        seen.add(sig)
        gold = by_key.get(p.get("gold_match_key"), {})
        positives.append({"id": sig, "template": _template(gold.get("summary", "") or rec.get("summary", "")),
                          "summary": rec.get("summary", ""), "symptom": rec.get("symptom", ""),
                          "source": "feedback_label"})

    ds = {"_doc": "실신호 누적 평가셋(outcome+feedback). 시간이 갈수록 성장.",
          "positives": positives, "negatives": []}
    write_json_atomic(REAL_FILE, ds)
    return {"positives": len(positives), "store_path": str(REAL_FILE.relative_to(ROOT)),
            "by_source": {s: sum(1 for p in positives if p["source"] == s)
                          for s in {p["source"] for p in positives}}}


def hard_set(records: list[dict], *, per_template: int = 1) -> dict:
    """증상만 남긴(요약 제거) 변별력 평가셋. 정답=템플릿(불변) → ground truth 유효.

    같은 템플릿이 KB에 2건 이상(coverage 보장)인 것만, 증상이 있는 해결 이슈에서 표집.
    negatives는 기존 eval_paraphrase에서 재사용(있으면).
    """
    from collections import defaultdict
    resolved = [r for r in records if r.get("status") == RESOLVED_STATUS]
    by_tmpl = defaultdict(list)
    for r in resolved:
        if (r.get("symptom") or "").strip():
            by_tmpl[_template(r.get("summary", ""))].append(r)

    positives = []
    for tmpl, recs in by_tmpl.items():
        if len(recs) < 2:        # 정답 사례가 1건뿐이면 LOO coverage 불가
            continue
        for r in recs[:per_template]:
            positives.append({"id": f"hard-{r['key']}", "template": tmpl,
                              "summary": "",                      # 요약 제거 = 신호 축소
                              "symptom": r.get("symptom", ""), "source": "hard_symptom_only"})

    base = read_json(ROOT / "data" / "eval_paraphrase.json", {})
    negatives = base.get("negatives", []) if isinstance(base, dict) else []
    ds = {"_doc": "변별력 hard 평가셋(증상만, 요약 제거). 정답=템플릿.",
          "positives": positives, "negatives": negatives}
    write_json_atomic(HARD_FILE, ds)
    return {"positives": len(positives), "negatives": len(negatives),
            "store_path": str(HARD_FILE.relative_to(ROOT))}


GEN_FILE = ROOT / "data" / "eval_generated.json"


def generate_paraphrases(records: list[dict], *, per_template: int = 1,
                         max_templates: int = 0) -> dict:
    """LLM로 각 템플릿의 증상/요약을 '다른 엔지니어가 다르게 표현한' 질의로 재서술.

    합성 데이터의 필드 축소는 변별이 안 되지만(측정), 의미보존·표현상이 재서술은
    사람 paraphrase처럼 변별력을 만든다. 정답=원 템플릿(불변). OPENROUTER_* 필요.
    누적 저장: data/eval_generated.json. 토큰 비용 있으니 max_templates로 제한 가능.
    """
    import json as _json
    import os
    import urllib.request
    from collections import defaultdict

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return {"error": "OPENROUTER_API_KEY 없음"}
    model = os.getenv("RVP_MODEL") or os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    base = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")

    resolved = [r for r in records if r.get("status") == RESOLVED_STATUS and (r.get("symptom") or "").strip()]
    by_tmpl = defaultdict(list)
    for r in resolved:
        by_tmpl[_template(r.get("summary", ""))].append(r)
    templates = [t for t, rs in by_tmpl.items() if len(rs) >= 2]  # coverage 보장
    if max_templates:
        templates = templates[:max_templates]

    def _reword(rep: dict) -> dict | None:
        prompt = (
            "아래 LSI 칩/펌웨어 고장 이슈를, 같은 문제를 겪는 **다른 엔지니어가 다르게 표현**한 "
            "것처럼 한국어로 재서술하라. 핵심 의미는 보존하되 어휘·문장을 바꾸고, 칩 코드·고객사명은 "
            "넣지 마라. JSON만 출력: {\"summary\": \"...\", \"symptom\": \"...\"}\n\n"
            f"요약: {rep.get('summary','')}\n증상: {rep.get('symptom','')}")
        body = _json.dumps({"model": model, "max_tokens": 500, "response_format": {"type": "json_object"},
                            "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(f"{base}/chat/completions", data=body,
                                     headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        try:
            d = _json.load(urllib.request.urlopen(req, timeout=60))
            txt = d["choices"][0]["message"]["content"]
            obj = _json.loads(txt)
            if obj.get("symptom") or obj.get("summary"):
                return {"summary": obj.get("summary", ""), "symptom": obj.get("symptom", "")}
        except Exception:
            return None
        return None

    existing = read_json(GEN_FILE, {})
    positives = existing.get("positives", []) if isinstance(existing, dict) else []
    seen = {p["id"] for p in positives}
    added = 0
    for tmpl in templates:
        for rep in by_tmpl[tmpl][:per_template]:
            pid = f"gen-{rep['key']}"
            if pid in seen:
                continue
            rw = _reword(rep)
            if not rw:
                continue
            positives.append({"id": pid, "template": tmpl, "summary": rw["summary"],
                              "symptom": rw["symptom"], "source": "llm_paraphrase",
                              "origin_key": rep["key"]})
            seen.add(pid)
            added += 1

    base_ds = read_json(ROOT / "data" / "eval_paraphrase.json", {})
    negatives = base_ds.get("negatives", []) if isinstance(base_ds, dict) else []
    ds = {"_doc": "LLM 재서술 평가셋(의미보존·표현상이). 정답=원 템플릿. 누적 성장.",
          "positives": positives, "negatives": negatives}
    write_json_atomic(GEN_FILE, ds)
    return {"added": added, "total_positives": len(positives), "templates_seen": len(templates),
            "store_path": str(GEN_FILE.relative_to(ROOT))}
