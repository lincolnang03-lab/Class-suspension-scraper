"""
API cost log. Every Claude call's tokens and price are appended to costs/api_costs.jsonl, and
costs/summary.json keeps running totals by month, model and job. The dashboard's Cost tracker shows
the totals by day (trial_dashboard/costs.json, written by `site`; never the log itself).

    python costs.py            # print the totals
    python costs.py site       # -> trial_dashboard/costs.json, for the dashboard
    python costs.py backfill   # once: add the 2024 trial runs, which ran before this log existed
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
DIR = ROOT / "costs"
LOG = DIR / "api_costs.jsonl"
SUMMARY = DIR / "summary.json"
SITE = ROOT / "trial_dashboard" / "costs.json"
MANILA = timezone(timedelta(hours=8))

# USD per million tokens (input, output). Haiku 5.5's price is for prompts of 100K tokens or fewer;
# articles are cut at 60,000 characters, far below that.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}


SEARCH_USD = 0.01   # per web search


def usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICES.get(model, PRICES["claude-opus-5-5"])  # unknown model: price it high, not low
    return input_tokens * pin / 1e6 + output_tokens * pout / 1e6


def usage_of(resp, batch: bool = False) -> dict:
    """One call's usage, with the model that actually answered (a refusal fallback can switch it).
    Web searches are $0.01 each; the Batches API halves token prices."""
    u = resp.usage
    inp = (u.input_tokens or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0) \
        + (getattr(u, "cache_read_input_tokens", 0) or 0)
    stu = getattr(u, "server_tool_use", None)
    searches = (getattr(stu, "web_search_requests", 0) or 0) if stu else 0
    tokens = usd(resp.model, inp, u.output_tokens or 0) * (0.5 if batch else 1)
    return {"model": resp.model, "input_tokens": inp, "output_tokens": u.output_tokens or 0,
            **({"web_searches": searches} if searches else {}), **({"batch": True} if batch else {}),
            "usd": round(tokens + searches * SEARCH_USD, 6)}


def total_usd(usage) -> float:
    """A read's usage: a list of calls (current), or one {input_tokens, output_tokens} dict (older Opus-only reads)."""
    if not usage:
        return 0.0
    if isinstance(usage, dict):
        return usd("claude-opus-5-5", usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    return sum(c.get("usd", usd(c["model"], c["input_tokens"], c["output_tokens"])) for c in usage)


def record(job: str, calls: list, when: datetime = None):
    """Append calls (each from usage_of) under a job name, and update the totals."""
    calls = [c for c in calls if c]
    if not calls:
        return
    now = (when or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")
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


def write_site():
    """Totals by day (Philippine time) and all time, for the dashboard's Cost tracker."""
    days = {}
    if LOG.exists():
        for line in LOG.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            c = json.loads(line)
            d = datetime.fromisoformat(c["time"]).astimezone(MANILA).date().isoformat()
            day = days.setdefault(d, {"date": d, "usd": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0,
                                      "by_model": {}, "by_job": {}, "estimated": False})
            day["usd"] += c["usd"]
            day["calls"] += 1
            day["input_tokens"] += c["input_tokens"]
            day["output_tokens"] += c["output_tokens"]
            day["by_model"][c["model"]] = day["by_model"].get(c["model"], 0) + c["usd"]
            day["by_job"][c["job"]] = day["by_job"].get(c["job"], 0) + c["usd"]
            day["estimated"] = day["estimated"] or bool(c.get("estimated"))
    for day in days.values():
        day["usd"] = round(day["usd"], 4)
        day["by_model"] = {k: round(v, 4) for k, v in day["by_model"].items()}
        day["by_job"] = {k: round(v, 4) for k, v in day["by_job"].items()}
    out = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_usd": round(sum(d["usd"] for d in days.values()), 4),
        "calls": sum(d["calls"] for d in days.values()),
        "prices": {m: {"input": p[0], "output": p[1]} for m, p in PRICES.items()},
        "days": sorted(days.values(), key=lambda d: d["date"], reverse=True),
    }
    SITE.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"{len(days)} days, ${out['total_usd']:.2f} all time -> {SITE}")


def backfill():
    """The 2024 trial runs read articles with Opus before this log existed. Their token counts were saved
    with each read (trial/YYYY-MM/scan.jsonl), so their cost is rebuilt from those, dated by the file."""
    if LOG.exists() and any(json.loads(l).get("estimated") for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()):
        sys.exit("Already added.")
    for path in sorted((ROOT / "trial").glob("*/scan.jsonl")):
        calls = []
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            u = json.loads(line).get("usage") if line.strip() else None
            if isinstance(u, dict):     # older Opus-only reads: one {input_tokens, output_tokens}
                calls.append({"model": "claude-opus-5-5", "input_tokens": u.get("input_tokens", 0),
                              "output_tokens": u.get("output_tokens", 0), "usd": round(total_usd(u), 6), "estimated": True})
            elif isinstance(u, list):
                calls += [{**c, "estimated": True} for c in u]
        if calls:
            record(f"trial {path.parent.name}", calls, when=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))
            print(f"{path.parent.name}: {len(calls)} calls, ${sum(c['usd'] for c in calls):.2f}")


if __name__ == "__main__":
    if sys.argv[1:] == ["site"]:
        sys.exit(write_site())
    if sys.argv[1:] == ["backfill"]:
        sys.exit(backfill())
    if not SUMMARY.exists():
        sys.exit("No API calls recorded yet.")
    s = json.loads(SUMMARY.read_text(encoding="utf-8"))
    print(f"All time: ${s['total_usd']:.2f}")
    for month, m in sorted(s["months"].items()):
        models = ", ".join(f"{k}: ${v['usd']:.2f} ({v['calls']} calls)" for k, v in m["by_model"].items())
        print(f"{month}: ${m['usd']:.2f}  [{models}]  by job: {m['by_job']}")
