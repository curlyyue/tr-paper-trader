"""Decision engines (Strategy pattern).

ClaudeCLIEngine   -> `claude -p`, runs under your Pro/Max subscription login.
AnthropicAPIEngine -> Anthropic API, needs ANTHROPIC_API_KEY.
Both return a validated Decision; the factory picks one from Settings.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from .config import Settings
from .models import Decision, Order

# Models that support adaptive thinking on the API path
_ADAPTIVE_PREFIXES = ("claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8",
                      "claude-sonnet-4-6", "claude-sonnet-5", "claude-fable-5")


class DecisionEngine(Protocol):
    name: str
    def complete(self, prompt: str) -> str: ...   # one raw LLM call → text
    def decide(self, prompt: str) -> Decision: ...


def _parse(raw: str, model_used: str) -> Decision:
    match = re.search(r"\{.*\}", raw, re.DOTALL)  # tolerate fences/prose around JSON
    if not match:
        raise ValueError(f"no JSON object in model output: {raw[:300]}")
    data = json.loads(match.group(0))
    orders = tuple(Order.from_dict(o) for o in data.get("orders", []))
    wl = data.get("watchlist_changes") or {}
    adds = tuple(
        (str(a.get("ticker", "")).strip(), str(a.get("reason", "")))
        for a in wl.get("add", []) if a.get("ticker")
    )
    removes = tuple(str(t).strip() for t in wl.get("remove", []) if t)
    return Decision(
        analysis=str(data.get("analysis", "")),
        orders=orders,
        raw_json=json.dumps(data, ensure_ascii=False),
        model_used=model_used,
        watchlist_add=adds,
        watchlist_remove=removes,
    )


def find_claude_binary() -> str:
    """Locate the claude CLI. The desktop app bundles it outside PATH."""
    # 1. Explicit override from .env
    if os.getenv("CLAUDE_BIN"):
        return os.getenv("CLAUDE_BIN")
    # 2. On PATH (standalone CLI install)
    if found := shutil.which("claude"):
        return found
    # 3. Common standalone install locations
    home = Path.home()
    for p in (home / ".claude/local/claude", home / ".local/bin/claude",
              Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")):
        if p.is_file():
            return str(p)
    # 4. Bundled inside the Claude desktop app (pick the newest version)
    bundled = sorted(
        (home / "Library/Application Support/Claude/claude-code").glob(
            "*/claude.app/Contents/MacOS/claude"
        ),
        key=lambda p: p.parent.parent.parent.parent.name,
    )
    if bundled:
        return str(bundled[-1])
    raise RuntimeError(
        "claude CLI not found. Either install it (npm install -g "
        "@anthropic-ai/claude-code), or set CLAUDE_BIN=<path> in .env, "
        "or switch to TRADER_BACKEND=api with an ANTHROPIC_API_KEY."
    )


class ClaudeCLIEngine:
    def __init__(self, model: str):
        self.model = model
        self.binary = find_claude_binary()
        self.name = f"{model} (claude CLI / subscription)"

    def complete(self, prompt: str) -> str:
        result = subprocess.run(
            [self.binary, "-p", "--output-format", "json", "--model", self.model],
            input=prompt, capture_output=True, text=True, timeout=600,
        )
        payload = None
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            pass
        if payload is not None and payload.get("is_error"):
            msg = payload.get("result", "unknown error")
            if "Not logged in" in str(msg):
                msg += (" — the claude CLI has its own login. Run `claude` in a "
                        "terminal once and sign in with your Pro/Max account.")
            raise RuntimeError(f"claude CLI error: {msg}")
        if result.returncode != 0 or payload is None:
            raise RuntimeError(
                f"claude CLI failed (exit {result.returncode}): "
                f"{result.stderr[:300] or result.stdout[:300]}"
            )
        return payload["result"]

    def decide(self, prompt: str) -> Decision:
        return _parse(self.complete(prompt), self.name)


class AnthropicAPIEngine:
    def __init__(self, model: str):
        self.model = model
        self.name = f"{model} (API)"

    def complete(self, prompt: str) -> str:
        import anthropic

        client = anthropic.Anthropic()
        kwargs = {}
        if self.model.startswith(_ADAPTIVE_PREFIXES):
            kwargs["thinking"] = {"type": "adaptive"}
        response = client.messages.create(
            model=self.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
        return next(b.text for b in response.content if b.type == "text")

    def decide(self, prompt: str) -> Decision:
        return _parse(self.complete(prompt), self.name)


def create_engine(settings: Settings) -> DecisionEngine:
    if settings.backend == "api":
        return AnthropicAPIEngine(settings.api_model)
    return ClaudeCLIEngine(settings.model)
