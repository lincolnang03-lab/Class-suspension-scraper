"""
Accuracy check for the trial pipeline, month by month.

    python accuracy_check.py coverage 2024-01 2024-02 ...   # what the old data has that the AI missed, and why (free)
    python accuracy_check.py audit 2024-01 2024-02 ...      # a second AI pass (Sonnet) rereads the article behind each claim (paid)
    python accuracy_check.py report 2024-01 2024-02 ...     # one summary + a spot-check sheet to check by hand (free)

Two different questions:
  - Missed  (recall):    of the suspensions in the old Rappler data, how many did the pipeline find?
                         For each one it missed, which step lost it.
  - Correct (precision): of the suspensions the pipeline reports, how many does the article really say?
                         Checked by an independent AI reading of the source, on every record the old data
                         doesn't confirm and on a sample of the ones it does (as a control).

Results are saved as trial/<month>/coverage.json, trial/<month>/audit.jsonl and trial/accuracy_<months>.json.
"""

import concurrent.futures as cf
import csv
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import costs
import reader
import trial_pipeline as tp

ROOT = Path(__file__).parent
CONTROL_SAMPLE = 15   # records the old data also has, audited per month as a control
SPOT_CHECK = 8        # records per month put on the hand-check sheet


# ── Coverage: what the AI missed ─────────────────────────────────────────────

def _near(d: str, days: int) -> list:
    x = date.fromisoformat(d)
    return [(x + timedelta(days=k)).isoformat() for k in range(-days, days + 1) if k]


def coverage(ym: str) -> dict:
    tp.set_month(ym)
    data = json.loads((tp.OUT / "dashboard_data.json").read_text(encoding="utf-8"))
    old = json.loads(tp.RAPPLER_FILE.read_text(encoding="utf-8"))
    catalog = {c["url"] for c in tp.read_jsonl(tp.OUT / "catalog.jsonl")}
    pool = {p["url"] for p in tp.read_jsonl(tp.OUT / "pool.jsonl")}
    scans = {s["url"]: s for s in tp.read_jsonl(tp.OUT / "scan.jsonl")}
    month = {d.isoformat() for d in tp.daterange(tp.MONTH_START, tp.MONTH_END)}

    ai_keys = set()
    for r in data["records"]:
        ai_keys |= tp.match_keys(r["date"], r["province"], r["city"])
    ai_province_days = {(r["date"], tp.place_key(r["province"] or "")) for r in data["records"]}
    ai_cities = {tp.place_key(r["city"] or "") for r in data["records"]}

    def norm_url(u):
        return (u or "").split("?")[0].rstrip("/") + "/"

    catalog_n, pool_n = {norm_url(u) for u in catalog}, {norm_url(u) for u in pool}
    scans_n = {norm_url(u): s for u, s in scans.items()}

    old_total, missed = 0, []
    for d in sorted(month):
        day = old.get(d, {})
        for r in day.get("suspensions", []):
            old_total += 1
            keys = tp.match_keys(d, r.get("province"), r.get("city"))
            if keys & ai_keys:
                continue
            url = norm_url(day.get("url"))
            near_keys = set().union(*(tp.match_keys(n, r.get("province"), r.get("city")) for n in _near(d, 1)))
            if near_keys & ai_keys:
                why = "Found, but on the next or previous day (date worked out differently)"
            elif tp.place_key(r.get("city") or "") in ai_cities:
                why = "Place found on other days this month, not this one"
            elif (d, tp.place_key(r.get("province") or "")) in ai_province_days:
                why = "Same province found that day, but not this town"
            elif url not in catalog_n:
                why = "Rappler's roundup article wasn't in the article list"
            elif url not in pool_n:
                why = "Roundup was listed but the keyword filter skipped it"
            elif "result" not in scans_n.get(url, {}):
                why = "Roundup was picked up but the AI read failed"
            else:
                why = "AI read the roundup but didn't pull out this place"
            missed.append({"date": d, "province": r.get("province"), "city": r.get("city"),
                           "suspension_info": r.get("suspension_info"), "rappler_url": day.get("url"), "why": why})

    by_reason = {}
    for m in missed:
        by_reason[m["why"]] = by_reason.get(m["why"], 0) + 1
    out = {"month": ym, "old_place_days": old_total, "found": old_total - len(missed), "missed": len(missed),
           "recall": round((old_total - len(missed)) / old_total, 3) if old_total else None,
           "missed_by_reason": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])), "missed_list": missed,
           "ai_place_days": len(data["records"]),
           "ai_only": sum(1 for r in data["records"] if not r["in_rappler_data"])}
    (tp.OUT / "coverage.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# ── Audit: does the article really say it? ───────────────────────────────────

AUDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["supported", "partly", "not_supported"]},
        "date_ok": {"type": "boolean"},
        "place_ok": {"type": "boolean"},
        "levels_ok": {"type": "boolean"},
        "status_ok": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "date_ok", "place_ok", "levels_ok", "status_ok", "reason"],
    "additionalProperties": False,
}

