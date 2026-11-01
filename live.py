"""
Live checker: is there class today / tomorrow?

    python live.py run      # one check (the daily schedule, or the dashboard's Check now button)
    python live.py places   # rebuild the site's list of every city and town (from PSGC; rarely needed)

One check:
  1. lists what PNA, DepEd and GMA published recently (sources.py),
  2. downloads anything new or edited and applies a free text check,
  3. has Claude read what passes: Haiku reads the article, Sonnet structures it (reader.py),
  4. parses Rappler's #WalangPasok roundups for yesterday, today and tomorrow, without AI,
  5. publishes live/data.json for yesterday to 3 days ahead, and logs API costs to costs/.

Everything it needs to remember between runs lives in live/. Article text is never saved there
(the repo is public): only titles, links, the AI's reads and the one-line quotes behind each entry.
API costs go to costs/, never into data.json, which the site loads.
"""

import concurrent.futures as cf
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import build_history
import costs
import reader
import sources
import trial_pipeline as tp
from web import get, log, read_jsonl, write_jsonl

ROOT = Path(__file__).parent
LIVE = ROOT / "live"
SITE = ROOT / "trial_dashboard"
PH = timezone(timedelta(hours=8))
KEEP_DAYS = 10          # articles and AI reads older than this are dropped from live/
RECENT_DAYS = 2         # only articles published this recently are read
WINDOW_BACK, WINDOW_AHEAD = 1, 3   # dates published in data.json: yesterday .. 3 days ahead
MAX_AI_READS = 60       # per run; anything over waits for the next run (guards against a runaway bill)

# ── The free text check ─────────────────────────────────────────────────────
# Tested on every article the AI read for Jan, Feb, Mar and Jul 2024: it keeps all 55 that were
# about class suspensions and skips about 80% of the rest. Both lists must appear close together.
CLASS_WORDS = re.compile(
    r"\bclass(es)?\b|\bklase\b|\bpasok\b|\bschools?\b|\bpaaralan|eskwela|face[\s-]to[\s-]face|\bf2f\b|kinder|"
    r"elementary|high school|\bcollege|estudyante|students?\b|mag-aaral|learners?\b|\bdeped\b")
OFF_WORDS = re.compile(
    r"uspen|uspin|walang[\s-]*pasok|walay[\s-]*(klase|pasok)|no[\s-]+(classes|school)|kanselad|cancel|lifted|"
    r"\bbinawi|modular|asynchronous|alternative (delivery|learning)|online (class|learning)|blended learning|"
    r"postpone|ipinagpaliban|ipagpapaliban|half[\s-]?day|unable to (start|open|resume)|walang klase|deferred|"
    r"(start|opening|pagbubukas) (of|ng) (classes|klase)|resumption|resume classes|work from home")


def worth_reading(title: str, text: str) -> bool:
    t = f"{title}\n{text}".lower()
    for m in OFF_WORDS.finditer(t):
        if CLASS_WORDS.search(t[max(0, m.start() - 250): m.end() + 250]):
            return True
    return False


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def fetch_all(items: list) -> list:
    with cf.ThreadPoolExecutor(6) as ex:
        return list(ex.map(sources.fetch, items))


# ── Rappler, without AI ─────────────────────────────────────────────────────

def rappler_reads(days: list, now: datetime) -> tuple:
    """Rappler's roundups for these days, shaped like AI reads so they merge the same way."""
    pool, reads = {}, []
    for d in days:
        rr = sources.rappler_roundup(d)
        if not rr["found"] or not rr["suspensions"]:
            continue
        found = []
        for rec in rr["suspensions"]:
            region, province, city, scope = build_history.clean_place(
                rec.get("region") or "", rec.get("province") or "",
                "" if rec.get("city") == "all cities" else rec.get("city") or "")
            info = rec.get("suspension_info") or ""
            where = city or province
            found.append({
                "place_as_written": where, "island": rec.get("island") if rec.get("island") in ("Luzon", "Visayas", "Mindanao") else None,
                "region": region or None, "province": province or None,
                "city": city if scope == "city" else "all cities" if scope == "province" else None,
                "dates": [d.isoformat()], "status": "suspended",
                # A school or barangay list rather than a whole town: keep it as "some schools"
                "levels": info if scope != "partial" else f"some schools: {city}; {info}",
                "evidence_quote": f"{where} – {info}", "check": "quote",
            })
        pool[rr["url"]] = {"outlet": "Rappler", "url": rr["url"], "title": f"#WalangPasok: class suspensions, {d:%B %-d, %Y}",
                           "published": f"{d}T00:00:00",
                           "modified_utc": rr["modified_utc"] or now.strftime("%Y-%m-%dT%H:%M:%S")}
        reads.append({"url": rr["url"], "usage": [], "result": {
            "about_class_suspensions": True, "suspensions": found, "keywords": [],
            "cited_sources": ["Rappler #WalangPasok roundup"]}})
    return pool, reads


