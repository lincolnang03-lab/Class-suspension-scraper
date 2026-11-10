"""
Past class suspensions for the dashboard: every LGU suspension in Rappler's daily #WalangPasok
roundups (class_suspensions_YYYY.json, from rappler_history.py / the notebook), with the cause
of each day taken from the roundup's own opening line.

    python build_history.py            # -> trial_dashboard/history.json   (free: no AI)
    python build_history.py catch-up   # first add the days since the last saved roundup (every check runs this)

The opening lines are cached in history_ledes.json, so re-running only fetches new days.
"""

import html
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import reader
import school_calendar
import trial_pipeline as tp
from web import get, html_to_text, log

ROOT = Path(__file__).parent
YEARS = list(range(2025, datetime.now().year + 1))   # the dashboard covers 2025 onward
RECHECK_DAYS = 3        # a roundup can appear or grow after the day was first saved
LEDE_CACHE = ROOT / "history_ledes.json"
OUT = ROOT / "trial_dashboard" / "history.json"

# In priority order: the first that matches a day's opening line is its main cause
CAUSES = [
    ("Typhoon / tropical storm", r"\b(super typhoon|typhoon|severe tropical storm|tropical storm|tropical depression)\b"),
    ("Extreme heat", r"\bheat\b|high temperature|hot weather|init ng panahon"),
    ("Earthquake", r"earthquake|lindol|tremor"),
    ("Volcanic activity", r"volcan|kanlaon|taal|mayon|ashfall|ash fall|phreatic"),
    ("Transport strike", r"transport strike|jeepney strike|tigil[- ]pasada"),
    ("Low pressure area / shear line", r"low pressure area|\blpa\b|shear line|shearline|trough"),
    ("Monsoon rains", r"monsoon|habagat|amihan"),
    ("Rain and flooding", r"\brain|flood|baha|inclement weather|bad weather|weather disturbance"),
]
STORM = re.compile(r"(?:Super Typhoon|Typhoon|Severe Tropical Storm|Tropical Storm|Tropical Depression)\s+([A-Z][a-z]+)(?:\s*\(([A-Z][a-z-]+)\))?")


def lede_of(url: str) -> dict:
    """The roundup's title and opening paragraph, from Rappler's public posts API."""
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    r = get("https://www.rappler.com/wp-json/wp/v2/posts", params={"slug": slug, "_fields": "title,content"})
    if r is None or r.status_code != 200 or not r.json():
        return {"title": "", "lede": ""}
    p = r.json()[0]
    text = html_to_text(p["content"]["rendered"])
    first = next((line for line in text.splitlines() if len(line) > 60), "")
    first = re.sub(r"^MANILA, Philippines\s*[–—-]\s*", "", first)
    first = re.split(r"(?<=\.)\s+This list will be updated", first)[0]
    return {"title": html.unescape(re.sub(r"<[^>]+>", "", p["title"]["rendered"])), "lede": first.strip()}


# ── Cleaning places ─────────────────────────────────────────────────────────
# The roundups are hand-written lists, so a few entries put a city, a date or a school where the
# province should be. PSGC (trial_dashboard/places.json) supplies provinces, cities and regions.
_P = json.loads((ROOT / "trial_dashboard" / "places.json").read_text(encoding="utf-8"))["places"]
PROVINCE_REGION = {tp.place_key(p[1]): p[0] for p in _P}
CITY_PROVINCE = {tp.place_key(p[2]): p[1] for p in _P if p[3]}
REGIONS = set(reader.REGIONS)
PROVINCE_FIX = {"mountain": "Mountain Province"}
# A "town" that is really part of one: schools, universities, barangays, lists, instructions
PARTIAL = re.compile(r"school|university|universidad|college|colegio|pamantasan|academy|institute|barangay|"
                     r"islands|municipalities|malaca|elementary|\bhigh\b|,|^for ", re.I)


