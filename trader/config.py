"""Settings: .env (model/agent/backend) + config.json (budget, watchlist, holidays)."""
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

# CLI aliases -> Anthropic API model ids (used only by the "api" backend)
MODEL_ALIASES = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5",
}


@dataclass(frozen=True)
class Settings:
    agent_name: str
    model: str            # alias ("opus") or full id ("claude-opus-4-8")
    backend: str          # "cli" | "api"
    budget_period: str    # "daily" | "monthly"
    daily_budget: float   # used when budget_period == "daily"
    initial_cash: float   # one-time credit, used when budget_period == "monthly"
    monthly_budget: float          # recurring credit, used when budget_period == "monthly"
    monthly_start: str | None      # "YYYY-MM-DD"; recurring credit starts this month
    fee: float
    watchlist: tuple[str, ...]
    holidays: frozenset[str]

    @property
    def db_path(self) -> Path:
        return DATA_DIR / f"{self.agent_name}.db"

    @property
    def api_model(self) -> str:
        return MODEL_ALIASES.get(self.model, self.model)


def load_settings(model: str | None = None, agent: str | None = None,
                  backend: str | None = None) -> Settings:
    """CLI flags override .env, which overrides defaults."""
    load_dotenv(ROOT / ".env")
    cfg = json.loads((ROOT / "config.json").read_text())

    model = model or os.getenv("TRADER_MODEL", "opus")
    agent = agent or os.getenv("TRADER_AGENT") or re.sub(r"[^A-Za-z0-9_.-]", "_", model)
    backend = (backend or os.getenv("TRADER_BACKEND", "cli")).lower()
    if backend not in ("cli", "api"):
        raise ValueError(f"TRADER_BACKEND must be 'cli' or 'api', got {backend!r}")
    if backend == "api" and not os.getenv("ANTHROPIC_API_KEY"):
        raise ValueError("TRADER_BACKEND=api requires ANTHROPIC_API_KEY in .env")

    agent_cfg = cfg.get("agents", {}).get(agent, {})

    return Settings(
        agent_name=agent,
        model=model,
        backend=backend,
        budget_period=agent_cfg.get("budget_period", "daily"),
        daily_budget=float(cfg["daily_budget_eur"]),
        initial_cash=float(agent_cfg.get("initial_cash_eur", 0)),
        monthly_budget=float(agent_cfg.get("monthly_budget_eur", 0)),
        monthly_start=agent_cfg.get("monthly_start"),
        fee=float(cfg["fee_per_trade_eur"]),
        watchlist=tuple(cfg["watchlist"]),
        holidays=frozenset(cfg["market_holidays"]),
    )