# ── One check ───────────────────────────────────────────────────────────────

def cmd_run():
    now = datetime.now(timezone.utc)
    today = now.astimezone(PH).date()
    recent = (today - timedelta(days=RECENT_DAYS)).isoformat()
    articles = {a["url"]: a for a in read_jsonl(LIVE / "articles.jsonl")}
    scans = {s["url"]: s for s in read_jsonl(LIVE / "scans.jsonl")}
    problems = []

    # 1. What the outlets list now
    listed = []
    for outlet in sources.AI_READ:
        got = sources.list_recent(outlet)
        if not got:
            problems.append(f"Couldn't list {outlet}'s articles this run")
        listed += got
    by_outlet = {o: sum(1 for x in listed if x["outlet"] == o) for o in sources.AI_READ}

    # 2. New, or edited since we last looked (when the listing says when it was edited)
    def changed_since(x):
        old = articles.get(x["url"])
        return old is None or (x.get("modified_utc") and x["modified_utc"] != old.get("modified_utc"))
    changed = fetch_all([x for x in listed if changed_since(x) and (not x.get("published") or x["published"][:10] >= recent)])
    for x in changed:
        x["text_hash"] = text_hash(x.get("text") or "")
        x["worth_reading"] = bool(x.get("text")) and worth_reading(x.get("title", ""), x["text"])
        x["first_seen"] = articles.get(x["url"], {}).get("first_seen") or now.isoformat(timespec="seconds")
        articles[x["url"]] = x

    # 3. Claude reads what passed, if its text changed since the last read
    to_read = [a for a in articles.values() if a.get("worth_reading")
               and (a.get("published") or a["first_seen"])[:10] >= recent
               and scans.get(a["url"], {}).get("text_hash") != a.get("text_hash")]
    to_read.sort(key=lambda a: a.get("published") or a["first_seen"], reverse=True)
    if len(to_read) > MAX_AI_READS:
        problems.append(f"{len(to_read) - MAX_AI_READS} articles left for the next run (limit {MAX_AI_READS} per run)")
        to_read = to_read[:MAX_AI_READS]
    # Text isn't kept between runs: an article left unread last time is downloaded again
    fetch_all([a for a in to_read if not a.get("text")])
    for a in to_read:
        a["text_hash"] = text_hash(a.get("text") or "")
    to_read = [a for a in to_read if a.get("text")]
    read_ok, spent = 0, 0.0
    if to_read:
        client = reader.make_client()

        def work(a):
            try:
                return a, reader.read_article(client, a)
            except Exception as e:  # recorded; retried next run
                return a, {"url": a["url"], "error": f"{type(e).__name__}: {e}", "usage": []}

        with cf.ThreadPoolExecutor(4) as ex:
            for a, res in ex.map(work, to_read):
                costs.record("live", res.get("usage") or [])
                spent += costs.total_usd(res.get("usage"))
                if "result" in res:
                    read_ok += 1
                    for sp in res["result"]["suspensions"]:
                        sp["check"] = tp.verify(sp, a["text"])  # is the quote really in the article?
                    scans[a["url"]] = {**res, "text_hash": a["text_hash"], "read_at": now.isoformat(timespec="seconds")}
                else:
                    err = res["error"]
                    msg = "the AI account is out of credit" if "credit balance" in err else err[:200]
                    if f"AI couldn't read some articles: {msg}" not in problems:
                        problems.append(f"AI couldn't read some articles: {msg}")

    # 4. Forget anything older than KEEP_DAYS
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    articles = {u: a for u, a in articles.items() if (a.get("published") or a["first_seen"])[:10] >= cutoff}
    scans = {u: s for u, s in scans.items() if u in articles}

    # 5. Rappler's roundups (no AI), then everything merged into one record per place and date
    rappler_pool, rappler = rappler_reads([today + timedelta(days=k) for k in (-1, 0, 1)], now)
    dates = {(today + timedelta(days=k)).isoformat() for k in range(-WINDOW_BACK, WINDOW_AHEAD + 1)}
    pool = {u: a for u, a in articles.items() if a.get("worth_reading")} | rappler_pool
    records, _, _, _ = tp.build_records(pool, list(scans.values()) + rappler, dates)
    for r in records:
        for s in r["sources"]:
            s.pop("check", None)
    data = {
        "checked_at": now.isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "outlets": [o for o in sources.OUTLETS if o not in sources.BLOCKED],
        "not_read": sources.BLOCKED,
        "records": records,
        "problems": problems,
        "stats": {"listed": len(listed), "new_or_edited": len(changed),
                  "passed_free_check": sum(1 for x in changed if x["worth_reading"]), "ai_reads": read_ok,
                  "rappler_roundups": len(rappler)},
    }
    LIVE.mkdir(exist_ok=True)
    write_jsonl(LIVE / "articles.jsonl", [{k: v for k, v in a.items() if k != "text"}
                                          for a in sorted(articles.values(), key=lambda a: a.get("published") or a["first_seen"])])
    write_jsonl(LIVE / "scans.jsonl", list(scans.values()))
    (LIVE / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (LIVE / "spend.json").unlink(missing_ok=True)  # replaced by costs/
    log(f"{now.astimezone(PH):%Y-%m-%d %H:%M} PH: listed {len(listed)} {by_outlet}, {len(changed)} new/edited, "
        f"{data['stats']['passed_free_check']} passed the free check, {read_ok} read by AI (${spent:.3f}), "
        f"{len(rappler)} Rappler roundups. {len(records)} place-days published."
        + (f"\nProblems: {problems}" if problems else ""))


# ── Places: every city and town, so a parent can pick theirs ───────────────

PSGC = "https://psgc.gitlab.io/api"
REGION_FIX = {"MIMAROPA Region": "MIMAROPA"}
NIR_PROVINCES = {"Negros Occidental", "Negros Oriental", "Siquijor"}   # PSGC predates the 2024 region
NO_PROVINCE = {"City of Isabela": "Basilan", "City of Cotabato": "Maguindanao del Norte"}


# Highly urbanized and independent component cities outside Metro Manila, with their province: a
# governor's province-wide suspension doesn't cover them. (Every Metro Manila city is independent too.)
INDEPENDENT = {
    "baguio": "benguet", "angeles": "pampanga", "olongapo": "zambales", "lucena": "quezon",
    "puerto princesa": "palawan", "iloilo": "iloilo", "bacolod": "negros occidental", "cebu": "cebu",
    "lapu lapu": "cebu", "mandaue": "cebu", "tacloban": "leyte", "ormoc": "leyte", "zamboanga": "zamboanga del sur",
    "cagayan de oro": "misamis oriental", "iligan": "lanao del norte", "davao": "davao del sur",
    "gensan": "south cotabato", "butuan": "agusan del norte", "santiago": "isabela", "dagupan": "pangasinan",
    "naga": "camarines sur", "cotabato": "maguindanao del norte", "isabela": "basilan",
}


def is_independent(name: str, province: str, is_city: bool) -> bool:
    if not is_city:
        return False
    if province == "Metro Manila":
        return True
    return INDEPENDENT.get(tp.place_key(name)) == tp.place_key(province)


def display_name(name: str) -> str:
    if name == "City of Manila":
        return "Manila"
    m = re.fullmatch(r"City of (.+)", name)
    return f"{m.group(1)} City" if m else name


def cmd_places():
    regions = {r["code"]: REGION_FIX.get(r["name"], r["name"]) for r in get(f"{PSGC}/regions.json").json()}
    provinces = {p["code"]: p["name"] for p in get(f"{PSGC}/provinces.json").json()}
    rows = []
    for c in get(f"{PSGC}/cities-municipalities.json").json():
        region = regions[c["regionCode"]]
        if region == "NCR":
            province = "Metro Manila"
        else:
            province = provinces.get(c["provinceCode"]) or NO_PROVINCE.get(c["name"], "")
        if province in NIR_PROVINCES:
            region = "Negros Island Region"
        if c["name"] == "City of Cotabato":
            region = "BARMM"
        name = display_name(c["name"])
        rows.append([region, province, name, 1 if c["isCity"] else 0,
                     1 if is_independent(name, province, c["isCity"]) else 0])
    rows.sort(key=lambda r: (r[1], r[2]))
    out = {"source": "PSGC (psgc.gitlab.io)", "fields": ["region", "province", "name", "is_city", "independent"],
           "places": rows}
    (SITE / "places.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"{len(rows)} cities and towns -> {SITE / 'places.json'} "
           f"({sum(r[4] for r in rows)} marked independent)")


if __name__ == "__main__":
    cmds = {"run": cmd_run, "places": cmd_places}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(1)
    cmds[sys.argv[1]]()
