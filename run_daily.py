"""Entry point: run one trading decision for one agent.

Usage:
  python run_daily.py                    # model/agent from .env
  python run_daily.py --model haiku      # separate book in data/haiku.db
  python run_daily.py --dry-run          # data + prompt only, no LLM, no trades
"""
import argparse

from trader.config import load_settings
from trader.pipeline import build_pipeline


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="opus | sonnet | haiku | full model id (overrides .env)")
    ap.add_argument("--agent", help="ledger name, defaults to model name (overrides .env)")
    ap.add_argument("--backend", choices=["cli", "api"], help="overrides .env")
    ap.add_argument("--dry-run", action="store_true", help="no LLM call, no trades")
    ap.add_argument("--force", action="store_true", help="run even on non-trading days")
    args = ap.parse_args()

    settings = load_settings(model=args.model, agent=args.agent, backend=args.backend)
    build_pipeline(settings).run(dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