AUDIT_SYSTEM = """\
You check one claim about a Philippine class suspension against the news article it came from.
Judge only from the article text and its publish date. Be strict.

- date_ok: the article says classes are affected on this calendar date. Work out "tomorrow",
  "Monday" etc. from the publish date. "Until further notice" covers only dates the article
  itself reaches.
- place_ok: the article names this place (or, for "all cities", the whole province).
- levels_ok: the levels / public-private description matches what the article says for that
  place. If the article says nothing about levels and the claim doesn't either, that is ok.
- status_ok: suspended / lifted / moved online or modular / other matches the article.
- verdict: "supported" if all four hold; "partly" if place and date hold but levels or status
  are off; "not_supported" if the place or the date is wrong, or the article doesn't report
  this suspension at all.
- reason: one sentence, quoting the article where it helps.
"""


def _claim_text(r: dict) -> str:
    place = r["city"] if r["city"] not in (None, "all cities") else f"the whole province ({r['province']})"
    if r["city"] == "all cities":
        place = f"all of {r['province']}"
    return (f"Date: {r['date']}\nPlace: {place}" + (f", {r['province']}" if r["city"] not in (None, "all cities") else "")
            + f"\nLevels as reported: {r['suspension_info']}\nStatus: {r['status']}")


def audit_one(client, rec: dict, item: dict) -> dict:
    content = (f"Outlet: {item.get('outlet')}\nPublished: {item.get('published')}\nTitle: {item.get('title')}\n\n"
               f"<article>\n{(item.get('text') or '')[:60000]}\n</article>\n\n<claim>\n{_claim_text(rec)}\n</claim>")
    # Sonnet: the checker is a judgement call on a whole article, not just reading it
    with client.beta.messages.stream(
        model=reader.STRUCTURE_MODEL, max_tokens=8000,
        betas=[reader.FALLBACK_BETA], fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": AUDIT_SCHEMA}},
        system=AUDIT_SYSTEM, messages=[{"role": "user", "content": content}],
    ) as stream:
        resp = stream.get_final_message()
    usage = [costs.usage_of(resp)]
    if resp.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"audit stopped: {resp.stop_reason}")
    text = next(b.text for b in resp.content if b.type == "text")
    return {"result": json.loads(text), "usage": usage}


def _rec_id(r: dict) -> str:
    return f"{r['date']}|{tp.place_key(r['province'] or '')}|{tp.place_key(r['city'] or '')}"


def audit(ym: str):
    tp.set_month(ym)
    client = reader.make_client()
    data = json.loads((tp.OUT / "dashboard_data.json").read_text(encoding="utf-8"))
    pool = {p["url"]: p for p in tp.read_jsonl(tp.OUT / "pool.jsonl")}
    path = tp.OUT / "audit.jsonl"
    done = {a["id"]: a for a in tp.read_jsonl(path) if "result" in a}

    ai_only = [r for r in data["records"] if not r["in_rappler_data"]]
    overlap = [r for r in data["records"] if r["in_rappler_data"]]
    control = random.Random(ym).sample(overlap, min(CONTROL_SAMPLE, len(overlap)))
    todo = [(r, "ai_only") for r in ai_only] + [(r, "control") for r in control]
    todo = [(r, g) for r, g in todo if _rec_id(r) not in done]
    tp.log(f"{ym}: auditing {len(todo)} records ({len(done)} already done)")

    def work(job):
        rec, group = job
        # Major outlet first, then the rest: the record stands if any one source supports it
        srcs = sorted(rec["sources"], key=lambda s: s["outlet"] not in tp.MAJOR_OUTLETS)
        tried = []
        for src in srcs[:2]:
            try:
                got = audit_one(client, rec, pool.get(src["url"], {}))
            except Exception as e:
                return {"id": _rec_id(rec), "group": group, "error": f"{type(e).__name__}: {e}"}
            tried.append({"url": src["url"], "outlet": src["outlet"], **got})
            if got["result"]["verdict"] == "supported":
                break
        best = min(tried, key=lambda t: ["supported", "partly", "not_supported"].index(t["result"]["verdict"]))
        return {"id": _rec_id(rec), "group": group, "date": rec["date"], "province": rec["province"],
                "city": rec["city"], "suspension_info": rec["suspension_info"], "status": rec["status"],
                "confidence": rec["confidence"], "result": best["result"], "checked_url": best["url"],
                "checked_outlet": best["outlet"],
                "usage": [c for t in tried for c in t["usage"]]}

    out = list(done.values())
    with cf.ThreadPoolExecutor(4) as ex:
        for i, res in enumerate(ex.map(work, todo), 1):
            out.append(res)
            tp.write_jsonl(path, out)
            costs.record(f"accuracy {ym}", res.get("usage") or [])
            if i % 25 == 0 or i == len(todo):
                tp.log(f"  {i}/{len(todo)}")
    errors = [a for a in out if "error" in a]
    if errors:
        tp.log(f"  {len(errors)} failed (re-run to retry): {errors[0]['error']}")
        tp.write_jsonl(path, [a for a in out if "result" in a] + errors)


