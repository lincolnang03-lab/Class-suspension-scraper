"""
Backfill: what PNA, DepEd and GMA reported on the past days that had class suspensions, to add the
places Rappler's roundups missed and to confirm the ones they listed. Built to keep the bill small:

  1. find    - Claude Haiku 5.5 runs a few web searches per day, limited to those three sites, and we
               keep the article links from the results (Claude doesn't open the pages: that's step 2)
  2. read    - we download each article ourselves (free), apply the live checker's free text check,
               and Haiku copies out the passages about class suspensions (reader.read_passages)
  3. structure - Sonnet 5.5 turns those passages into records, through the Batches API (half price)

    python backfill.py run --only 2025-07-22,2025-03-04    # a test on chosen days
    python backfill.py run --cap 18          # every day still to do, stopping before $18 is spent
    python backfill.py weeks --only 2025-03-31 --cap 22   # the cheaper weekly search (Mondays given)
    python backfill.py weeks --more --cap 22              # 3 more searches a week: Luzon, Visayas, Mindanao

The days Rappler shows suspensions are searched one by one (6 searches each). The other weeks get a
cheaper pass: 3 searches for the whole week, for the single-town suspensions Rappler doesn't list. Results go to backfill/ (no article text: the
repo is public); build_history.py merges them with Rappler's lists into the dashboard's past data.
"""

import concurrent.futures as cf
import difflib
import hashlib
import json
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from bs4 import BeautifulSoup

import costs
import live
import reader
import sources
import trial_pipeline as tp
from web import get, log, read_jsonl, write_jsonl

ROOT = Path(__file__).parent
DIR = ROOT / "backfill"
DAYS = DIR / "days.json"            # per day: the links found and what happened to each
READS = DIR / "reads.jsonl"         # Haiku's passages per article, waiting for (or past) step 3
SCANS = DIR / "scans.jsonl"         # finished reads, in the shape tp.build_records takes
WEEKS = DIR / "weeks.json"          # per week (its Monday) for the weekly pass: the same as DAYS
TEXT = DIR / ".text"                # article text, kept locally (gitignored) only until its quotes are checked
SITES = {"pna.gov.ph": "PNA", "deped.gov.ph": "DepEd", "gmanetwork.com": "GMA"}
SEARCHES_PER_DAY = 6
SEARCHES_PER_WEEK = 3
FIRST_DAY = date(2025, 1, 1)
STRUCTURE_USD = 0.02     # Sonnet, batch price, per article (measured: about $0.017)
WEEK_STRUCTURE_MODEL = reader.READ_MODEL   # the weekly pass structures with Haiku: about 20x cheaper
WEEK_STRUCTURE_USD = 0.002
JOB = "backfill"

FIND_SYSTEM = """\
You find news articles about class suspensions in the Philippines on one given date. Run up to {n} web
searches that would surface reports of classes being suspended for that date, each worded differently:
- one written the way the Philippine News Agency (PNA) headlines it, e.g. "classes suspended" with the
  date as "July 10" and the weekday, and the word PNA
- one in Filipino, e.g. "walang pasok" or "suspendido ang klase" with the date
- about a third naming the provinces given in the user's message, one or two per search, with the date
- the rest in English: "class suspension", "suspension of classes", and the storm or weather named in the
  user's message, with the date
When you're done, reply with just "done".
"""

FIND_WEEK_SYSTEM = """\
You find news articles about class suspensions in the Philippines during one school week. These are
usually small: one town or province, for floods, heavy rain, extreme heat, an earthquake, a volcano, a
transport strike, a power outage or a local event. Run up to {n} web searches, each worded differently:
- one written the way the Philippine News Agency (PNA) headlines it, e.g. "classes suspended" with the
  month and the days of the week given, and the word PNA
- one in Filipino, e.g. "walang pasok" or "suspendido ang klase", with the month and days
- one for GMA Regional TV, e.g. "GMA Regional TV class suspension", with the month and days
When you're done, reply with just "done".
"""

