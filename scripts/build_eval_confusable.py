"""변별 평가셋 생성 — 혼동 후보를 곁에 둔 질의.

**왜 필요한가.** 기존 평가셋 4종이 전부 P@1 1.0 으로 포화됐다. 좋아져도 나빠져도
1.0 이라 지금부터의 변경을 검증할 수 없다 — 실제로 이번(2026-08) 세션에서 "품질
동일" 을 근거로 한 제안이 두 번 뒤집혔다.

**어떻게 어렵게 만드는가.** 정답 하나만 덩그러니 있는 질의는 쉽다. 여기서는
정답과 **헷갈릴 만한 다른 고장모드**가 KB 안에 함께 있는 상황을 골라 낸다:

  · 같은 칩 또는 같은 분류에 속하면서
  · 템플릿(근본원인 클래스)은 다른 사례를

정답의 최근접 이웃으로 붙여 두고, 질의는 **증상만**으로 준다(요약을 빼면 템플릿
문자열이 그대로 노출되는 지름길이 사라진다). 정답 템플릿이 혼동 후보보다 위로
와야 통과다.

생성은 **결정적**이다 — LLM 을 쓰지 않고 KB 임베딩으로만 고르므로 같은 KB 면
같은 셋이 나온다. 재현되지 않는 평가셋은 회귀 판단에 쓸 수 없다.

사용:
    set -a && source .env && set +a
    .venv/bin/python scripts/build_eval_confusable.py
    .venv/bin/python scripts/build_eval_confusable.py --min-sim 0.5 --out data/eval_confusable.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from preprocess import parse_issue          # noqa: E402
from recommender import Recommender, template_key, _doc_text  # noqa: E402

ALL_RAW = ROOT / "data" / "all_raw_issues.json"


def _reword(rep: dict, timeout: int = 60) -> dict | None:
    """증상을 **현장 사용자 말투**로 재서술 — 어휘 지름길을 없앤다.

    원문 증상을 그대로 질의로 쓰면 BM25 가 문서와 문자열이 겹쳐 바로 맞힌다
    (실측: 원문 그대로면 P@1 1.0 으로 포화). 기술 용어·에러 코드·칩 코드를 빼고
    관찰된 현상만 남기면 어휘가 아니라 의미로 찾아야 한다.
    """
    import json as _json
    import os
    import urllib.request
    from llm_headers import custom_headers

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None
    model = os.getenv("RVP_MODEL") or os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    base = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    prompt = (
        "아래 칩/펌웨어 고장 증상을, **기술 용어를 모르는 현장 담당자가 신고하듯** "
        "한국어로 다시 쓰라. 규칙:\n"
        "- 칩 코드·에러 코드·레지스터/프로토콜 약어(UFS, LTSSM, AER, PLL 등)를 쓰지 마라\n"
        "- 관찰된 현상(언제·무엇이·어떻게 보이는지)만 남겨라\n"
        "- 2~3문장. JSON만 출력: {\"symptom\": \"...\"}\n\n"
        f"증상: {rep.get('symptom','')}")
    body = _json.dumps({"model": model, "max_tokens": 400,
                        "response_format": {"type": "json_object"},
                        "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(f"{base}/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {api_key}",
                                          "Content-Type": "application/json",
                                          **custom_headers()})
    try:
        d = _json.load(urllib.request.urlopen(req, timeout=timeout))
        out = _json.loads(d["choices"][0]["message"]["content"])
        t = (out.get("symptom") or "").strip()
        return {"symptom": t} if len(t) >= 20 else None
    except Exception:
        return None


def build(min_sim: float, max_items: int, paraphrase: bool = False) -> dict:
    raw = json.loads(ALL_RAW.read_text(encoding="utf-8"))
    resolved = [r for r in (parse_issue(x) for x in raw) if r["status"] == "완료"]
    rec = Recommender(resolved, method="hybrid_embed", rerank=False, signals=True)
    if rec._kb_emb is None:
        raise SystemExit("임베딩을 만들 수 없어 중단 — 혼동 후보를 고를 수 없습니다.")

    import numpy as np
    emb = rec._kb_emb
    norm = emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9)
    sim = norm @ norm.T
    np.fill_diagonal(sim, -1.0)

    tmpl = [template_key(r["summary"]) for r in resolved]
    positives, skipped = [], {"증상없음": 0, "혼동후보없음": 0, "중복템플릿": 0}
    seen_tmpl: set[str] = set()

    # 템플릿마다 한 건씩만 — 같은 클래스를 여러 번 물어봐야 변별력이 늘지 않는다.
    order = sorted(range(len(resolved)), key=lambda i: resolved[i]["key"])
    for i in order:
        r = resolved[i]
        if tmpl[i] in seen_tmpl:
            skipped["중복템플릿"] += 1
            continue
        symptom = (r.get("symptom") or "").strip()
        if len(symptom) < 25:
            skipped["증상없음"] += 1
            continue
        # 혼동 후보: 칩 또는 분류를 공유하면서 템플릿이 다른 최근접 이웃
        cands = [(float(sim[i][j]), j) for j in range(len(resolved))
                 if tmpl[j] != tmpl[i]
                 and (resolved[j].get("chip") == r.get("chip")
                      or resolved[j].get("category") == r.get("category"))]
        cands.sort(reverse=True)
        if not cands or cands[0][0] < min_sim:
            skipped["혼동후보없음"] += 1
            continue
        s, j = cands[0]
        seen_tmpl.add(tmpl[i])
        query = symptom
        reworded = False
        if paraphrase:
            rw = _reword(r)
            if rw:
                query, reworded = rw["symptom"], True
        seen_tmpl.add(tmpl[i])
        positives.append({
            "id": f"conf-{r['key']}",
            "template": tmpl[i],
            "summary": query[:200],        # 요약 대신 (재서술된) 증상
            "symptom": query,
            "reworded": reworded,
            "original_symptom": symptom[:300],
            "gold_key": r["key"],
            "chip": r.get("chip", ""),
            "category": r.get("category", ""),
            "distractor_key": resolved[j]["key"],
            "distractor_template": tmpl[j],
            "distractor_similarity": round(s, 3),
        })
        if len(positives) >= max_items:
            break

    # 부정(무관) 질의 — 기존 셋과 겹치지 않게 다른 도메인에서 뽑는다.
    negatives = [
        {"id": "n-conf-01", "summary": "사내 인트라넷 로그인 후 결재 목록이 비어 있음",
         "symptom": "결재 대기 문서가 하나도 보이지 않음"},
        {"id": "n-conf-02", "summary": "회의실 예약 시스템에서 중복 예약이 허용됨",
         "symptom": "같은 시간대에 두 건이 동시에 잡힘"},
        {"id": "n-conf-03", "summary": "구내식당 모바일 주문 결제가 취소로 처리됨",
         "symptom": "카드 승인은 났는데 주문이 사라짐"},
        {"id": "n-conf-04", "summary": "출입 게이트에서 사원증 인식이 간헐적으로 실패",
         "symptom": "태그해도 문이 열리지 않고 재시도하면 열림"},
        {"id": "n-conf-05", "summary": "사내 위키 검색이 최신 문서를 반환하지 않음",
         "symptom": "어제 올린 페이지가 검색 결과에 없음"},
        {"id": "n-conf-06", "summary": "법인차량 배차 앱에서 반납 처리가 안 됨",
         "symptom": "반납 버튼을 눌러도 사용 중 상태 유지"},
    ]
    return {
        "_note": ("혼동 후보(같은 칩·분류, 다른 템플릿)를 곁에 둔 변별 셋. "
                  "질의는 증상만 — 요약을 빼서 템플릿 문자열 노출을 막았다. "
                  "scripts/build_eval_confusable.py 로 KB 에서 결정적으로 생성."),
        "_params": {"min_sim": min_sim, "max_items": max_items},
        "_skipped": skipped,
        "positives": positives,
        "negatives": negatives,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-sim", type=float, default=0.45,
                    help="혼동 후보로 인정할 최소 유사도 (낮으면 쉬운 문제가 섞인다)")
    ap.add_argument("--max-items", type=int, default=60)
    ap.add_argument("--paraphrase", action="store_true",
                    help="증상을 현장 말투로 LLM 재서술 — 어휘 지름길 제거(항목당 호출 1회)")
    ap.add_argument("--out", default=str(ROOT / "data" / "eval_confusable.json"))
    a = ap.parse_args()

    ds = build(a.min_sim, a.max_items, paraphrase=a.paraphrase)
    Path(a.out).write_text(json.dumps(ds, ensure_ascii=False, indent=2), encoding="utf-8")
    p = ds["positives"]
    rw = sum(1 for x in p if x.get("reworded"))
    print(f"[build] positives {len(p)} (재서술 {rw}) · negatives {len(ds['negatives'])} → {a.out}")
    print(f"[build] 제외: {ds['_skipped']}")
    if p:
        sims = [x["distractor_similarity"] for x in p]
        print(f"[build] 혼동 후보 유사도 중앙 {sorted(sims)[len(sims)//2]:.3f} "
              f"(최소 {min(sims):.3f} / 최대 {max(sims):.3f})")
        print("[build] 예시:")
        for x in p[:3]:
            print(f"   {x['gold_key']} vs {x['distractor_key']}({x['distractor_similarity']}) "
                  f"— {x['summary'][:50]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