# ── Report ───────────────────────────────────────────────────────────────────

def report(months: list):
    rows, spot = [], []
    for ym in months:
        tp.set_month(ym)
        cov = json.loads((tp.OUT / "coverage.json").read_text(encoding="utf-8")) \
            if (tp.OUT / "coverage.json").exists() else coverage(ym)
        audits = [a for a in tp.read_jsonl(tp.OUT / "audit.jsonl") if "result" in a]
        cost = sum(costs.total_usd(a.get("usage")) for a in audits)
        dash = json.loads((tp.OUT / "dashboard_data.json").read_text(encoding="utf-8"))

        def tally(group):
            g = [a for a in audits if a["group"] == group]
            v = {k: sum(1 for a in g if a["result"]["verdict"] == k) for k in ("supported", "partly", "not_supported")}
            return {"audited": len(g), **v}

        rows.append({"month": ym, "coverage": {k: cov[k] for k in ("old_place_days", "found", "missed", "recall",
                                                                  "missed_by_reason", "ai_place_days", "ai_only")},
                     "audit_ai_only": tally("ai_only"), "audit_control": tally("control"),
                     "pipeline_cost_usd": dash["summary"]["cost_usd"], "audit_cost_usd": round(cost, 2)})
        recs = dash["records"]
        for r in random.Random("spot" + ym).sample(recs, min(SPOT_CHECK, len(recs))):
            src = sorted(r["sources"], key=lambda s: s["outlet"] not in tp.MAJOR_OUTLETS)[0]
            a = next((a for a in audits if a["id"] == _rec_id(r)), None)
            spot.append({"month": ym, "date": r["date"], "province": r["province"], "city": r["city"],
                         "levels": r["suspension_info"], "status": r["status"], "confidence": r["confidence"],
                         "in_old_data": "yes" if r["in_rappler_data"] else "no",
                         "ai_audit": a["result"]["verdict"] if a else "", "source": src["url"],
                         "quote": src["quote"], "correct? (yes/no/partly)": "", "your note": ""})

    def total(key, field):
        return sum(r[key][field] for r in rows)

    old, found = total("coverage", "old_place_days"), total("coverage", "found")
    summary = {
        "months": months,
        "recall_vs_old_data": round(found / old, 3) if old else None,
        "old_place_days": old, "found": found, "missed": old - found,
        "ai_place_days": total("coverage", "ai_place_days"), "ai_only": total("coverage", "ai_only"),
        "audit_ai_only": {k: total("audit_ai_only", k) for k in ("audited", "supported", "partly", "not_supported")},
        "audit_control": {k: total("audit_control", k) for k in ("audited", "supported", "partly", "not_supported")},
        "pipeline_cost_usd": round(sum(r["pipeline_cost_usd"] for r in rows), 2),
        "audit_cost_usd": round(sum(r["audit_cost_usd"] for r in rows), 2),
    }
    reasons = {}
    for r in rows:
        for k, v in r["coverage"]["missed_by_reason"].items():
            reasons[k] = reasons.get(k, 0) + v
    summary["missed_by_reason"] = dict(sorted(reasons.items(), key=lambda kv: -kv[1]))
    tag = f"{months[0]}_to_{months[-1]}" if len(months) > 1 else months[0]
    (ROOT / "trial" / f"accuracy_{tag}.json").write_text(
        json.dumps({"summary": summary, "months": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(ROOT / "trial" / f"spot_check_{tag}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(spot[0]) if spot else ["month"])
        w.writeheader()
        w.writerows(spot)
    tp.log(json.dumps(summary, ensure_ascii=False, indent=1))
    for r in rows:
        c, a = r["coverage"], r["audit_ai_only"]
        tp.log(f"{r['month']}: old {c['old_place_days']:>4}  found {c['found']:>4}  missed {c['missed']:>3}  "
               f"| AI {c['ai_place_days']:>4} (only AI {c['ai_only']:>3}; audited {a['audited']}: "
               f"{a['supported']} ok / {a['partly']} partly / {a['not_supported']} wrong)")


if __name__ == "__main__":
    cmds = {"coverage": lambda ms: [tp.log(json.dumps({k: v for k, v in coverage(m).items() if k != "missed_list"},
                                                      ensure_ascii=False)) for m in ms],
            "audit": lambda ms: [audit(m) for m in ms],
            "report": report}
    if len(sys.argv) < 3 or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(1)
    cmds[sys.argv[1]](sys.argv[2:])
