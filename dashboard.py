"""Generate a static dashboard (dashboard/index.html) from all agent databases.

  python dashboard.py            # regenerate with live prices
  open dashboard/index.html

The output is a self-contained page (Chart.js from CDN) — the same file can
later be deployed as-is to GitHub/Cloudflare Pages.
"""
import json
from datetime import datetime
from pathlib import Path

from trader.config import DATA_DIR, ROOT
from trader.market import YFinanceMarket
from trader.storage import Repository

OUT_DIR = ROOT / "dashboard"


def collect_agent(db_file: Path, market: YFinanceMarket) -> dict:
    repo = Repository(db_file)
    positions = repo.positions()
    quotes = market.quotes(list(positions)) if positions else {}

    pos_rows, positions_value = [], 0.0
    for tk, pos in positions.items():
        q = quotes.get(tk)
        price = q.price_eur if q else None
        value = pos.qty * price if price else pos.cost
        positions_value += value
        pos_rows.append({
            "ticker": tk, "qty": round(pos.qty, 4), "avg_cost": round(pos.avg_cost, 2),
            "price": round(price, 2) if price else None, "value": round(value, 2),
            "pnl_pct": round((price / pos.avg_cost - 1) * 100, 2) if price else None,
        })

    def variant(src: dict) -> dict:
        return {
            "analysis": src.get("analysis", ""), "reasoning": src.get("reasoning", ""),
            "memo": src.get("memo", ""), "orders": src.get("orders", []),
            "watchlist_changes": src.get("watchlist_changes", {}),
        }

    decisions = []
    for d in repo.conn.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 30"):
        try:
            base = json.loads(d["raw_json"])
        except (json.JSONDecodeError, TypeError):
            base = {}
        native = d["lang"] or "en"
        try:
            trans = json.loads(d["translations"]) if d["translations"] else {}
        except (json.JSONDecodeError, TypeError):
            trans = {}
        # For each UI language, use the native text or its translation,
        # falling back to native if a translation is missing.
        langs = {L: variant(base if L == native else (trans.get(L) or base))
                 for L in ("en", "zh")}
        decisions.append({
            "date": d["date"], "created_at": d["created_at"], "model": d["model"],
            "en": langs["en"], "zh": langs["zh"],
        })

    cash = repo.cash_balance()
    deposits = repo.deposits_total()
    nav = cash + positions_value
    agent = {
        "name": db_file.stem,
        "cash": round(cash, 2), "deposits": round(deposits, 2),
        "fees": round(repo.fees_total(), 2),
        "positions_value": round(positions_value, 2), "nav": round(nav, 2),
        "return_pct": round((nav / deposits - 1) * 100, 2) if deposits else None,
        "positions": pos_rows,
        "closed_positions": [
            {"ticker": c["ticker"], "realized_pnl": round(c["realized_pnl"], 2),
             "realized_pct": round(c["realized_pct"], 2) if c["realized_pct"] is not None else None,
             "fees": round(c["fees"], 2), "closed_at": c["closed_at"]}
            for c in sorted(repo.closed_positions(), key=lambda c: c["closed_at"] or "", reverse=True)
        ],
        "trades": [
            {"executed_at": t["executed_at"], "side": t["side"], "ticker": t["ticker"],
             "qty": round(t["qty"], 4), "price": round(t["price"], 2),
             "fee": t["fee"], "reason": t["rationale"] or ""}
            for t in repo.recent_trades(100)
        ],
        "decisions": decisions,
        "watchlist": [
            {"ticker": w["ticker"], "added_at": w["added_at"], "reason": w["reason"] or ""}
            for w in repo.dynamic_watchlist()
        ],
        "news": [
            {"date": n["date"], "ticker": n["ticker"], "headline": n["headline"]}
            for n in repo.recent_news(150)
        ],
    }
    repo.close()
    return agent


def main():
    market = YFinanceMarket()
    agents = [collect_agent(f, market) for f in sorted(DATA_DIR.glob("*.db"))]

    tickers = sorted({p["ticker"] for a in agents for p in a["positions"]}
                      | {tr["ticker"] for a in agents for tr in a["trades"]})
    price_history = {tk: h for tk in tickers if (h := market.history(tk))}

    data = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "agents": agents,
        "price_history": price_history,
    }
    OUT_DIR.mkdir(exist_ok=True)
    template = (ROOT / "dashboard_template.html").read_text()
    html = template.replace("/*__DATA__*/", json.dumps(data, ensure_ascii=False))
    (OUT_DIR / "index.html").write_text(html)
    print(f"Dashboard written to {OUT_DIR / 'index.html'} "
          f"({len(agents)} agent(s), prices as of {data['generated_at']})")


if __name__ == "__main__":
    main()
