"""Simulated broker: validates orders and executes them at decision-time prices."""
from .market import MarketDataProvider
from .models import ExecutionReport, Order, Quote
from .storage import Repository


class Broker:
    def __init__(self, repo: Repository, fee: float, market: MarketDataProvider):
        self.repo = repo
        self.fee = fee
        self.market = market

    def _quote(self, ticker: str, quotes: dict[str, Quote]) -> Quote | None:
        """Known quote, or fetch on demand — lets the agent trade any valid
        ticker it discovered, not just the pre-fetched universe."""
        if ticker not in quotes:
            fetched = self.market.quotes([ticker]).get(ticker)
            if fetched:
                quotes[ticker] = fetched  # visible to portfolio_value afterwards
        return quotes.get(ticker)

    def execute(self, day: str, now: str, orders: tuple[Order, ...],
                quotes: dict[str, Quote]) -> ExecutionReport:
        executed, rejected = [], []
        for o in orders:
            quote = self._quote(o.ticker, quotes)
            if o.action not in ("buy", "sell"):
                rejected.append((o, "unknown action"))
                continue
            if not quote:
                rejected.append((o, "ticker not found / no quote available"))
                continue
            price = quote.price_eur

            if o.action == "buy":
                eur = o.eur or 0.0
                cash = self.repo.cash_balance()
                if eur <= self.fee:
                    rejected.append((o, f"amount {eur:.2f} EUR does not cover the {self.fee:.2f} EUR fee"))
                    continue
                if eur > cash + 1e-6:
                    rejected.append((o, f"insufficient cash ({cash:.2f} EUR available)"))
                    continue
                qty = (eur - self.fee) / price
                self.repo.record_trade(day, now, o.ticker, "buy", qty, price, self.fee, o.reason)
                executed.append(f"BUY  {qty:.4f} {o.ticker} @ {price:.2f} EUR ({eur:.2f} EUR incl. fee)")
            else:
                pos = self.repo.positions().get(o.ticker)
                if not pos:
                    rejected.append((o, "no position held"))
                    continue
                qty = pos.qty if o.qty == "all" else float(o.qty or 0)
                if qty <= 0:
                    rejected.append((o, "sell qty missing or zero"))
                    continue
                if qty > pos.qty + 1e-6:
                    rejected.append((o, f"only {pos.qty:.4f} shares held"))
                    continue
                qty = min(qty, pos.qty)
                self.repo.record_trade(day, now, o.ticker, "sell", qty, price, self.fee, o.reason)
                executed.append(f"SELL {qty:.4f} {o.ticker} @ {price:.2f} EUR")
        return ExecutionReport(tuple(executed), tuple(rejected))

    def portfolio_value(self, quotes: dict[str, Quote]) -> float:
        total = 0.0
        for ticker, pos in self.repo.positions().items():
            quote = quotes.get(ticker)
            total += pos.qty * quote.price_eur if quote else pos.cost
        return total