FIND_WEEK_MORE_SYSTEM = """\
You find news articles about class suspensions in the Philippines during one school week that earlier
searches missed. These are usually small: one town or province, for floods, heavy rain, extreme heat,
an earthquake, a volcano, a transport strike, a power outage or a local event. Run up to {n} web
searches, each for one part of the country, worded differently from the earlier searches listed in the
user's message:
- one for Luzon and Metro Manila, naming a few of its provinces or cities
- one for the Visayas, naming a few of its provinces or cities
- one for Mindanao, naming a few of its provinces or cities
Each with "class suspension", "suspend classes" or "walang pasok" and the month and days of the week.
When you're done, reply with just "done".
"""


def target_days() -> list:
    out = []
    for y in range(2025, date.today().year + 1):
        path = ROOT / f"class_suspensions_{y}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            out += [(d, v) for d, v in data.items() if v.get("suspensions")]
    return sorted(out)


def outlet_of(url: str) -> str:
    host = url.split("/")[2].removeprefix("www.")
    return next((o for site, o in SITES.items() if host == site or host.endswith("." + site)), "")


def find_links(client, d: date, hint: str, provinces: list = (), n: int = SEARCHES_PER_DAY) -> tuple:
    """Step 1 for one day Rappler lists suspensions."""
    return search(client, FIND_SYSTEM.format(n=n), n,
                  f"Date: {d:%A, %B %-d, %Y}.\nWhat Rappler said caused it: {hint[:300]}\n"
                  f"Provinces with suspensions that day: {', '.join(provinces) or 'not known'}")


def find_week_links(client, monday: date, covered: list, n: int = SEARCHES_PER_WEEK, earlier: list = ()) -> tuple:
    """Step 1 for one week, cheaper: a few searches for the whole week. With `earlier` (queries
    already run for the week), a second round, one search per part of the country."""
    friday = monday + timedelta(days=4)
    span = (f"{monday:%B %-d} to {friday:%-d, %Y}" if monday.month == friday.month
            else f"{monday:%B %-d} to {friday:%B %-d, %Y}")
    note = (f"\nAlready covered (no need to search): {', '.join(f'{date.fromisoformat(c):%A %B %-d}' for c in covered)}"
            if covered else "")
    if earlier:
        note += "\nEarlier searches for this week:\n" + "\n".join(f"- {q}" for q in earlier)
    return search(client, (FIND_WEEK_MORE_SYSTEM if earlier else FIND_WEEK_SYSTEM).format(n=n), n, f"Week: Monday {span}.{note}")


def search(client, system: str, n: int, ask: str) -> tuple:
    """Haiku searches; the links come from the search results themselves.
    -> (links, usage, searches): searches lists each query with the links it brought up, in order."""
    resp = client.messages.create(
        model=reader.READ_MODEL,
        max_tokens=4000,
        output_config={"effort": "low"},
        system=system,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": n,
                "allowed_domains": list(SITES)}],
        messages=[{"role": "user", "content": ask}],
    )
    links, searches = {}, {}
    for b in resp.content:
        if b.type == "server_tool_use":
            searches[b.id] = {"query": (b.input or {}).get("query", ""), "urls": []}
        elif b.type == "web_search_tool_result" and isinstance(b.content, list):
            for x in b.content:
                outlet = outlet_of(x.url)
                if outlet and (outlet != "GMA" or "/news/" in x.url):
                    url = x.url.split("?")[0].split("#")[0]
                    links.setdefault(url, {"outlet": outlet, "title": x.title or ""})
                    if b.tool_use_id in searches:
                        searches[b.tool_use_id]["urls"].append(url)
    return [{"url": u, **v} for u, v in links.items()], costs.usage_of(resp), list(searches.values())


MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"


def from_archive(it: dict) -> dict:
    """PNA moved its site: older links (/articles/<number>) now return 404, so read the Internet
    Archive's copy instead, when it has one."""
    if it.get("text") or it["outlet"] != "PNA" or "/articles/" not in it["url"]:
        return it
    r = get("https://archive.org/wayback/available", params={"url": it["url"].split("://", 1)[1]})
    snap = (r.json().get("archived_snapshots", {}).get("closest") or {}) if r is not None and r.status_code == 200 else {}
    if not snap.get("url"):
        return it
    page = get(snap["url"].replace("/http", "id_/http", 1))   # id_: the page as it was, without the archive's toolbar
    if page is None or page.status_code != 200:
        return it
    when = re.search(rf"calendar-icon>.{{0,300}}?({MONTHS}) (\d{{1,2}}), (20\d\d)", page.text, re.S)
    if when:
        it["published"] = datetime.strptime(" ".join(when.groups()), "%B %d %Y").date().isoformat() + "T00:00:00"
        it["modified_utc"] = it["published"]
    it["text"] = sources._main_text(BeautifulSoup(page.text, "html.parser"))
    it["archived"] = snap["url"]
    return it


