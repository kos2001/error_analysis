"""파이프라인 3단계: EXPLORER — 그래프 탐색 / 검색 / 시각화.

전처리가 만든 그래프(tmp_db/lsi_graph.pkl)를 로드해:
  - search(query): query 엔티티 추출 → 연결된 이슈로 traverse → 엔티티 중첩 랭킹
                   (repo GraphRetriever 와 동일한 방법) → 시니어 해결책 반환
  - stats(): 분류/칩/허브 엔티티 통계
  - to_html(): 인터랙티브 지식 그래프(HTML) 생성 (vis-network CDN, 설치 불필요)

CLI:
    .venv/bin/python src/explorer.py "PM9C3 thermal throttle link down"   # 검색
    .venv/bin/python src/explorer.py --stats                              # 통계
    .venv/bin/python src/explorer.py --viz                                # HTML 생성/열기 안내
"""

from __future__ import annotations

import argparse
import html
import json
import pickle
from collections import Counter
from pathlib import Path

import networkx as nx

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from preprocess import extract_entities, tokenize  # noqa: E402  (단일 소스 재사용)

ROOT = Path(__file__).resolve().parent.parent
PICKLE = ROOT / "tmp_db" / "lsi_graph.pkl"
OUT_HTML = ROOT / "tmp_db" / "lsi_solution_graph.html"

CATEGORY_COLORS = {
    "Firmware": "#4F81BD", "Thermal": "#C0504D", "Signal Integrity": "#9BBB59",
    "Timing": "#8064A2", "Hardware": "#F79646", "Power": "#E6B800", "Security": "#4BACC6",
}


class GraphExplorer:
    def __init__(self, graph_path: Path = PICKLE):
        if not graph_path.exists():
            raise FileNotFoundError(
                f"그래프 없음: {graph_path}\n먼저 실행: src/ingest.py → src/preprocess.py")
        with graph_path.open("rb") as f:
            self.g: nx.Graph = pickle.load(f)
        self.issues = [n for n, d in self.g.nodes(data=True) if d.get("kind") == "issue"]

    # ---------- 검색 (질의 → 유사 고장) ----------
    def _rank(self, query: str) -> list[tuple[str, int]]:
        q_ents = extract_entities(query)
        scores: dict[str, int] = {}
        for ent in q_ents:
            en = f"ent:{ent.lower()}"
            if en in self.g:
                for issue in self.g.neighbors(en):
                    scores[issue] = scores.get(issue, 0) + 1
        if scores:
            return sorted(scores.items(), key=lambda x: -x[1])
        # Fallback: 제목 토큰 중첩
        qtoks = set(tokenize(query))
        scored = [(sum(1 for t in tokenize(self.g.nodes[n]["title"]) if t in qtoks), n)
                  for n in self.issues]
        scored.sort(reverse=True)
        return [(n, s) for s, n in scored if s > 0] or [(n, 0) for n in self.issues[:1]]

    def search(self, query: str, k: int = 3) -> list[dict]:
        out = []
        for node, score in self._rank(query)[:k]:
            d = self.g.nodes[node]
            out.append({
                "key": d["key"], "score": score, "title": d["title"],
                "category": d.get("category", ""), "chip": d.get("chip", ""),
                "root_cause": d.get("root_cause", ""), "resolution": d.get("resolution", ""),
                "workaround": d.get("workaround", ""), "symptom": d.get("symptom", ""),
            })
        return out

    def retrieve_context(self, query: str, k: int = 3) -> str:
        nodes = [n for n, _ in self._rank(query)[:k]]
        return "\n\n---\n\n".join(self.g.nodes[n]["text"] for n in nodes)

    # ---------- 통계 ----------
    def stats(self) -> dict:
        cats = Counter(self.g.nodes[n].get("category", "?") for n in self.issues)
        chips = Counter(self.g.nodes[n].get("chip", "?") for n in self.issues)
        ent_deg = sorted(
            ((d["name"], self.g.degree(n)) for n, d in self.g.nodes(data=True) if d.get("kind") == "entity"),
            key=lambda x: -x[1])[:10]
        return {"issues": len(self.issues), "by_category": dict(cats),
                "by_chip": dict(chips), "hub_entities": ent_deg,
                "edges": self.g.number_of_edges()}

    # ---------- 시각화 ----------
    def to_html(self, path: Path = OUT_HTML) -> Path:
        nodes, edges, seen = [], [], set()

        def add(nid, **kw):
            if nid not in seen:
                seen.add(nid); nodes.append({"id": nid, **kw})

        for n in self.issues:
            d = self.g.nodes[n]
            cat = d.get("category", "기타"); color = CATEGORY_COLORS.get(cat, "#888888")
            cat_id = f"cat:{cat}"
            add(cat_id, label=f"📂 {cat}", shape="hexagon", size=34, color=color,
                title=f"<b>고장 분류: {html.escape(cat)}</b>")
            chip = d.get("chip", "?"); chip_id = f"chip:{chip}"
            add(chip_id, label=f"🔧 {chip}", shape="box", size=20, color="#cfcfcf",
                title=f"<b>칩: {html.escape(chip)}</b>")
            dbg = d.get("debug_approach", "")
            dbg_id = None
            if dbg:
                lbl = dbg.split(".")[0][:40]; dbg_id = f"dbg:{lbl}"
                add(dbg_id, label=f"🛠 {lbl}…", shape="ellipse", size=24,
                    color="#2C3E50", font={"color": "#fff"},
                    title=f"<b>디버깅 접근</b><br>{html.escape(dbg)}")
            add(f"issue:{d['key']}", label=d["key"], shape="dot", size=10,
                color={"background": color, "border": "#333"}, title=self._tooltip(d))
            edges.append({"from": f"issue:{d['key']}", "to": cat_id, "color": {"opacity": 0.35}})
            edges.append({"from": f"issue:{d['key']}", "to": chip_id, "color": {"opacity": 0.15}, "dashes": True})
            if dbg_id:
                edges.append({"from": f"issue:{d['key']}", "to": dbg_id, "color": {"opacity": 0.25}})

        n_issue = sum(1 for nd in nodes if nd["id"].startswith("issue:"))
        legend = "".join(
            f"<span><span class='sw' style='background:{c}'></span>{cat}</span>"
            for cat, c in CATEGORY_COLORS.items())
        path.write_text(_HTML.format(
            n_issue=n_issue, legend=legend,
            nodes_json=json.dumps(nodes, ensure_ascii=False),
            edges_json=json.dumps(edges, ensure_ascii=False)), encoding="utf-8")
        return path

    @staticmethod
    def _tooltip(d: dict) -> str:
        def e(s): return html.escape(s or "").replace("\n", "<br>")
        return (f"<div style='max-width:420px;font:12px sans-serif'>"
                f"<b>{e(d['key'])} — {e(d['title'])}</b><br>"
                f"<span style='color:#888'>칩:{e(d.get('chip',''))} · 분류:{e(d.get('category',''))}</span><hr style='margin:4px 0'>"
                f"<b>증상</b><br>{e(d.get('symptom','')[:300])}<br><br>"
                f"<b>🔍 근본원인</b><br>{e(d.get('root_cause','')[:400])}<br><br>"
                f"<b>✅ 해결책</b><br>{e(d.get('resolution','')[:400])}<br><br>"
                f"<b>↪ 우회책</b><br>{e(d.get('workaround','')[:200])}</div>")


