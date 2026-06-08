"""Structured RVP data in SQLite — plans, error codes, escalation rules.

These are facts that fit a relational schema better than free-text RAG:
exact thresholds, numeric limits, refund policy. The agent can query
this DB with deterministic precision instead of hoping retrieval finds it.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "tmp_db" / "rvp_structured.sqlite"


def init_db(db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    cur.executescript("""
        DROP TABLE IF EXISTS error_codes;
        DROP TABLE IF EXISTS plans;
        DROP TABLE IF EXISTS escalation_rules;

        CREATE TABLE error_codes (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            cause TEXT NOT NULL,
            fix TEXT NOT NULL,
            threshold TEXT
        );

        CREATE TABLE plans (
            name TEXT PRIMARY KEY,
            monthly_price_usd REAL,
            daily_inference_limit INTEGER,
            rate_limit_per_sec INTEGER,
            refund_window_days INTEGER,
            refund_usage_cap INTEGER
        );

        CREATE TABLE escalation_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger_keyword TEXT NOT NULL,
            min_amount_usd REAL,
            target_tier TEXT NOT NULL,
            note TEXT
        );
    """)

    cur.executemany(
        "INSERT INTO error_codes VALUES (?, ?, ?, ?, ?)",
        [
            ("E001", "Camera not detected", "USB/PoE cable issue", "Reconnect USB; verify PoE switch 802.3af 15.4W min", None),
            ("E014", "Cloud sync failure", "Auth failure", "Re-pair device or rotate API key in dashboard", None),
            ("E022", "Model load failure", "Edge model corrupted", "Re-download model from Settings > Device > Maintenance", None),
            ("E033", "Thermal throttling", "Device exceeded temperature limit", "Improve ventilation; auto-resume when cool", "trip=85C, resume=75C"),
            ("E101", "Insufficient illumination", "Scene lux too low", "Add lighting or enable IR mode on RVP-Cam-200", "min=50 lux"),
        ],
    )

    cur.executemany(
        "INSERT INTO plans VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("Free", 0.0, 100, 0, 0, 0),
            ("Pro", 29.0, 10000, 100, 14, 1000),
            ("Enterprise", None, None, 1000, 0, 0),
        ],
    )

    cur.executemany(
        "INSERT INTO escalation_rules (trigger_keyword, min_amount_usd, target_tier, note) VALUES (?, ?, ?, ?)",
        [
            ("refund", 200.0, "L2", "Refund disputes above $200 must escalate"),
            ("RMA", None, "L2", "Hardware RMA always escalates"),
            ("security incident", None, "L2", "Security incidents always escalate"),
            ("model accuracy regression", None, "L3", "Accuracy regressions go to engineering"),
            ("platform outage", None, "L3", "Outages go to engineering"),
        ],
    )

    con.commit()
    con.close()


def query(sql: str, params: tuple = ()) -> list[dict]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(sql, params).fetchall()
    con.close()
    return [dict(r) for r in rows]


def lookup_error(code: str) -> dict | None:
    rows = query("SELECT * FROM error_codes WHERE code = ?", (code.upper(),))
    return rows[0] if rows else None


def lookup_plan(name: str) -> dict | None:
    rows = query("SELECT * FROM plans WHERE name = ?", (name.title(),))
    return rows[0] if rows else None


def check_escalation(text: str, amount_usd: float | None = None) -> list[dict]:
    rows = query("SELECT * FROM escalation_rules")
    hits = []
    tl = text.lower()
    for r in rows:
        if r["trigger_keyword"].lower() in tl:
            if r["min_amount_usd"] is None or (amount_usd is not None and amount_usd >= r["min_amount_usd"]):
                hits.append(r)
    return hits


if __name__ == "__main__":
    init_db()
    print("Initialized:", DB_PATH)
    print("E033 ->", lookup_error("E033"))
    print("Pro ->", lookup_plan("Pro"))
    print("Escalation 'refund' $350 ->", check_escalation("I want refund", 350))
