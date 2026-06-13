"""인입 지식 품질 게이트 — 파싱된 KB 레코드의 필드 충족률을 검증·보고한다.

배경(지식자산화 갭 P1-2):
  parse_issue의 추출 마커("근본 원인 (Root Cause)" 등)가 본문 형식과 어긋나면
  **경보 없이 빈 레코드**가 KB에 적재된다(예: resolved 92건이 전부 root_cause=''
  로 무음 파싱). 자산 품질이 Jira 필드 위생에 무방비로 종속된다.

해결:
  적재 전(preprocess) / 서빙 시(server) 필드 충족률을 측정하고, 핵심 필드 결측이
  임계를 초과하면 차단(strict)하거나 두드러지게 경고한다. 해결 지식의 핵심 자산은
  root_cause/resolution 이고 미해결 이슈는 관찰 필드만 기대되므로 **상태별로 다른
  기준**을 적용한다.
"""
from __future__ import annotations

RESOLVED_STATUS_DEFAULT = "완료"
CATEGORY_DEFAULT = "기타"  # parse_issue의 폴백 — '분류 안 됨'으로 취급

# 상태별 측정 대상 필드
RESOLVED_CRITICAL = ("root_cause", "resolution")   # 해결 지식의 핵심 자산
RESOLVED_RECOMMENDED = ("symptom", "category", "chip")
ALL_REQUIRED = ("key", "summary")

# 충족률 임계 — 미달 시 위반(차단/경고 대상)
DEFAULT_THRESHOLDS = {
    "summary": 0.99,
    "resolved_root_cause": 0.90,
    "resolved_resolution": 0.90,
}


class QualityGateError(RuntimeError):
    """strict 모드에서 핵심 필드 충족률이 임계 미달일 때."""


def _filled(v, *, default=None) -> bool:
    if isinstance(v, str):
        s = v.strip()
        return bool(s) and (default is None or s != default)
    if isinstance(v, (list, tuple, set, dict)):
        return len(v) > 0
    return v is not None


def _rate(records: list[dict], field: str, *, default=None) -> float:
    if not records:
        return 1.0
    return sum(1 for r in records if _filled(r.get(field), default=default)) / len(records)


def report(records: list[dict], *, resolved_status: str = RESOLVED_STATUS_DEFAULT) -> dict:
    """상태별 필드 충족률 + 무음 추출 실패 의심 키 목록."""
    resolved = [r for r in records if r.get("status") == resolved_status]
    unresolved = [r for r in records if r.get("status") != resolved_status]

    def rates(recs, fields, **kw):
        return {f: round(_rate(recs, f, **kw), 3) for f in fields}

    # 무음 실패 의심: 해결 이슈인데 핵심 자산(root_cause/resolution)이 빈 레코드
    deficient = [r["key"] for r in resolved
                 if not _filled(r.get("root_cause")) or not _filled(r.get("resolution"))]
    return {
        "total": len(records),
        "resolved": len(resolved),
        "unresolved": len(unresolved),
        "fill": {
            "all_required": rates(records, ALL_REQUIRED),
            "resolved_critical": rates(resolved, RESOLVED_CRITICAL),
            "resolved_recommended": rates(resolved, RESOLVED_RECOMMENDED),
            "category_classified": round(_rate(records, "category", default=CATEGORY_DEFAULT), 3),
            "resolved_verified": round(_rate(resolved, "verified"), 3),
        },
        "deficient_resolved_keys": deficient,
    }


def validate(records: list[dict], *, resolved_status: str = RESOLVED_STATUS_DEFAULT,
             thresholds: dict | None = None, strict: bool = False) -> dict:
    """충족률을 임계와 비교해 위반 목록 산출. strict면 위반 시 QualityGateError."""
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    rep = report(records, resolved_status=resolved_status)
    fill = rep["fill"]
    violations = []
    if fill["all_required"].get("summary", 1.0) < th["summary"]:
        violations.append(f"summary 충족률 {fill['all_required']['summary']} < {th['summary']}")
    if rep["resolved"]:
        rc = fill["resolved_critical"].get("root_cause", 1.0)
        rs = fill["resolved_critical"].get("resolution", 1.0)
        if rc < th["resolved_root_cause"]:
            violations.append(f"해결 이슈 root_cause 충족률 {rc} < {th['resolved_root_cause']}")
        if rs < th["resolved_resolution"]:
            violations.append(f"해결 이슈 resolution 충족률 {rs} < {th['resolved_resolution']}")
    result = {"ok": not violations, "violations": violations, "report": rep}
    if strict and violations:
        raise QualityGateError("; ".join(violations))
    return result


def format_report(result: dict) -> str:
    """사람이 읽는 콘솔/로그 요약."""
    rep = result["report"]
    fill = rep["fill"]
    lines = [
        f"[품질 게이트] 총 {rep['total']} (해결 {rep['resolved']} · 미해결 {rep['unresolved']})",
        f"  필수      summary={fill['all_required'].get('summary')}  key={fill['all_required'].get('key')}",
        f"  해결-핵심 root_cause={fill['resolved_critical'].get('root_cause')}  resolution={fill['resolved_critical'].get('resolution')}",
        f"  해결-권장 symptom={fill['resolved_recommended'].get('symptom')}  category={fill['resolved_recommended'].get('category')}  chip={fill['resolved_recommended'].get('chip')}",
        f"  분류율(비-기타)={fill['category_classified']}  검증율={fill['resolved_verified']}",
    ]
    if rep["deficient_resolved_keys"]:
        d = rep["deficient_resolved_keys"]
        lines.append(f"  ⚠ 무음 실패 의심(해결인데 핵심 결측) {len(d)}건: {', '.join(d[:10])}{' …' if len(d) > 10 else ''}")
    if result["violations"]:
        lines.append("  ✗ 위반: " + " / ".join(result["violations"]))
    else:
        lines.append("  ✓ 임계 통과")
    return "\n".join(lines)