_HTML = """<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<title>LSI 고장 해결 지식 그래프</title>
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<style>body{{margin:0;font-family:-apple-system,sans-serif}}
#hdr{{padding:10px 16px;background:#1b2838;color:#fff}}#hdr h1{{margin:0;font-size:18px}}
#hdr p{{margin:4px 0 0;font-size:12px;color:#9fb3c8}}#legend span{{margin-right:12px;font-size:12px}}
.sw{{display:inline-block;width:11px;height:11px;border-radius:2px;vertical-align:middle;margin-right:4px}}
#net{{width:100vw;height:calc(100vh - 78px);background:#0e1621}}</style></head><body>
<div id="hdr"><h1>LSI 고장 해결 지식 그래프 — 완료 이슈 {n_issue}건</h1>
<p>📂 고장분류 · 🛠 디버깅 접근(해결 방법) · 🔧 칩 · ● 이슈(hover: 증상→근본원인→해결책)</p>
<div id="legend">{legend}</div></div><div id="net"></div>
<script>
const nodes=new vis.DataSet({nodes_json});const edges=new vis.DataSet({edges_json});
new vis.Network(document.getElementById('net'),{{nodes,edges}},{{
 nodes:{{font:{{color:'#e8eef5',size:13}}}},edges:{{smooth:{{type:'continuous'}},color:{{color:'#5b6b7d'}}}},
 physics:{{barnesHut:{{gravitationalConstant:-8000,springLength:120,springConstant:0.03}},stabilization:{{iterations:250}}}},
 interaction:{{hover:true,tooltipDelay:80,navigationButtons:true,keyboard:true}}}});
</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description="그래프 탐색/검색/시각화 (explorer)")
    ap.add_argument("query", nargs="?", help="검색 질의")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--viz", action="store_true", help="HTML 그래프 생성")
    ap.add_argument("-k", type=int, default=3)
    args = ap.parse_args()

    ex = GraphExplorer()
    if args.stats:
        s = ex.stats()
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    if args.viz:
        p = ex.to_html()
        print(f"[explorer] 그래프 생성 → {p.relative_to(ROOT)}  (open {p})")
        return 0
    if not args.query:
        ap.error("query 또는 --stats / --viz 중 하나가 필요합니다.")
    print(f"[explorer] query: {args.query}\n")
    for r in ex.search(args.query, k=args.k):
        print(f"■ {r['key']} (중첩 {r['score']}) — {r['title']}")
        print(f"   분류:{r['category']} · 칩:{r['chip']}")
        print(f"   🔍 근본원인: {r['root_cause'][:140]}")
        print(f"   ✅ 해결책 : {r['resolution'][:140]}")
        print(f"   ↪ 우회책 : {r['workaround'][:100]}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
