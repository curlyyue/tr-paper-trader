"""Translation step — deliberately separate from the decision engine.

Takes a decision's free-text fields (analysis / reasoning / memo / order &
watchlist reasons) and produces the same structure in another language via ONE
extra LLM call with a dedicated translation prompt. It does NOT create a
`decisions` row, so it never counts against the daily LLM wake-up cap.
"""
import copy
import json
import re

LANG_NAMES = {"en": "English", "zh": "Simplified Chinese"}

TRANSLATE_PROMPT = """\
You are a professional financial translator. Translate every VALUE in the JSON
object below into {lang}. Rules:
- Keep every KEY exactly as-is.
- Keep stock tickers (e.g. SAP.DE, 000660.KS), numbers, percentages, currency
  symbols and dates unchanged.
- Preserve the financial meaning precisely; natural, fluent {lang}.
Respond with ONLY the translated JSON object, no markdown fences.

{payload}
"""


def _text_map(d: dict) -> dict[str, str]:
    """Pull translatable strings out of a decision dict, keyed by stable ids."""
    m = {}
    for k in ("analysis", "reasoning", "memo"):
        if d.get(k):
            m[k] = d[k]
    for i, o in enumerate(d.get("orders") or []):
        if o.get("reason"):
            m[f"order_{i}"] = o["reason"]
    for i, a in enumerate((d.get("watchlist_changes") or {}).get("add") or []):
        if a.get("reason"):
            m[f"wl_{i}"] = a["reason"]
    return m


def _apply_map(d: dict, m: dict) -> dict:
    """Splice translated strings back into a copy of the decision dict."""
    out = copy.deepcopy(d)
    for k in ("analysis", "reasoning", "memo"):
        if k in m:
            out[k] = m[k]
    for i, o in enumerate(out.get("orders") or []):
        if f"order_{i}" in m:
            o["reason"] = m[f"order_{i}"]
    for i, a in enumerate((out.get("watchlist_changes") or {}).get("add") or []):
        if f"wl_{i}" in m:
            a["reason"] = m[f"wl_{i}"]
    return out


def translate_decision(engine, decision: dict, target_lang: str) -> dict:
    """Return a copy of `decision` with all human text translated to target_lang.
    Raises on failure — callers should treat it as best-effort."""
    texts = _text_map(decision)
    if not texts:
        return decision
    prompt = TRANSLATE_PROMPT.format(
        lang=LANG_NAMES[target_lang],
        payload=json.dumps(texts, ensure_ascii=False),
    )
    raw = engine.complete(prompt)
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON in translation output: {raw[:200]}")
    translated = json.loads(match.group(0))
    return _apply_map(decision, translated)
