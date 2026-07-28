"""Market data provider. Protocol + yfinance implementation.

Prices are near-real-time (Yahoo's last trade, typically <= 15 min delayed),
NOT the daily close — so whatever time the pipeline runs, the agent decides
on, and is filled at, the price of that moment.  All prices in EUR.
"""
from datetime import datetime
from typing import Protocol

import yfinance as yf

from .models import Quote


class MarketDataProvider(Protocol):
    def quotes(self, tickers: list[str]) -> dict[str, Quote]: ...


class YFinanceMarket:
    def __init__(self):
        self._fx_cache: dict[str, float] = {}

    def _fx_to_eur(self, currency: str) -> float:
        if currency in ("EUR", None, ""):
            return 1.0
        if currency == "GBp":  # pence
            return self._fx_to_eur("GBP") / 100.0
        if currency not in self._fx_cache:
            hist = yf.Ticker(f"{currency}EUR=X").history(period="5d")
            self._fx_cache[currency] = float(hist["Close"].iloc[-1])
        return self._fx_cache[currency]

    def _snapshot(self, ticker: str) -> Quote | None:
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="1mo")
            if hist.empty:
                return None
            closes = hist["Close"].dropna()
            # Prefer the live last-trade price; fall back to the latest bar
            try:
                last = float(t.fast_info["last_price"])
            except Exception:
                last = float(closes.iloc[-1])
            prev = float(closes.iloc[-2]) if len(closes) > 1 else last
            first = float(closes.iloc[0])
            currency = t.fast_info.get("currency") or "EUR"
            fx = self._fx_to_eur(currency)
            return Quote(
                ticker=ticker,
                price_eur=round(last * fx, 4),
                currency=currency,
                change_1d_pct=round((last / prev - 1) * 100, 2),
                change_1mo_pct=round((last / first - 1) * 100, 2),
                asof=datetime.now().isoformat(timespec="minutes"),
            )
        except Exception as e:
            print(f"  [warn] quote failed for {ticker}: {e}")
            return None

    def quotes(self, tickers: list[str]) -> dict[str, Quote]:
        result = {}
        for tk in tickers:
            q = self._snapshot(tk)
            if q:
                result[tk] = q
        return result
