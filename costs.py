"""
API cost log. Every Claude call's tokens and price are appended to costs/api_costs.jsonl, and
costs/summary.json keeps running totals by month, model and job. Never shown on the site.

    python costs.py          # print the totals
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
DIR = ROOT / "costs"
LOG = DIR / "api_costs.jsonl"
SUMMARY = DIR / "summary.json"

# USD per million tokens (input, output). Haiku 5.5's price is for prompts of 100K tokens or fewer;
# articles are cut at 60,000 characters, far below that.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}


def usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICES.get(model, PRICES["claude-opus-5-5"])  # unknown model: price it high, not low
    return input_tokens * pin / 1e6 + output_tokens * pout / 1e6


def usage_of(resp) -> dict:
    """One call's usage, with the model that actually answered (a refusal fallback can switch it)."""
    u = resp.usage
    inp = (u.input_tokens or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0) \
        + (getattr(u, "cache_read_input_tokens", 0) or 0)
    return {"model": resp.model, "input_tokens": inp, "output_tokens": u.output_tokens or 0,
            "usd": round(usd(resp.model, inp, u.output_tokens or 0), 6)}


def total_usd(usage) -> float:
    """A read's usage: a list of calls (current), or one {input_tokens, output_tokens} dict (older Opus-only reads)."""
    if not usage:
        return 0.0
    if isinstance(usage, dict):
        return usd("claude-opus-5-5", usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    return sum(c.get("usd", usd(c["model"], c["input_tokens"], c["output_tokens"])) for c in usage)


def record(job: str, calls: list):
    """Append calls (each from usage_of) under a job name, and update the totals."""
    calls = [c for c in calls if c]
    if not calls:
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    DIR.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        for c in calls:
            f.write(json.dumps({"time": now, "job": job, **c}) + "\n")
    s = json.loads(SUMMARY.read_text(encoding="utf-8")) if SUMMARY.exists() else {"total_usd": 0, "months": {}}
    month = s["months"].setdefault(now[:7], {"usd": 0, "calls": 0, "by_model": {}, "by_job": {}})
    for c in calls:
        s["total_usd"] = round(s["total_usd"] + c["usd"], 6)
        month["usd"] = round(month["usd"] + c["usd"], 6)
        month["calls"] += 1
        m = month["by_model"].setdefault(c["model"], {"usd": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0})
        m["usd"] = round(m["usd"] + c["usd"], 6)
        m["calls"] += 1
        m["input_tokens"] += c["input_tokens"]
        m["output_tokens"] += c["output_tokens"]
        kind = job.split()[0]
        month["by_job"][kind] = round(month["by_job"].get(kind, 0) + c["usd"], 6)
    s["updated"] = now
    SUMMARY.write_text(json.dumps(s, indent=1), encoding="utf-8")


if __name__ == "__main__":
    if not SUMMARY.exists():
        sys.exit("No API calls recorded yet.")
    s = json.loads(SUMMARY.read_text(encoding="utf-8"))
    print(f"All time: ${s['total_usd']:.2f}")
    for month, m in sorted(s["months"].items()):
        models = ", ".join(f"{k}: ${v['usd']:.2f} ({v['calls']} calls)" for k, v in m["by_model"].items())
        print(f"{month}: ${m['usd']:.2f}  [{models}]  by job: {m['by_job']}")
