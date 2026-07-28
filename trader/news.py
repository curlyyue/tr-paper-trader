"""News provider. Protocol + yfinance implementation with Google News RSS fallback."""
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Protocol

import yfinance as yf


class NewsProvider(Protocol):
    def headlines(self, ticker: str, limit: int = 5) -> list[str]: ...
    def market_headlines(self, limit_per_topic: int = 5) -> list[str]: ...


class YFinanceNews:
    # Broad queries for stock discovery beyond the watchlist
    MARKET_TOPICS = (
        "stock market today biggest movers",
        "DAX MDAX Aktien news",
        "earnings beat stock surge",
    )

    def market_headlines(self, limit_per_topic: int = 5) -> list[str]:
        heads = []
        for topic in self.MARKET_TOPICS:
            try:
                heads += self._from_google_rss(topic, limit_per_topic)
            except Exception as e:
                print(f"  [warn] market news failed for {topic!r}: {e}")
        return heads

    def headlines(self, ticker: str, limit: int = 5) -> list[str]:
        try:
            heads = self._from_yfinance(ticker, limit)
            if heads:
                return heads
        except Exception:
            pass
        try:
            return self._from_google_rss(f"{ticker} stock", limit)
        except Exception as e:
            print(f"  [warn] news failed for {ticker}: {e}")
            return []

    @staticmethod
    def _from_yfinance(ticker: str, limit: int) -> list[str]:
        items = yf.Ticker(ticker).news or []
        heads = []
        for it in items[:limit]:
            # yfinance >= 0.2.5x nests fields under "content"; older versions are flat
            content = it.get("content", it)
            title = content.get("title")
            pub = content.get("pubDate") or content.get("providerPublishTime") or ""
            if title:
                heads.append(f"{title} ({pub})")
        return heads

    @staticmethod
    def _from_google_rss(query: str, limit: int) -> list[str]:
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
            {"q": query, "hl": "en", "gl": "US", "ceid": "US:en"}
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            root = ET.fromstring(resp.read())
        heads = []
        for item in root.iter("item"):
            title = item.findtext("title")
            pub = item.findtext("pubDate") or ""
            if title:
                heads.append(f"{title} ({pub})")
            if len(heads) >= limit:
                break
        return heads
