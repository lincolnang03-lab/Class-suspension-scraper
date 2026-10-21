"""
Live checker: is there class today / tomorrow?

    python live.py run      # one check (what the schedule runs): new articles -> free text filter -> AI -> live/data.json
    python live.py places   # rebuild the site's list of every city and town (from PSGC; rarely needed)

Each run only looks at articles published yesterday or today, and only pays for the AI to read
an article that is new or has changed since the last run AND passes a free text check (the body
must talk about classes being suspended, cancelled, moved online, ...). Everything it needs to
remember between runs lives in live/, which the scheduled GitHub Action commits back. Article
text is never saved there (the repo is public): only titles, links, the AI's reads and the
one-line quotes that back each entry.
"""

import concurrent.futures as cf
import hashlib
import html
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import trial_pipeline as tp

ROOT = Path(__file__).parent
LIVE = ROOT / "live"
SITE = ROOT / "trial_dashboard"
PH = timezone(timedelta(hours=8))
KEEP_DAYS = 10          # articles and AI reads older than this are dropped from live/
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


# ── Listing what each outlet published ──────────────────────────────────────

def list_wordpress(outlet: str, base: str, d: date) -> list:
    rows, page = [], 1
    while True:
        r = tp.get(f"{base}/wp-json/wp/v2/posts", params={
            "after": f"{d}T00:00:00", "before": f"{d + timedelta(days=1)}T00:00:00",
            "per_page": 100, "page": page, "_fields": "id,date,modified_gmt,link,title"})
        if r is None or r.status_code != 200:
            break
        for p in r.json():
            rows.append({"outlet": outlet, "id": p["id"], "url": p["link"], "published": p["date"],
                         "modified_utc": p.get("modified_gmt"),
                         "title": html.unescape(re.sub(r"<[^>]+>", "", p["title"]["rendered"]))})
        if page >= int(r.headers.get("X-WP-TotalPages", 1)):
            break
        page += 1
    return rows


def list_sunstar(d: date) -> list:
    rows = tp.catalog_sunstar(d)
    for r in rows:
        r["modified_utc"] = r["published"]  # the daily sitemap's lastmod is the last change
    return rows


LISTERS = {
    "Rappler": lambda d: list_wordpress("Rappler", "https://www.rappler.com", d),
    "Bombo Radyo": lambda d: list_wordpress("Bombo Radyo", "https://www.bomboradyo.com", d),
    "SunStar": list_sunstar,
}


# ── Run ─────────────────────────────────────────────────────────────────────

