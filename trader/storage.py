"""Repository over SQLite. One database file per agent (data/<agent>.db)."""
import sqlite3
from pathlib import Path

from .models import Position

SCHEMA = """
CREATE TABLE IF NOT EXISTS cash_ledger (
    id     INTEGER PRIMARY KEY,
    date   TEXT NOT NULL,
    ts     TEXT NOT NULL,
    type   TEXT NOT NULL CHECK (type IN ('deposit', 'buy', 'sell')),
    amount REAL NOT NULL,          -- positive = cash in, negative = cash out
    note   TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY,
    date        TEXT NOT NULL,
    executed_at TEXT NOT NULL,     -- exact decision/execution timestamp
    ticker      TEXT NOT NULL,
    side        TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    qty         REAL NOT NULL,
    price       REAL NOT NULL,     -- EUR per share at execution time
    fee         REAL NOT NULL DEFAULT 0,
    rationale   TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id           INTEGER PRIMARY KEY,
    date         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    model        TEXT,
    raw_json     TEXT NOT NULL,
    prompt       TEXT,            -- full input the model saw (audit trail)
    lang         TEXT,            -- language raw_json is written in ('en'/'zh')
    translations TEXT             -- JSON {lang: decision-shaped dict} for the dashboard
);
CREATE TABLE IF NOT EXISTS snapshots (
    date            TEXT PRIMARY KEY,
    ts              TEXT NOT NULL,
    cash            REAL NOT NULL,
    positions_value REAL NOT NULL,
    nav             REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS watchlist (
    ticker   TEXT PRIMARY KEY,   -- agent-discovered, on top of the config seed list
    added_at TEXT NOT NULL,
    reason   TEXT                -- doubles as the agent's recommendation to the user
);
CREATE TABLE IF NOT EXISTS news_seen (
    id       INTEGER PRIMARY KEY,
    date     TEXT NOT NULL,
    ts       TEXT NOT NULL,
    ticker   TEXT NOT NULL DEFAULT '',  -- '' = general market news
    headline TEXT NOT NULL,
    UNIQUE(date, ticker, headline)
);
"""


class Repository:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # Migrations for columns added to `decisions` over time
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(decisions)")}
        for col in ("prompt", "lang", "translations"):
            if col not in cols:
                self.conn.execute(f"ALTER TABLE decisions ADD COLUMN {col} TEXT")
        self.conn.commit()

    # --- cash ---

    def cash_balance(self) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS bal FROM cash_ledger"
        ).fetchone()
        return row["bal"]

    def deposit_daily_budget(self, day: str, ts: str, amount: float) -> bool:
        """Credit the budget once per trading day; returns False if already done."""
        dup = self.conn.execute(
            "SELECT 1 FROM cash_ledger WHERE date = ? AND type = 'deposit'", (day,)
        ).fetchone()
        if dup:
            return False
        self.conn.execute(
            "INSERT INTO cash_ledger (date, ts, type, amount, note) "
            "VALUES (?, ?, 'deposit', ?, 'daily budget')",
            (day, ts, amount),
        )
        self.conn.commit()
        return True

    def deposits_total(self) -> float:
        return self.conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM cash_ledger WHERE type = 'deposit'"
        ).fetchone()["s"]

    # --- trades / positions ---

    def positions(self) -> dict[str, Position]:
        """Rebuilt from trade history, average cost basis."""
        pos: dict[str, Position] = {}
        for t in self.conn.execute("SELECT * FROM trades ORDER BY id"):
            p = pos.setdefault(t["ticker"], Position(t["ticker"], 0.0, 0.0))
            if t["side"] == "buy":
                p.cost += t["qty"] * t["price"]
                p.qty += t["qty"]
            else:
                if p.qty > 1e-9:
                    p.cost -= t["qty"] * (p.cost / p.qty)
                p.qty -= t["qty"]
        return {k: v for k, v in pos.items() if v.qty > 1e-9}

    def record_trade(self, day, executed_at, ticker, side, qty, price, fee, rationale):
        self.conn.execute(
            "INSERT INTO trades (date, executed_at, ticker, side, qty, price, fee, rationale) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (day, executed_at, ticker, side, qty, price, fee, rationale),
        )
        gross = qty * price
        amount = -(gross + fee) if side == "buy" else (gross - fee)
        self.conn.execute(
            "INSERT INTO cash_ledger (date, ts, type, amount, note) VALUES (?,?,?,?,?)",
            (day, executed_at, side, amount,
             f"{side} {qty:.4f} {ticker} @ {price:.2f} EUR (fee {fee:.2f})"),
        )
        self.conn.commit()

    def recent_trades(self, limit=20):
        return self.conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def fees_total(self) -> float:
        return self.conn.execute(
            "SELECT COALESCE(SUM(fee), 0) AS s FROM trades"
        ).fetchone()["s"]

    # --- dynamic watchlist (agent discoveries / recommendations) ---

    def dynamic_watchlist(self):
        return self.conn.execute(
            "SELECT * FROM watchlist ORDER BY added_at DESC"
        ).fetchall()

    def add_to_watchlist(self, ticker: str, ts: str, reason: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO watchlist (ticker, added_at, reason) VALUES (?,?,?)",
            (ticker, ts, reason),
        )
        self.conn.commit()

    def remove_from_watchlist(self, ticker: str):
        self.conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
        self.conn.commit()

    # --- news archive (what the agent saw) ---

    def save_news(self, day: str, ts: str, entries: list[tuple[str, str]]):
        """entries: (ticker, headline); ticker '' for general market news."""
        self.conn.executemany(
            "INSERT OR IGNORE INTO news_seen (date, ts, ticker, headline) VALUES (?,?,?,?)",
            [(day, ts, tk, h) for tk, h in entries],
        )
        self.conn.commit()

    def recent_news(self, limit=100):
        return self.conn.execute(
            "SELECT * FROM news_seen ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    # --- decisions / snapshots ---

    def record_decision(self, day, created_at, model, raw_json, prompt=None, lang="en"):
        cur = self.conn.execute(
            "INSERT INTO decisions (date, created_at, model, raw_json, prompt, lang) "
            "VALUES (?,?,?,?,?,?)",
            (day, created_at, model, raw_json, prompt, lang),
        )
        self.conn.commit()
        return cur.lastrowid

    def save_translation(self, decision_id: int, translations_json: str):
        self.conn.execute(
            "UPDATE decisions SET translations = ? WHERE id = ?",
            (translations_json, decision_id),
        )
        self.conn.commit()

    def save_snapshot(self, day, ts, cash, positions_value):
        self.conn.execute(
            "INSERT OR REPLACE INTO snapshots (date, ts, cash, positions_value, nav) "
            "VALUES (?,?,?,?,?)",
            (day, ts, cash, positions_value, cash + positions_value),
        )
        self.conn.commit()

    def decisions_count(self, day: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) AS c FROM decisions WHERE date = ?", (day,)
        ).fetchone()["c"]

    def nav_history(self, limit=30):
        return self.conn.execute(
            "SELECT * FROM snapshots ORDER BY date DESC LIMIT ?", (limit,)
        ).fetchall()

    def close(self):
        self.conn.close()
