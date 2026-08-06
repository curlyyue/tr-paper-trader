"""Daily pipeline orchestrator. All dependencies injected via the constructor."""
import json
from datetime import datetime

from .broker import Broker
from .config import Settings
from .engine import DecisionEngine
from .market import MarketDataProvider
from .news import NewsProvider
from .storage import Repository


class DailyPipeline:
    def __init__(self, settings: Settings, repo: Repository,
                 market: MarketDataProvider, news: NewsProvider,
                 engine: DecisionEngine, broker: Broker):
        self.settings = settings
        self.repo = repo
        self.market = market
        self.news = news
        self.engine = engine
        self.broker = broker

    def is_trading_day(self, day: str) -> bool:
        d = datetime.strptime(day, "%Y-%m-%d").date()
        return d.weekday() < 5 and day not in self.settings.holidays

    def run(self, dry_run: bool = False, force: bool = False,
            trigger: str | None = None, kind: str = "daily") -> None:
        now = datetime.now()
        day = now.date().isoformat()
        now_iso = now.isoformat(timespec="seconds")
        now_human = now.strftime("%Y-%m-%d %H:%M (%A)")

        print(f"Agent: {self.settings.agent_name}  |  engine: {self.engine.name}")
        print(f"Decision time: {now_human}  |  kind: {kind}")
        if trigger:
            print(f"Triggered by: {trigger}")

        if not self.is_trading_day(day) and not force:
            print(f"{day} is not a trading day (weekend/holiday). Use --force to override.")
            return

        # The daily decision happens at most once per day. GitHub's cron is
        # best-effort, so the hourly monitor also tries to catch up a dropped
        # 08:17 slot — this guard is what makes that retry safe.
        if kind == "daily" and not dry_run and self.repo.has_decision(day, "daily"):
            if not force:
                print(f"Daily decision for {day} already recorded — nothing to do. "
                      f"Use --force to run a second one.")
                return
            print(f"[warn] daily decision for {day} already exists; --force given, running again.")

        # 1. Budget top-up (idempotent per period)
        if not dry_run:
            if self.settings.budget_period == "monthly":
                if self.repo.deposit_initial_cash(now_iso, self.settings.initial_cash):
                    print(f"Credited {self.settings.initial_cash} EUR initial budget.")
                month = day[:7]
                if self.settings.monthly_budget and month >= (self.settings.monthly_start or month)[:7]:
                    if self.repo.deposit_monthly_budget(month, now_iso, self.settings.monthly_budget):
                        print(f"Credited {self.settings.monthly_budget} EUR monthly budget for {month}.")
                    else:
                        print(f"Monthly budget for {month} already credited.")
            else:
                if self.repo.deposit_daily_budget(day, now_iso, self.settings.daily_budget):
                    print(f"Credited {self.settings.daily_budget} EUR daily budget.")
                else:
                    print("Budget already credited today.")
        cash = self.repo.cash_balance()
        print(f"Cash: {cash:.2f} EUR")

        # 2. Live data: seed watchlist + agent-discovered watchlist + held positions
        dynamic = {row["ticker"] for row in self.repo.dynamic_watchlist()}
        tickers = sorted(set(self.settings.watchlist) | dynamic | set(self.repo.positions()))
        print(f"Fetching live quotes for {len(tickers)} tickers ...")
        quotes = self.market.quotes(tickers)
        print("Fetching news ...")
        news_lines = [
            f"[{tk}] {h}" for tk in tickers for h in self.news.headlines(tk, limit=3)
        ]
        market_news_lines = self.news.market_headlines(limit_per_topic=5)
        if not dry_run:  # archive exactly what the agent will see
            entries = []
            for line in news_lines:
                tk, _, head = line.partition("] ")
                entries.append((tk.lstrip("["), head))
            entries += [("", h) for h in market_news_lines]
            self.repo.save_news(day, now_iso, entries)

        # 3. Build context
        prompt = self._build_prompt(now_human, cash, quotes, news_lines,
                                    market_news_lines, trigger)
        if dry_run:
            print("\n----- PROMPT (dry run, not sent) -----\n")
            print(prompt)
            return

        # 4. Decide
        print("Asking Claude for a decision ...")
        decision = self.engine.decide(prompt)
        dec_id = self.repo.record_decision(
            day, now_iso, decision.model_used, decision.raw_json, prompt,
            lang="en", kind=kind,
        )
        print(f"\nAnalysis: {decision.analysis}\n")

        # Best-effort translation to 中文 for the dashboard's language toggle.
        # Separate prompt, one extra LLM call; does NOT touch the decisions
        # count, so it never consumes a daily LLM wake-up slot.
        try:
            from .translate import translate_decision
            zh = translate_decision(self.engine, json.loads(decision.raw_json), "zh")
            self.repo.save_translation(dec_id, json.dumps({"zh": zh}, ensure_ascii=False))
            print("  (translated to 中文 for the dashboard)")
        except Exception as e:
            print(f"  [warn] translation skipped: {e}")

        # 5. Apply watchlist changes (discoveries = recommendations to the user).
        #    Additions must be priceable — guards against hallucinated tickers.
        for ticker, reason in decision.watchlist_add:
            if ticker in quotes or self.market.quotes([ticker]).get(ticker):
                self.repo.add_to_watchlist(ticker, now_iso, reason)
                print(f"  ★ watchlist + {ticker}: {reason}")
            else:
                print(f"  ✘ watchlist add rejected (no quote): {ticker}")
        for ticker in decision.watchlist_remove:
            self.repo.remove_from_watchlist(ticker)
            print(f"  ☆ watchlist - {ticker}")

        # 6. Execute at the same decision-time prices
        report = self.broker.execute(day, now_iso, decision.orders, quotes)
        for line in report.executed:
            print(f"  ✔ {line}")
        for o, why in report.rejected:
            print(f"  ✘ rejected {o.action} {o.ticker}: {why}")
        if not decision.orders:
            print("  (agent chose to hold — no orders)")

        # 7. Snapshot
        cash = self.repo.cash_balance()
        pv = self.broker.portfolio_value(quotes)
        self.repo.save_snapshot(day, now_iso, cash, pv)
        print(f"\nEnd of run: cash {cash:.2f} + positions {pv:.2f} = NAV {cash + pv:.2f} EUR")

    def _build_prompt(self, now_human, cash, quotes, news_lines,
                      market_news_lines, trigger=None) -> str:
        from .prompts import build_prompt

        portfolio_lines = []
        for tk, pos in self.repo.positions().items():
            q = quotes.get(tk)
            cur = q.price_eur if q else None
            pnl = f"{(cur / pos.avg_cost - 1) * 100:+.1f}%" if cur else "n/a"
            portfolio_lines.append(
                f"{tk}: {pos.qty:.4f} shares, avg cost {pos.avg_cost:.2f}, "
                f"now {cur or 'n/a'}, P/L {pnl}"
            )
        quote_lines = [
            f"{q.ticker}: {q.price_eur:.2f} EUR, 1d {q.change_1d_pct:+.1f}%, "
            f"1mo {q.change_1mo_pct:+.1f}% (asof {q.asof})"
            for q in quotes.values()
        ]
        history_lines = [
            f"{t['executed_at']} {t['side'].upper()} {t['qty']:.4f} {t['ticker']} "
            f"@ {t['price']:.2f} — {t['rationale'] or ''}"
            for t in self.repo.recent_trades()
        ]
        watchlist_lines = [
            f"{w['ticker']} (added {w['added_at'][:10]}): {w['reason'] or ''}"
            for w in self.repo.dynamic_watchlist()
        ]
        nav_lines = [f"{s['date']}: {s['nav']:.2f}" for s in self.repo.nav_history()]
        # Agent memory: memo (notes to self) or analysis from recent decisions
        memory_lines = []
        rows = self.repo.conn.execute(
            "SELECT created_at, raw_json FROM decisions ORDER BY id DESC LIMIT 7"
        ).fetchall()
        for r in reversed(rows):
            try:
                body = json.loads(r["raw_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            note = body.get("memo") or body.get("analysis")
            if note:
                memory_lines.append(f"[{r['created_at'][:16]}] {note}")
        if self.settings.budget_period == "monthly":
            budget_line = (
                f"Currency is EUR. You started with a lump sum and {self.settings.monthly_budget:.0f} "
                "EUR is added once at the start of each calendar month. Unused cash carries over; "
                "you are NOT required to trade every run — size positions for a monthly, not daily, cadence."
            )
        else:
            budget_line = (
                f"Currency is EUR. Each trading day {self.settings.daily_budget:.0f} EUR is added to "
                "your cash. Unused cash carries over; you are NOT required to trade every day."
            )
        return build_prompt(now_human, cash, portfolio_lines, quote_lines,
                            news_lines, market_news_lines, watchlist_lines,
                            history_lines, nav_lines, memory_lines, budget_line, trigger)


def build_pipeline(settings: Settings) -> DailyPipeline:
    """Composition root: wire the default implementations together."""
    from .engine import create_engine
    from .market import YFinanceMarket
    from .news import YFinanceNews

    repo = Repository(settings.db_path)
    market = YFinanceMarket()
    return DailyPipeline(
        settings=settings,
        repo=repo,
        market=market,
        news=YFinanceNews(),
        engine=create_engine(settings),
        broker=Broker(repo, settings.fee, market),
    )
