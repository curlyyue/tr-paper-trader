"""Performance report.

  python report.py                  # agent from .env
  python report.py --model haiku    # that model's book
  python report.py --compare        # all agents side by side (data/*.db)
"""
import argparse
from pathlib import Path

from trader.config import DATA_DIR, load_settings
from trader.market import YFinanceMarket
from trader.storage import Repository


def report_one(settings):
    repo = Repository(settings.db_path)
    market = YFinanceMarket()
    positions = repo.positions()
    quotes = market.quotes(list(positions)) if positions else {}

    print(f"=== Agent: {settings.agent_name} ===")
    print("Positions:")
    total_value = 0.0
    for tk, pos in positions.items():
        q = quotes.get(tk)
        cur = q.price_eur if q else None
        value = pos.qty * cur if cur else pos.cost
        total_value += value
        pnl = f"{(cur / pos.avg_cost - 1) * 100:+.2f}%" if cur else "n/a"
        print(f"  {tk:<10} {pos.qty:>10.4f} @ avg {pos.avg_cost:>8.2f}  "
              f"now {cur or 'n/a':>8}  value {value:>9.2f}  P/L {pnl}")
    if not positions:
        print("  (none)")

    cash = repo.cash_balance()
    deposits = repo.deposits_total()
    nav = cash + total_value
    print(f"\nCash:            {cash:>10.2f} EUR")
    print(f"Positions value: {total_value:>10.2f} EUR")
    print(f"NAV:             {nav:>10.2f} EUR")
    print(f"Total deposited: {deposits:>10.2f} EUR")
    print(f"Fees paid:       {repo.fees_total():>10.2f} EUR")
    if deposits:
        print(f"Total return:    {(nav / deposits - 1) * 100:>+9.2f}%")

    watchlist = repo.dynamic_watchlist()
    print("\nAgent-discovered watchlist (= stock recommendations):")
    for w in watchlist:
        print(f"  {w['ticker']:<10} added {w['added_at'][:10]}  — {w['reason'] or '(no reason given)'}")
    if not watchlist:
        print("  (none yet)")

    print("\nNAV history (last 30 runs):")
    for s in reversed(repo.nav_history(30)):
        print(f"  {s['date']}  NAV {s['nav']:>10.2f}  (cash {s['cash']:.2f} / pos {s['positions_value']:.2f})")
    repo.close()


def compare():
    print(f"{'agent':<15} {'last date':<12} {'NAV':>10} {'deposited':>10} {'return':>8}")
    for db_file in sorted(Path(DATA_DIR).glob("*.db")):
        repo = Repository(db_file)
        hist = repo.nav_history(1)
        deposits = repo.deposits_total()
        if hist:
            nav, last = hist[0]["nav"], hist[0]["date"]
            ret = f"{(nav / deposits - 1) * 100:+.2f}%" if deposits else "n/a"
            print(f"{db_file.stem:<15} {last:<12} {nav:>10.2f} {deposits:>10.2f} {ret:>8}")
        else:
            print(f"{db_file.stem:<15} {'(no runs)':<12} {'-':>10} {deposits:>10.2f} {'-':>8}")
        repo.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--agent")
    ap.add_argument("--compare", action="store_true", help="compare all agents")
    args = ap.parse_args()

    if args.compare:
        compare()
    else:
        report_one(load_settings(model=args.model, agent=args.agent))


if __name__ == "__main__":
    main()