def clean_place(region: str, province: str, city: str) -> tuple:
    """-> (region, province, city, scope) with scope 'province' (whole province), 'city' or 'partial'."""
    province = re.sub(r"\s*\(.*?\)\s*$", "", province or "").strip()
    city = re.split(r"\s+[–—-]\s+", (city or "").strip())[0]   # "Bay – all levels" -> "Bay"
    pk = tp.place_key(province)
    # "Quezon City" / "Isabela City" are cities, not Quezon / Isabela province (place_key drops "City")
    if re.search(r"\bcity\b", province, re.I) and pk in CITY_PROVINCE and \
            tp.place_key(CITY_PROVINCE[pk]) != pk or (re.search(r"\bcity\b", province, re.I) and pk in CITY_PROVINCE
                                                      and CITY_PROVINCE[pk] == "Metro Manila"):
        city, province = (province, CITY_PROVINCE[pk]) if not city else (f"{province}: {city}", CITY_PROVINCE[pk])
        pk = tp.place_key(province)
    if pk in PROVINCE_FIX:
        province = PROVINCE_FIX[pk]
    elif pk not in PROVINCE_REGION:
        m = re.fullmatch(r"(.+?),\s*(.+)", province)
        if m and tp.place_key(m.group(2)) in PROVINCE_REGION:          # "Lingayen, Pangasinan"
            if not city:
                city = m.group(1)
            province = m.group(2)
        elif pk in CITY_PROVINCE:                                     # "Malolos City" given as the province
            city, province = (province, CITY_PROVINCE[pk]) if not city else (f"{province}: {city}", CITY_PROVINCE[pk])
        elif PARTIAL.search(province):                                # a university listed on its own
            city, province = province, ""
    region = PROVINCE_REGION.get(tp.place_key(province)) or (region if region in REGIONS else "")
    if not city:
        scope = "province" if province else "partial"
    else:
        scope = "partial" if PARTIAL.search(city) or len(city) > 45 else "city"
    return region, province, city, scope


def cause_of(lede: str) -> tuple:
    low = lede.lower()
    cause = next((name for name, pat in CAUSES if re.search(pat, low)), "Not stated")
    storms = [f"{m.group(1)}" + (f" ({m.group(2)})" if m.group(2) else "") for m in STORM.finditer(lede)]
    return cause, list(dict.fromkeys(storms))


def main():
    ledes = json.loads(LEDE_CACHE.read_text(encoding="utf-8")) if LEDE_CACHE.exists() else {}
    days, records, seen = {}, [], set()
    covered = []
    for y in YEARS:
        path = ROOT / f"class_suspensions_{y}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        covered.append([min(data), max(data)])
        for d, v in sorted(data.items()):
            sus = v.get("suspensions") or []
            if not sus:
                continue
            if d not in ledes:
                ledes[d] = lede_of(v["url"])
                log(f"{d}: {ledes[d]['lede'][:110]}")
            cause, storms = cause_of(f"{ledes[d]['title']} {ledes[d]['lede']}")
            days[d] = {"cause": cause, "storms": storms, "reason": ledes[d]["lede"], "url": v["url"]}
            for r in sus:
                region, province, city, scope = clean_place(r.get("region") or "", r.get("province") or "",
                                                            "" if r.get("city") == "all cities" else r.get("city") or "")
                key = (d, tp.place_key(province), tp.place_key(city))
                if key in seen:
                    continue
                seen.add(key)
                lv = tp.normalize_levels(r.get("suspension_info") or "", "suspended")
                mask = -1 if lv["levels"] is None else sum(1 << i for i, k in enumerate(tp.LEVELS) if lv["levels"][k])
                records.append([
                    d, region, province, city, scope,
                    mask, {"Public & private": "both", "Public only": "public", "Private only": "private"}.get(lv["sector"], ""),
                    1 if "In-person only" in lv["notes"] else 0,
                    1 if any(n.startswith("From") for n in lv["notes"]) else 0,
                ])
    LEDE_CACHE.write_text(json.dumps(ledes, ensure_ascii=False, indent=1), encoding="utf-8")
    records = merge_backfill(records, days)
    out = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "source": "Rappler #WalangPasok daily roundups",
        "covered": covered,
        "levels": tp.LEVELS,
        # Days the backfill searched add: outlets, confidence (1-5), why, and a source link
        "fields": ["date", "region", "province", "city", "scope", "levels_mask", "sector", "in_person_only", "part_day",
                   "outlets", "confidence", "confidence_reason", "url"],
        "causes": [c for c, _ in CAUSES] + ["Not stated"],
        "days": days,
        "records": records,
        "calendar": school_calendar.as_json(),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    from collections import Counter
    log(f"{len(records)} place-days on {len(days)} days -> {OUT} ({OUT.stat().st_size / 1e6:.2f} MB)")
    log("Days by cause: " + json.dumps(Counter(v["cause"] for v in days.values()), ensure_ascii=False))


