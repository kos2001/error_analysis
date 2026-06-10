"""파이프라인 2단계: PREPROCESS — 정제 / 필드·엔티티 추출 / 그래프 구축.

입력:  data/raw_issues.json   (ingest 산출물)
출력:  data/processed_issues.json        (정규화된 구조 레코드)
       tmp_db/lsi_graph.graphml / .pkl   (엔티티↔이슈 bipartite 그래프)

repo의 `retrievers.py::GraphRetriever` 와 동일한 그래프 방법:
  각 이슈를 노드로, 정규식+라벨로 추출한 엔티티를 노드로, 둘을 잇는 bipartite 그래프.

이 모듈이 엔티티 패턴/파싱의 **단일 소스**다 (explorer, 레거시 스크립트가 여기서 import).
"""

from __future__ import annotations

import json
import pickle
import re
from pathlib import Path

import networkx as nx

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_DIR = ROOT / "tmp_db"
RAW_JSON = DATA_DIR / "raw_issues.json"
PROCESSED_JSON = DATA_DIR / "processed_issues.json"
GRAPHML = DB_DIR / "lsi_graph.graphml"
PICKLE = DB_DIR / "lsi_graph.pkl"

# ---------------------------------------------------------------------------
# 엔티티 패턴 (단일 소스) — LSI 칩/펌웨어 도메인
# ---------------------------------------------------------------------------
KEY_PATTERNS = [
    r"\b[A-Z]{2,}[A-Z0-9]*-[A-Za-z0-9]+\b",                                   # 칩 코드
    r"\b(?:GDC7|UF40|ISP3|MDM5|DDIT|PMC2|NPX2|DPHY|AMV9|SEC7|NFC3)\.[0-9.]+\b",  # 펌웨어 버전
    r"\b(?:GDC7|UF40|ISP3|MDM5|DDIT|PMC2|NPX2|DPHY|AMV9|SEC7|NFC3)\b",           # 펌웨어 prefix
    r"\b(Firmware|Thermal|Signal Integrity|Timing|Hardware|Power|Security)\b",  # 고장 분류
    r"\b(throttle|throttling|GC|garbage collection|PHY|LTSSM|AER|TRIM|L2P|journal|"
    r"link startup|HS-G4|HS-G3|CDR|ADAPT|HPB|bkops|flicker|banding|anti-flicker|"
    r"HDR|deghosting|RRC|NSA|SA|handover|mmWave|beam|BFR|VRR|LTPO|Vcom|gamma|demura|"
    r"undershoot|transient|DVS|buck|SPMI|I2C|NACK|descriptor|DMA|tiling|quantization|"
    r"INT8|self-refresh|ZQ|DQS|training|VTC|CAN-FD|FIFO|ISR|lockstep|ASIL-D|metastability|"
    r"NFC|eSE|APDU|attestation|secure boot|PCR|glitch|recalibration|"
    r"NCI|NDEF|LLCP|ISO-DEP|TNEP|SNEP|WLC|anticollision|SDD|NVB|WTX|FWT|SYMM|"
    r"NFC-[ABFV]|load modulation|polling loop|RF field|RF discovery|"
    r"Connection Handover|Type [1-5] Tag|T_WAIT|OOB|"
    r"LPCD|SENSF_REQ|Smart Poster|Capability Container|DRBG|nonce|TLV|MIU)\b",  # 기술 용어 글로서리 (+NFC Forum)
    r"\bGen[1-5]\b",
]

_TOKEN_RE = re.compile(r"[A-Za-z0-9가-힣]+")
NOISE_LABELS = {"customer-report"}


def tokenize(s: str) -> list[str]:
    return _TOKEN_RE.findall(s.lower())


def extract_entities(text: str) -> set[str]:
    ents: set[str] = set()
    for pat in KEY_PATTERNS:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            ents.add(m.group(0).strip())
    return ents


# ---------------------------------------------------------------------------
# 필드 파싱 (wiki-markup 본문/코멘트 → 구조 레코드)
# ---------------------------------------------------------------------------
def _field(text: str, marker: str) -> str:
    m = re.search(rf"\*{re.escape(marker)}\*\s*:\s*(.+)", text)
    return m.group(1).strip() if m else ""


def _section(text: str, header: str) -> str:
    m = re.search(rf"h2\.\s*{re.escape(header)}[^\n]*\n+(.+?)(?:\n\nh2\.|\Z)", text, flags=re.S)
    return m.group(1).strip() if m else ""


def _comment_block(comment: str, label: str) -> str:
    m = re.search(rf"\*{re.escape(label)}\*\s*:\s*\n?(.+?)(?:\n\n\*|\n\n----|\Z)", comment, flags=re.S)
    return m.group(1).strip() if m else ""