def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def cmd_run():
    now = datetime.now(timezone.utc)
    today = now.astimezone(PH).date()
    articles = {a["url"]: a for a in tp.read_jsonl(LIVE / "articles.jsonl")}
    scans = {s["url"]: s for s in tp.read_jsonl(LIVE / "scans.jsonl")}
    spend = load_json(LIVE / "spend.json", {})
    problems = []

    # 1. What was published yesterday and today (PH time), with each article's last-edit time
    jobs = [(o, d) for o in LISTERS for d in (today - timedelta(days=1), today)]
    listed = []
    with cf.ThreadPoolExecutor(6) as ex:
        for (outlet, _), got in zip(jobs, ex.map(lambda j: LISTERS[j[0]](j[1]), jobs)):
            listed += got
    by_outlet = {o: sum(1 for x in listed if x["outlet"] == o) for o in LISTERS}
    for o, n in by_outlet.items():
        if n == 0:
            problems.append(f"Couldn't list {o}'s articles this run")

    # 2. New or edited since we last looked -> download the text and apply the free check
    changed = [x for x in listed if x["url"] not in articles
               or (x.get("modified_utc") or "") != (articles[x["url"]].get("modified_utc") or "")]
    tp.add_text(changed)  # fills x["text"] (SunStar titles come from the page)
    for x in changed:
        old = articles.get(x["url"], {})
        x["text_hash"] = text_hash(x.get("text") or "")
        x["worth_reading"] = bool(x.get("text")) and worth_reading(x["title"], x["text"])
        x["first_seen"] = old.get("first_seen") or now.isoformat(timespec="seconds")
        articles[x["url"]] = x

    # 3. AI reads: articles that passed and whose text changed since the last read
    to_read = [a for a in articles.values() if a.get("worth_reading") and a.get("text")
               and scans.get(a["url"], {}).get("text_hash") != a["text_hash"]]
    to_read.sort(key=lambda a: a["published"], reverse=True)
    if len(to_read) > MAX_AI_READS:
        problems.append(f"{len(to_read) - MAX_AI_READS} articles left for the next run (limit {MAX_AI_READS} per run)")
        to_read = to_read[:MAX_AI_READS]
    # Text isn't kept between runs: an article left unread last time (no credit, over the limit) is downloaded again
    tp.add_text([a for a in to_read if not a.get("text")])
    for a in to_read:
        a["text_hash"] = text_hash(a.get("text") or "")
    to_read = [a for a in to_read if a.get("text")]
    read_ok = 0
    if to_read:
        client = tp.make_client()

        def work(a):
            try:
                return a, tp.scan_one(client, a)
            except Exception as e:
                return a, {"url": a["url"], "error": f"{type(e).__name__}: {e}"}

        with cf.ThreadPoolExecutor(4) as ex:
            for a, res in ex.map(work, to_read):
                if "result" in res:
                    read_ok += 1
                    for sp in res["result"]["suspensions"]:
                        sp["check"] = tp.verify(sp, a["text"])  # is the quote really in the article?
                    scans[a["url"]] = {**res, "text_hash": a["text_hash"], "read_at": now.isoformat(timespec="seconds")}
                    u = res["usage"]
                    month = today.strftime("%Y-%m")
                    spend[month] = round(spend.get(month, 0) + u["input_tokens"] * tp.PRICE_IN / 1e6
                                         + u["output_tokens"] * tp.PRICE_OUT / 1e6, 4)
                else:
                    err = res["error"]
                    msg = "the AI account is out of credit" if "credit balance" in err else err[:200]
                    if f"AI couldn't read some articles: {msg}" not in problems:
                        problems.append(f"AI couldn't read some articles: {msg}")

    # 4. Forget anything older than KEEP_DAYS
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    articles = {u: a for u, a in articles.items() if a["published"][:10] >= cutoff}
    scans = {u: s for u, s in scans.items() if u in articles}

    # 5. Rebuild the published data from every read we have
    dates = {(today + timedelta(days=k)).isoformat() for k in range(-WINDOW_BACK, WINDOW_AHEAD + 1)}
    pool = {u: a for u, a in articles.items() if a.get("worth_reading")}
    records, unplaced, _, _ = tp.build_records(pool, list(scans.values()), dates)
    for r in records:
        for s in r["sources"]:
            s.pop("check", None)
    data = {
        "checked_at": now.isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "outlets": list(LISTERS),
        "records": records,
        "problems": problems,
        "stats": {"listed": len(listed), "new_or_edited": len(changed),
                  "passed_free_check": sum(1 for x in changed if x["worth_reading"]),
                  "ai_reads": read_ok, "spend_usd_this_month": round(spend.get(today.strftime("%Y-%m"), 0), 2)},
    }
    LIVE.mkdir(exist_ok=True)
    tp.write_jsonl(LIVE / "articles.jsonl", [{k: v for k, v in a.items() if k != "text"}
                                             for a in sorted(articles.values(), key=lambda a: a["published"])])
    tp.write_jsonl(LIVE / "scans.jsonl", list(scans.values()))
    (LIVE / "spend.json").write_text(json.dumps(spend, indent=1), encoding="utf-8")
    (LIVE / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tp.log(f"{now.astimezone(PH):%Y-%m-%d %H:%M} PH: listed {len(listed)} {by_outlet}, "
           f"{len(changed)} new/edited, {data['stats']['passed_free_check']} passed the free check, "
           f"{read_ok} read by AI. {len(records)} place-days published. "
           f"Spent this month: ${data['stats']['spend_usd_this_month']}"
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
    regions = {r["code"]: REGION_FIX.get(r["name"], r["name"]) for r in tp.get(f"{PSGC}/regions.json").json()}
    provinces = {p["code"]: p["name"] for p in tp.get(f"{PSGC}/provinces.json").json()}
    rows = []
    for c in tp.get(f"{PSGC}/cities-municipalities.json").json():
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
    tp.log(f"{len(rows)} cities and towns -> {SITE / 'places.json'} "
           f"({sum(r[4] for r in rows)} marked independent)")


if __name__ == "__main__":
    cmds = {"run": cmd_run, "places": cmd_places}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(1)
    cmds[sys.argv[1]]()
