"""Intraday monitor: cheap price checks, wakes the LLM agent only on triggers.

Run frequently (e.g. every 30 min during market hours) via cron — quote checks
are free; the LLM is only consulted when something actually moved:
  - a HELD position moved more than `held_move_pct` vs previous close, or
  - a watchlist ticker moved more than `watch_move_pct`.
A per-day LLM run cap (`max_llm_runs_per_day`, counting the regular daily run)
protects your subscription quota.

  python monitor.py --model opus
  python monitor.py --model opus --check-only   # show triggers, never call LLM
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

from trader.config import ROOT, load_settings
from trader.models import Quote
from trader.pipeline import build_pipeline

MONITOR_CFG = json.loads((ROOT / "config.json").read_text()).get("monitor", {})


def find_triggers(quotes: dict[str, Quote], held: set[str],
                  held_move: float, watch_move: float) -> list[str]:
    triggers = []
    for tk, q in quotes.items():
        move = q.change_1d_pct
        if tk in held and abs(move) >= held_move:
            triggers.append(f"HELD position {tk} moved {move:+.1f}% vs previous close "
                            f"(now {q.price_eur:.2f} EUR)")
        elif tk not in held and abs(move) >= watch_move:
            triggers.append(f"Watchlist ticker {tk} moved {move:+.1f}% vs previous close "
                            f"(now {q.price_eur:.2f} EUR)")
    return triggers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--agent")
    ap.add_argument("--backend", choices=["cli", "api"])
    ap.add_argument("--check-only", action="store_true", help="report triggers, never call the LLM")
    ap.add_argument("--force", action="store_true", help="ignore trading-day/market-hours checks")
    args = ap.parse_args()

    settings = load_settings(model=args.model, agent=args.agent, backend=args.backend)
    pipeline = build_pipeline(settings)
    now = datetime.now()
    day = now.date().isoformat()

    h0, h1 = MONITOR_CFG.get("market_hours", [9, 22])
    if not args.force:
        if not pipeline.is_trading_day(day):
            print(f"{day}: not a trading day, monitor idle.")
            return
        if not (h0 <= now.hour < h1):
            print(f"{now:%H:%M}: outside market hours ({h0}:00–{h1}:00), monitor idle.")
            return

    held = set(pipeline.repo.positions())
    dynamic = {row["ticker"] for row in pipeline.repo.dynamic_watchlist()}
    tickers = sorted(set(settings.watchlist) | dynamic | held)
    quotes = pipeline.market.quotes(tickers)

    triggers = find_triggers(
        quotes, held,
        MONITOR_CFG.get("held_move_pct", 5.0),
        MONITOR_CFG.get("watch_move_pct", 8.0),
    )
    if not triggers:
        print(f"{now:%H:%M}: no significant moves, agent stays asleep.")
        return

    print(f"{now:%H:%M}: {len(triggers)} trigger(s):")
    for t in triggers:
        print(f"  ! {t}")

    if args.check_only:
        print("(--check-only: LLM not called)")
        return

    cap = MONITOR_CFG.get("max_llm_runs_per_day", 3)
    runs = pipeline.repo.decisions_count(day)
    if runs >= cap:
        print(f"LLM run cap reached for today ({runs}/{cap}) — skipping to protect quota.")
        return

    pipeline.run(trigger="; ".join(triggers))


if __name__ == "__main__":
    main()
