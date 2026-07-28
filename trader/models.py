"""Domain objects passed between layers."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Quote:
    ticker: str
    price_eur: float
    currency: str
    change_1d_pct: float
    change_1mo_pct: float
    asof: str  # ISO timestamp of when the quote was fetched


@dataclass(frozen=True)
class Order:
    action: str                 # "buy" | "sell"
    ticker: str
    eur: float | None = None    # buys: total cash to spend incl. fee
    qty: float | str | None = None  # sells: share count or "all"
    reason: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Order":
        return cls(
            action=str(d.get("action", "")).lower(),
            ticker=str(d.get("ticker", "")),
            eur=float(d["eur"]) if d.get("eur") is not None else None,
            qty=d.get("qty"),
            reason=str(d.get("reason", "")),
        )


@dataclass(frozen=True)
class Decision:
    analysis: str
    orders: tuple[Order, ...]
    raw_json: str    # verbatim model output, archived in the DB
    model_used: str
    watchlist_add: tuple[tuple[str, str], ...] = ()   # (ticker, reason)
    watchlist_remove: tuple[str, ...] = ()


@dataclass
class Position:
    ticker: str
    qty: float
    cost: float      # total cost basis in EUR

    @property
    def avg_cost(self) -> float:
        return self.cost / self.qty if self.qty else 0.0


@dataclass(frozen=True)
class ExecutionReport:
    executed: tuple[str, ...]
    rejected: tuple[tuple[Order, str], ...]  # (order, reason)