BOT_COMMENT_MARKER = "자동 근본원인 분석"  # RCA-bot 댓글 식별 (scripts/rca_comment.py)


def parse_issue(raw: dict) -> dict:
    """raw 이슈 → 정규화 레코드 (필드 + 엔티티 + 검색용 본문).

    RCA-bot이 단 자동 분석 댓글은 시니어 분석으로 오인되어 KB를 오염시키므로 제외한다.
    """
    desc = raw.get("description", "")
    comments = [c for c in raw.get("comments", []) if BOT_COMMENT_MARKER not in c[:80]]
    comment = comments[0] if comments else ""
    rec = {
        "key": raw["key"],
        "summary": raw.get("summary", ""),
        "status": raw.get("status", ""),
        "priority": raw.get("priority", ""),
        "labels": raw.get("labels", []),
        "components": raw.get("components", []),
        "chip": _field(desc, "칩 모델"),
        "category": _field(desc, "고장 분류") or "기타",
        "severity": _field(desc, "심각도"),
        "customer": _field(desc, "고객사"),
        "fw_version": _field(desc, "펌웨어 버전"),
        "symptom": _section(desc, "증상 (Symptom)"),
        "debug_approach": _comment_block(comment, "디버깅 접근"),
        "root_cause": _comment_block(comment, "근본 원인 (Root Cause)"),
        "resolution": _comment_block(comment, "적용 해결책 (Resolution)"),
        "workaround": _comment_block(comment, "임시 우회책 (Workaround)"),
    }
    # 검색 결과로 반환할 본문 (봇 댓글 제외)
    rec["context_text"] = (
        f"### {rec['key']} — {rec['summary']}\n\n{desc}\n\n"
        + "\n\n".join(comments)
    ).strip()
    # 엔티티: 본문/요약/코멘트 정규식 + 라벨 + 컴포넌트 (봇 댓글 제외)
    blob = "\n".join([rec["summary"], desc, *comments])
    ents = extract_entities(blob)
    ents.update(raw.get("labels", []))
    ents.update(raw.get("components", []))
    ents -= NOISE_LABELS
    rec["entities"] = sorted(e for e in ents if len(e) >= 2)
    return rec


def build_records(raw_list: list[dict]) -> list[dict]:
    return [parse_issue(r) for r in raw_list]


# ---------------------------------------------------------------------------
# 그래프 구축 + 영속화
# ---------------------------------------------------------------------------
def build_graph(records: list[dict]) -> nx.Graph:
    g = nx.Graph()
    for rec in records:
        node = f"issue:{rec['key']}"
        g.add_node(
            node, kind="issue", key=rec["key"], title=rec["summary"],
            status=rec["status"], priority=rec["priority"],
            category=rec["category"], chip=rec["chip"],
            labels=",".join(rec["labels"]),
            text=rec["context_text"],
            root_cause=rec["root_cause"], resolution=rec["resolution"],
            workaround=rec["workaround"], symptom=rec["symptom"],
            debug_approach=rec["debug_approach"],
        )
        for ent in rec["entities"]:
            en = f"ent:{ent.lower()}"
            if en not in g:
                g.add_node(en, kind="entity", name=ent)
            g.add_edge(node, en)
    return g


def persist(records: list[dict], g: nx.Graph) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    DB_DIR.mkdir(exist_ok=True)
    PROCESSED_JSON.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    nx.write_graphml(g, GRAPHML)
    with PICKLE.open("wb") as f:
        pickle.dump(g, f)


def run(raw_path: Path = RAW_JSON) -> tuple[list[dict], nx.Graph]:
    if not raw_path.exists():
        raise FileNotFoundError(f"{raw_path} 없음 — 먼저 src/ingest.py 실행")
    raw = json.load(raw_path.open())
    records = build_records(raw)
    g = build_graph(records)
    persist(records, g)
    return records, g


def main() -> int:
    print("[preprocess] 정제/엔티티 추출/그래프 구축 중...")
    records, g = run()
    n_issue = sum(1 for _, d in g.nodes(data=True) if d.get("kind") == "issue")
    n_ent = sum(1 for _, d in g.nodes(data=True) if d.get("kind") == "entity")
    hubs = sorted(
        ((d["name"], g.degree(n)) for n, d in g.nodes(data=True) if d.get("kind") == "entity"),
        key=lambda x: -x[1])[:8]
    print(f"[preprocess] 레코드 {len(records)} · 이슈노드 {n_issue} · 엔티티노드 {n_ent} · 엣지 {g.number_of_edges()}")
    print(f"[preprocess] 허브 엔티티: {hubs}")
    print(f"[preprocess] → {PROCESSED_JSON.relative_to(ROOT)}, {GRAPHML.relative_to(ROOT)}, {PICKLE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
