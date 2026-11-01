"""
Past class suspensions for the dashboard: every LGU suspension in Rappler's daily #WalangPasok
roundups (class_suspensions_YYYY.json, from rappler_history.py / the notebook), with the cause
of each day taken from the roundup's own opening line.

    python build_history.py        # -> trial_dashboard/history.json   (free: no AI)

The opening lines are cached in history_ledes.json, so re-running only fetches new days.
"""

import html
import json
import re
from datetime import datetime
from pathlib import Path

import reader
import trial_pipeline as tp
from web import get, html_to_text, log

ROOT = Path(__file__).parent
YEARS = [2025, 2026]   # the dashboard covers 2025 onward
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
    out = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "source": "Rappler #WalangPasok daily roundups",
        "covered": covered,
        "levels": tp.LEVELS,
        "fields": ["date", "region", "province", "city", "scope", "levels_mask", "sector", "in_person_only", "part_day"],
        "causes": [c for c, _ in CAUSES] + ["Not stated"],
        "days": days,
        "records": records,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    from collections import Counter
    log(f"{len(records)} place-days on {len(days)} days -> {OUT} ({OUT.stat().st_size / 1e6:.2f} MB)")
    log("Days by cause: " + json.dumps(Counter(v["cause"] for v in days.values()), ensure_ascii=False))


if __name__ == "__main__":
    main()