def day_for(published: str, searched: str, targets: list) -> str:
    """The target day an article belongs to: published from two days before it to one day after.
    The day being searched wins; otherwise the nearest target day that fits; "" if none does."""
    if not published:
        return ""
    p = date.fromisoformat(published)
    fits = [t for t in targets if -2 <= (p - date.fromisoformat(t)).days <= 1]
    if searched in fits:
        return searched
    return min(fits, key=lambda t: abs((p - date.fromisoformat(t)).days), default="")


def spent() -> float:
    if not costs.LOG.exists():
        return 0.0
    return sum(json.loads(l)["usd"] for l in costs.LOG.read_text(encoding="utf-8").splitlines()
               if l.strip() and json.loads(l)["job"].startswith(JOB))


def text_path(url: str) -> Path:
    return TEXT / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".txt")


def cmd_run(max_days: int, cap: float, only: str = "", n_searches: int = SEARCHES_PER_DAY):
    DIR.mkdir(exist_ok=True)
    TEXT.mkdir(exist_ok=True)
    days = json.loads(DAYS.read_text(encoding="utf-8")) if DAYS.exists() else {}
    reads = {r["url"]: r for r in read_jsonl(READS)}
    todo = [(d, v) for d, v in target_days() if d not in days and (not only or d in only.split(","))][:max_days]
    client = reader.make_client()
    start = spent()
    targets = [d for d, _ in target_days()]
    log(f"{len(todo)} days to search; backfill spending so far ${start:.2f}, cap ${cap:.2f}")
    hints = json.loads((ROOT / "history_ledes.json").read_text(encoding="utf-8"))

    for d, v in todo:
        # Step 3 is billed at the end: count what it will cost against the cap now
        if spent() + pending_usd(reads) >= cap:
            log(f"Stopping before {d}: ${spent():.2f} spent + ~${pending_usd(reads):.2f} to structure reaches the ${cap:.2f} cap")
            break
        day = date.fromisoformat(d)
        provinces = [p for p, _ in Counter(s.get("province") for s in v["suspensions"] if s.get("province")).most_common(max(4, n_searches * 2 // 3))]
        links, call, searches = find_links(client, day, hints.get(d, {}).get("lede", ""), provinces, n_searches)
        costs.record(f"{JOB} find", [call])
        # An article about another of the target days is kept for that day (searches aren't exact about dates)
        items, kept = read_links(client, links, reads, lambda pub: day_for(pub, d, targets))
        days[d] = {"links": len(links), "fetched": sum(1 for it in items if it.get("text")),
                   "kept": len(kept), "for_other_days": sum(1 for it in kept if it["day"] != d),
                   "about": sum(1 for it in kept if (reads[it["url"]]["found"] or {}).get("about_class_suspensions")),
                   "searched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "searches": searches}
        DAYS.write_text(json.dumps(days, indent=1), encoding="utf-8")
        write_jsonl(READS, list(reads.values()))
        log(f"{d}: {len(links)} links, {days[d]['fetched']} read, {len(kept)} passed the free check "
            f"({days[d]['for_other_days']} for other days), "
            f"{days[d]['about']} about suspensions · ${spent() - start:.3f} this run")

    structure(client, reads)


def read_links(client, links: list, reads: dict, day_of) -> tuple:
    """Step 2: fetch (free), keep what day_of(publish date) places on a day and passes the free check,
    and have Haiku copy out the passages. Adds to `reads`. -> (items fetched, items kept)."""
    items = live.fetch_all([{**x, "published": "", "modified_utc": ""} for x in links if x["url"] not in reads])
    with cf.ThreadPoolExecutor(4) as ex:
        items = list(ex.map(from_archive, items))
    kept = []
    for it in items:
        it["day"] = day_of((it.get("published") or "")[:10])
        it["worth_reading"] = bool(it.get("text") and it["day"]) and live.worth_reading(it.get("title", ""), it["text"])
        if it["worth_reading"]:
            kept.append(it)

    def work(it):
        try:
            return it, reader.read_passages(client, it)
        except Exception as e:  # noted; the day can be redone
            return it, (None, [], f"{type(e).__name__}: {e}")

    with cf.ThreadPoolExecutor(4) as ex:
        for it, (found, calls, error) in ex.map(work, kept):
            costs.record(f"{JOB} read", calls)
            text_path(it["url"]).write_text(it["text"], encoding="utf-8")
            reads[it["url"]] = {"url": it["url"], "day": it["day"], "outlet": it["outlet"], "title": it.get("title", ""),
                                "published": it.get("published", ""), "modified_utc": it.get("modified_utc", ""),
                                **({"archived": it["archived"]} if it.get("archived") else {}),
                                "found": found, "error": error}
    return items, kept


def retry(call, tries: int = 6):
    """For a dropped connection (the SDK already retries twice): wait a minute and try again."""
    import anthropic
    for i in range(tries):
        try:
            return call()
        except anthropic.APIConnectionError:
            if i == tries - 1:
                raise
            log("Connection lost; trying again in a minute")
            time.sleep(60)


def pending_usd(reads: dict, per_article: float = STRUCTURE_USD) -> float:
    """What step 3 will cost for the reads not structured yet (it's billed at the end)."""
    done = {s["url"] for s in read_jsonl(SCANS)}
    return per_article * sum(1 for r in reads.values() if r["url"] not in done and r["found"] and r["found"]["passages"])


def empty_weeks() -> list:
    """(Monday, the weekdays Rappler lists nothing, the days it does) for every week since FIRST_DAY,
    through yesterday (Philippine time)."""
    targets = {d for d, _ in target_days()}
    yesterday = datetime.now(timezone(timedelta(hours=8))).date() - timedelta(days=1)
    out = []
    monday = FIRST_DAY - timedelta(days=FIRST_DAY.weekday())
    while monday <= yesterday:
        week = [monday + timedelta(days=i) for i in range(5)]
        empty = [x.isoformat() for x in week if FIRST_DAY <= x <= yesterday and x.isoformat() not in targets]
        if empty:
            out.append((monday.isoformat(), empty, [x.isoformat() for x in week if x.isoformat() in targets]))
        monday += timedelta(days=7)
    return out


def cmd_weeks(max_weeks: int, cap: float, only: str = "", n_searches: int = SEARCHES_PER_WEEK, more: bool = False):
    """The cheaper pass over the weeks with days Rappler lists nothing. more: a second round of
    searches for weeks already searched (saved under "more" in weeks.json)."""
    DIR.mkdir(exist_ok=True)
    TEXT.mkdir(exist_ok=True)
    weeks = json.loads(WEEKS.read_text(encoding="utf-8")) if WEEKS.exists() else {}
    reads = {r["url"]: r for r in read_jsonl(READS)}
    todo = [w for w in empty_weeks() if (("more" not in weeks[w[0]]) if more else (w[0] not in weeks))
            and (w[0] in weeks or not more) and (not only or w[0] in only.split(","))][:max_weeks]
    client = reader.make_client()
    start = spent()
    log(f"{len(todo)} weeks to search; backfill spending so far ${start:.2f}, cap ${cap:.2f}")
    # An article is placed on its publish date; Claude reads which day(s) it is about in step 3
    in_range = lambda pub: pub if pub and pub >= (FIRST_DAY - timedelta(days=3)).isoformat() else ""
    for monday, empty, covered in todo:
        pending = pending_usd(reads, WEEK_STRUCTURE_USD)
        if spent() + pending >= cap:
            log(f"Stopping before the week of {monday}: ${spent():.2f} spent + ~${pending:.2f} to structure reaches the ${cap:.2f} cap")
            break
        earlier = [q["query"] for q in weeks[monday]["searches"]] if more else []
        links, call, searches = retry(lambda: find_week_links(client, date.fromisoformat(monday), covered, n_searches, earlier))
        costs.record(f"{JOB} week find", [call])
        items, kept = read_links(client, links, reads, in_range)
        result = {"links": len(links), "fetched": sum(1 for it in items if it.get("text")), "kept": len(kept),
                  "about": sum(1 for it in kept if (reads[it["url"]]["found"] or {}).get("about_class_suspensions")),
                  "searched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "searches": searches}
        if more:
            weeks[monday]["more"] = result
        else:
            weeks[monday] = {"empty_days": empty, **result}
        WEEKS.write_text(json.dumps(weeks, indent=1), encoding="utf-8")
        write_jsonl(READS, list(reads.values()))
        log(f"week of {monday}{' (more)' if more else ''}: {len(links)} links, {result['fetched']} read, {len(kept)} passed the free check, "
            f"{result['about']} about suspensions · ${spent() - start:.3f} this run")
    structure(client, reads, WEEK_STRUCTURE_MODEL)


def structure(client, reads: dict, model: str = reader.STRUCTURE_MODEL, batch_id: str = ""):
    """Step 3: every read with passages and no structured result yet, in one batch at half price.
    batch_id: collect a batch sent earlier (e.g. by a run that was stopped while it waited)."""
    scans = {s["url"]: s for s in read_jsonl(SCANS)}
    waiting = [r for r in reads.values() if r["url"] not in scans and r["found"]
               and r["found"]["about_class_suspensions"] and r["found"]["passages"]]
    for r in reads.values():   # reads that stop at step 2 are finished as they are
        if r["url"] not in scans and r["found"] and r not in waiting:
            scans[r["url"]] = {"url": r["url"], "result": reader.not_about_suspensions(r["found"])}
    if waiting:
        ids = {hashlib.sha1(r["url"].encode()).hexdigest()[:24]: r for r in waiting}
        batch = client.messages.batches.retrieve(batch_id) if batch_id else client.messages.batches.create(requests=[
            {"custom_id": cid, "params": reader.structure_params(r, r["found"], model)} for cid, r in ids.items()])
        log(f"Batch {batch.id}: {len(ids)} articles to structure; waiting (usually minutes, at most a day)")
        while (batch := retry(lambda: client.messages.batches.retrieve(batch.id))).processing_status != "ended":
            time.sleep(30)
        for res in client.messages.batches.results(batch.id):
            r = ids.get(res.custom_id)
            if r is None:
                continue
            if res.result.type != "succeeded":
                log(f"  not structured ({res.result.type}): {r['url']}")
                continue
            msg = res.result.message
            costs.record(f"{JOB} structure", [costs.usage_of(msg, batch=True)])
            if msg.stop_reason in ("refusal", "max_tokens"):
                log(f"  not structured ({msg.stop_reason}): {r['url']}")
                continue
            result = reader.structured(msg, r["found"])
            path = text_path(r["url"])
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            for sp in result["suspensions"]:
                sp["check"] = tp.verify(sp, text)  # is the quote really in the article?
            scans[r["url"]] = {"url": r["url"], "result": result}
    write_jsonl(SCANS, list(scans.values()))
    for r in waiting:          # quotes are checked: the article text isn't needed any more
        text_path(r["url"]).unlink(missing_ok=True)
    log(f"Backfill: {len(scans)} articles finished; backfill spending ${spent():.2f} in all")


def _town_provinces() -> dict:
    """Town name -> (region, province) for names used once in the whole country (PSGC)."""
    places = json.loads((ROOT / "trial_dashboard" / "places.json").read_text(encoding="utf-8"))["places"]
    seen = {}
    for region, province, name, *_ in places:
        seen.setdefault(tp.place_key(name), set()).add((region, province))
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


def fill_provinces(day_scans: list, rappler_towns: dict, unique: dict) -> list:
    """News lists often give a town under a province heading the AI doesn't repeat. Fill the province
    from Rappler's list for the same day, else from the town's name if only one town has it."""
    out = []
    for s in day_scans:
        s = json.loads(json.dumps(s))
        for sp in (s.get("result") or {}).get("suspensions", []):
            town = tp.place_key(sp.get("city") or "")
            if not town or sp.get("city") == "all cities":
                continue
            if town not in rappler_towns:   # a one-letter slip ("Gataran") is still Rappler's "Gattaran"
                close = difflib.get_close_matches(town, list(rappler_towns), n=1, cutoff=0.88)
                same_province = not sp.get("province") or (close and tp.place_key(sp["province"]) == tp.place_key(rappler_towns[close[0]][1]))
                if close and same_province:
                    town = close[0]
                    sp["city"] = rappler_towns[town][2]
            if sp.get("province"):
                continue
            hit = rappler_towns.get(town) or unique.get(town)
            if hit:
                sp["region"], sp["province"] = sp.get("region") or hit[0], hit[1]
        out.append(s)
    return out


_WORDS = {}


def article_words(url: str) -> str:
    """An article's passages and keywords, for telling the cause of a day only the news covered."""
    if not _WORDS:
        reads = {r["url"]: r for r in read_jsonl(READS)}
        for s in read_jsonl(SCANS):
            r = reads.get(s["url"], {})
            words = " ".join((r.get("found") or {}).get("passages", []) + [k["keyword"] for k in (s.get("result") or {}).get("keywords", [])])
            _WORDS[s["url"]] = _WORDS[r.get("archived", s["url"])] = words
    return _WORDS.get(url, "")


def merged_days() -> dict:
    """For each searched day: Rappler's list and the AI reads merged into records (tp.build_records),
    with outlets and confidence. Used by build_history.py."""
    if not DAYS.exists():
        return {}
    searched = list(json.loads(DAYS.read_text(encoding="utf-8")))
    if WEEKS.exists():
        searched += sorted({d for w in json.loads(WEEKS.read_text(encoding="utf-8")).values() for d in w["empty_days"]} - set(searched))
    reads = {r["url"]: r for r in read_jsonl(READS)}
    scans = read_jsonl(SCANS)
    saved = {}
    for y in {d[:4] for d in searched}:
        saved.update(json.loads((ROOT / f"class_suspensions_{y}.json").read_text(encoding="utf-8")))
    now = datetime.now(timezone.utc)
    unique = _town_provinces()
    out = {}
    for d in searched:
        pool, day_scans, rappler_towns = {}, [], {}
        entry = saved.get(d)
        if entry and entry.get("suspensions"):
            item, read = live.rappler_read(date.fromisoformat(d), {"url": entry["url"], "suspensions": entry["suspensions"]}, now)
            pool[item["url"]] = item
            day_scans.append(read)
            rappler_towns = {tp.place_key(sp["city"]): (sp["region"], sp["province"], sp["city"]) for sp in read["result"]["suspensions"]
                             if sp["city"] and sp["city"] != "all cities" and sp["province"]}
        for s in scans:
            r = reads.get(s["url"])
            # an article from three days before (Friday, for a Monday) to the day after
            if r and -1 <= (date.fromisoformat(r["day"]) - date.fromisoformat(d)).days <= 3:
                link = r.get("archived") or r["url"]    # PNA's old links only open in the archive now
                pool[link] = {**{k: r[k] for k in ("outlet", "title", "published", "modified_utc")}, "url": link}
                day_scans.append({**s, "url": link})
        if not day_scans:
            continue
        recs = tp.build_records(pool, fill_provinces(day_scans, rappler_towns, unique), {d})[0]
        # The same town once with its province and once without: keep the one with the province
        placed = {tp.place_key(r["city"]) for r in recs if r["province"] and r["city"] not in (None, "all cities")}
        out[d] = [r for r in recs if r["province"] or r["city"] in (None, "all cities") or tp.place_key(r["city"]) not in placed]
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] not in (["run"], ["weeks"]):
        sys.exit(__doc__)
    opt = lambda name, default: type(default)(args[args.index(name) + 1]) if name in args else default
    if args[0] == "weeks":
        sys.exit(cmd_weeks(opt("--weeks", 999), opt("--cap", 22.0), opt("--only", ""), opt("--searches", SEARCHES_PER_WEEK),
                           "--more" in args))
    cmd_run(opt("--days", 999), opt("--cap", 18.0), opt("--only", ""), opt("--searches", SEARCHES_PER_DAY))
