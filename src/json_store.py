"""JSON 영속 스토어 공용 헬퍼 — 지식자산 스토어들이 공유.

지식자산화 작업으로 늘어난 스토어 모듈(knowledge_store/reco_feedback/lifecycle/...)이
동일한 '원자적 JSON 저장(tmp+os.replace)'·'안전 로드'·'타임스탬프'를 각자 중복 구현했다.
하나로 모아 DRY를 지키고 일관성(손상 방지 쓰기)을 보장한다.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path


def read_json(path: Path, default):
    """JSON 로드. 파일 없음/손상 시 default 반환(타입 검증은 호출측 책임)."""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def write_json_atomic(path: Path, data) -> None:
    """원자적 JSON 저장 — 임시 파일에 쓰고 os.replace로 교체(쓰기 중 손상 방지)."""
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def now_iso() -> str:
    """초 단위 ISO 타임스탬프 (스토어 레코드 created_at/updated_at 공통)."""
    return _dt.datetime.now().isoformat(timespec="seconds")