SECTORS = {"Public & private": "both", "Public only": "public", "Private only": "private"}
ACTIVE = {"suspended", "online_or_modular", "opening_postponed"}


def merge_backfill(records: list, days: dict) -> list:
    """On days the backfill searched (backfill.py), Rappler's rows give way to Rappler and the news
    outlets merged: the places Rappler missed, and more outlets (so more confidence) for the ones it had."""
    import backfill
    merged = backfill.merged_days()
    keep = [r for r in records if r[0] not in merged]
    seen = set()
    for d, recs in merged.items():
        recs = [r for r in recs if r["status"] in ACTIVE]
        if not recs:
            continue
        if d not in days:   # found only by the news (backfill's weekly pass): its cause from the articles
            news = [s for r in recs for s in r["sources"]]
            text = " ".join(dict.fromkeys(f"{s['title'] or ''} {s['quote'] or ''}" for s in news))
            # The articles' passages and keywords too; the cause they name most often wins
            text += " " + " ".join(backfill.article_words(s["url"]) for s in news)
            counts = [(len(re.findall(pat, text.lower())), name) for name, pat in CAUSES]
            cause = max(counts)[1] if max(counts)[0] else "Not stated"
            storms = cause_of(text)[1]
            days[d] = {"cause": cause, "storms": storms, "reason": news[0]["title"] or "", "url": news[0]["url"],
                       "found_by": "news"}
        for rec in recs:
            if rec["scope"] in ("region", "national"):
                region, province, city, scope = rec["region"] or "", "", "", rec["scope"]
            else:
                region, province, city, scope = clean_place(rec["region"] or "", rec["province"] or "",
                                                            "" if rec["city"] in (None, "all cities") else rec["city"])
                if rec["scope"] == "schools":
                    scope = "partial"
            key = (d, scope, tp.place_key(region if scope == "region" else province), tp.place_key(city))
            if key in seen:
                continue
            seen.add(key)
            lv = rec["levels"]
            mask = -1 if lv is None else sum(1 << i for i, k in enumerate(tp.LEVELS) if lv.get(k))
            outlets = sorted({s["outlet"] for s in rec["sources"]})
            url = next((s["url"] for s in rec["sources"] if s["outlet"] != "Rappler"), rec["sources"][0]["url"])
            keep.append([d, region, province, city, scope, mask, SECTORS.get(rec["sector"], ""),
                         1 if "In-person only" in rec["notes"] else 0,
                         1 if any(n.startswith("From") for n in rec["notes"]) else 0,
                         outlets, rec["confidence"], rec["confidence_reason"], url])
    return sorted(keep, key=lambda r: r[0])


def catch_up():
    """Scrape Rappler's roundups for every day missing since the last one saved, through yesterday
    (Philippine time), plus the last few saved days again. Missed checks fill themselves in this way."""
    import rappler_history
    yesterday = datetime.now(timezone(timedelta(hours=8))).date() - timedelta(days=1)
    saved = {}
    for y in range(2025, yesterday.year + 1):
        path = ROOT / f"class_suspensions_{y}.json"
        saved[y] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    last = max((d for days in saved.values() for d in days), default="2024-12-31")
    start = min(date.fromisoformat(last) + timedelta(days=1), yesterday - timedelta(days=RECHECK_DAYS - 1))
    if start > yesterday:
        return
    got = rappler_history.scrape_range(start, yesterday)
    for d, v in got.items():
        old = saved[int(d[:4])].get(d)
        if old and old["has_suspensions"] and not v["has_suspensions"]:
            continue    # a failed fetch reads as "no roundup": never let it erase a saved list
        saved[int(d[:4])][d] = v
    for y, days in saved.items():
        if days:
            (ROOT / f"class_suspensions_{y}.json").write_text(
                json.dumps(dict(sorted(days.items())), ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Rappler roundups {start} to {yesterday}: {sum(v['has_suspensions'] for v in got.values())} of {len(got)} days had suspensions")


if __name__ == "__main__":
    if sys.argv[1:] == ["catch-up"]:
        catch_up()
    main()
