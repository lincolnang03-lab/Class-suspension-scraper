"""
Trial pipeline: pool -> AI scan -> verified raw data, one month at a time.

    python trial_pipeline.py catalog 2024-09   # list every article each outlet published (free)
    python trial_pipeline.py pool 2024-09      # keyword filter + download article text and edit times (free)
    python trial_pipeline.py times 2024-09     # (re)fetch publish / last-edit times for pooled articles (free)
    python trial_pipeline.py scan 2024-09      # Claude reads each pooled article (paid)
    python trial_pipeline.py round2 2024-09    # pull extra articles using keywords the AI found, scan them (paid)
    python trial_pipeline.py merge 2024-09     # verify quotes, merge by date/place, compare with Rappler (free)
    python trial_pipeline.py all 2024-09       # every step above, in order (skips steps already done)
    python trial_pipeline.py dashboard         # combine every merged month into trial_dashboard/data.json

The month defaults to 2024-07 (the first trial) when left out.

Everything is saved under trial/<month>/ so each step can be re-run or inspected.
"""

import concurrent.futures as cf
import copy
import html
import json
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Trial settings ───────────────────────────────────────────────────────────
ROOT = Path(__file__).parent


def set_month(ym: str):
    """Point every step at one month ("YYYY-MM")."""
    global MONTH_START, MONTH_END, CATALOG_START, OUT, RAPPLER_FILE
    y, m = map(int, ym.split("-"))
    MONTH_START = date(y, m, 1)
    MONTH_END = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    # Catalog one day before the month: suspensions are often announced the night before
    CATALOG_START = MONTH_START - timedelta(days=1)
    OUT = ROOT / "trial" / MONTH_START.strftime("%Y-%m")
    RAPPLER_FILE = ROOT / f"class_suspensions_{MONTH_START.year}.json"


set_month("2024-07")

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
PRICE_IN, PRICE_OUT = 4.00, 20.00  # USD per million tokens (Opus 5.5)

UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")}

# Round-1 pool filter, matched against title + URL. Generous on purpose:
# the AI throws out what isn't about class suspensions.
POOL_PATTERNS = [
    r"walang[\s-]*pasok", r"\bpasok\b", r"suspen", r"\bklase\b", r"\bclasses\b",
    r"\bclass\b", r"no[\s-]+classes", r"face[\s-]*to[\s-]*face", r"\bf2f\b",
    r"online[\s-]+class", r"modular", r"kanselado", r"school",
]

REGIONS = [
    "NCR", "CAR", "Ilocos Region", "Cagayan Valley", "Central Luzon", "CALABARZON",
    "MIMAROPA", "Bicol Region", "Western Visayas", "Central Visayas",
    "Negros Island Region", "Eastern Visayas", "Zamboanga Peninsula",
    "Northern Mindanao", "Davao Region", "SOCCSKSARGEN", "Caraga", "BARMM",
]


def log(*a):
    print(*a, flush=True)


def get(url, **kw):
    for attempt in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=40, **kw)
            if r.status_code in (429, 502, 503, 504):
                time.sleep(3 * (attempt + 1))
                continue
            if not r.encoding or r.encoding.lower() == "iso-8859-1":
                r.encoding = "utf-8"  # pages without a declared charset are UTF-8 here
            if r.content[:3] == b"\xef\xbb\xbf":
                r.encoding = "utf-8-sig"  # some feeds start with a byte-order mark, which json can't read
            return r
        except requests.RequestException:
            time.sleep(3 * (attempt + 1))
    return None


def html_to_text(fragment: str) -> str:
    """Article HTML -> plain text that keeps one line per paragraph / list item."""
    soup = BeautifulSoup(fragment or "", "html.parser")
    for t in soup(["script", "style", "figure", "iframe"]):
        t.decompose()
    lines = []
    for el in soup.find_all(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td"]):
        if el.find(["p", "li"]):  # skip wrappers; their children are listed themselves
            continue
        t = el.get_text(" ", strip=True)
        if t:
            prefix = "- " if el.name == "li" else ("## " if el.name.startswith("h") else "")
            lines.append(prefix + t)
    if not lines:
        lines = [soup.get_text("\n", strip=True)]
    return "\n".join(lines)


def daterange(a: date, b: date):
    while a <= b:
        yield a
        a += timedelta(days=1)


def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def write_jsonl(path: Path, rows: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


# ── 1. Catalog: every article each outlet published ─────────────────────────

def catalog_wordpress(outlet: str, base: str, d: date) -> list:
    """One day of posts from a WordPress site's public posts feed."""
    rows, page = [], 1
    while True:
        r = get(f"{base}/wp-json/wp/v2/posts", params={
            "after": f"{d}T00:00:00", "before": f"{d + timedelta(days=1)}T00:00:00",
            "per_page": 100, "page": page, "_fields": "id,date,link,title",
        })
        if r is None or r.status_code != 200:
            break
        batch = r.json()
        rows += [{"outlet": outlet, "id": p["id"], "url": p["link"], "published": p["date"],
                  "title": html.unescape(re.sub(r"<[^>]+>", "", p["title"]["rendered"]))}
                 for p in batch]
        if page >= int(r.headers.get("X-WP-TotalPages", 1)):
            break
        page += 1
    return rows


def catalog_sunstar(d: date) -> list:
    """SunStar publishes one sitemap per day; titles come from the URL slug."""
    r = get(f"https://www.sunstar.com.ph/sitemap/sitemap-daily-{d}.xml")
    if r is None or r.status_code != 200:
        return []
    rows = []
    for block in re.findall(r"<url>(.*?)</url>", r.text, flags=re.S):
        loc = re.search(r"<loc>\s*(.*?)\s*</loc>", block)
        lm = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", block)
        if not loc:
            continue
        slug = loc.group(1).rstrip("/").rsplit("/", 1)[-1]
        rows.append({"outlet": "SunStar", "id": loc.group(1), "url": loc.group(1),
                     "published": lm.group(1)[:19] if lm else f"{d}T00:00:00",
                     "title": slug.replace("-", " ")})
    return rows


OUTLETS = {
    "Rappler": lambda d: catalog_wordpress("Rappler", "https://www.rappler.com", d),
    "Bombo Radyo": lambda d: catalog_wordpress("Bombo Radyo", "https://www.bomboradyo.com", d),
    "SunStar": catalog_sunstar,
}


def cmd_catalog():
    days = list(daterange(CATALOG_START, MONTH_END))
    jobs = [(name, d) for name in OUTLETS for d in days]
    rows = []
    with cf.ThreadPoolExecutor(6) as ex:
        for got in ex.map(lambda j: OUTLETS[j[0]](j[1]), jobs):
            rows += got
    seen, uniq = set(), []
    for r in rows:
        if r["url"] not in seen:
            seen.add(r["url"])
            uniq.append(r)
    uniq.sort(key=lambda r: (r["published"], r["outlet"]))
    write_jsonl(OUT / "catalog.jsonl", uniq)
    by = {o: sum(1 for r in uniq if r["outlet"] == o) for o in OUTLETS}
    log(f"Catalog: {len(uniq)} articles {by} -> {OUT / 'catalog.jsonl'}")


# ── 2. Pool: keyword filter + article text ───────────────────────────────────

def matches(text: str, patterns) -> list:
    t = text.lower()
    return [p for p in patterns if re.search(p, t)]


def fetch_text(item: dict) -> str:
    if item["outlet"] in ("Rappler", "Bombo Radyo"):
        base = "https://www.rappler.com" if item["outlet"] == "Rappler" else "https://www.bomboradyo.com"
        r = get(f"{base}/wp-json/wp/v2/posts/{item['id']}", params={"_fields": "content"})
        if r is not None and r.status_code == 200:
            return html_to_text(r.json()["content"]["rendered"])
    r = get(item["url"])
    if r is None or r.status_code != 200:
        return ""
    soup = BeautifulSoup(r.text, "html.parser")
    if item["outlet"] == "SunStar":
        h1 = soup.find("h1")
        if h1:
            item["title"] = h1.get_text(" ", strip=True)
        parts = soup.select("[data-test-id=text]")
        return "\n".join(html_to_text(str(p)) for p in parts)
    return html_to_text(str(soup.find("article") or soup.body or soup))


def add_text(items: list) -> list:
    def work(it):
        it["text"] = fetch_text(it)
        return it
    with cf.ThreadPoolExecutor(6) as ex:
        return list(ex.map(work, items))


def add_times(items: list) -> list:
    """Publish and last-edit times (UTC) per article, used to tell updates from disagreements."""
    wp = {"Rappler": "https://www.rappler.com", "Bombo Radyo": "https://www.bomboradyo.com"}
    for outlet, base in wp.items():
        mine = [it for it in items if it["outlet"] == outlet]
        for i in range(0, len(mine), 100):
            chunk = mine[i:i + 100]
            r = get(f"{base}/wp-json/wp/v2/posts", params={
                "include": ",".join(str(it["id"]) for it in chunk), "per_page": 100,
                "_fields": "id,date_gmt,modified_gmt"})
            got = {p["id"]: p for p in r.json()} if r is not None and r.status_code == 200 else {}
            for it in chunk:
                if it["id"] in got:
                    it["published_utc"] = got[it["id"]]["date_gmt"]
                    it["modified_utc"] = got[it["id"]]["modified_gmt"]
    for it in items:
        if it["outlet"] == "SunStar":  # SunStar's daily sitemap lastmod is the last change, in UTC
            it.setdefault("modified_utc", it["published"][:19])
    return items


def cmd_times():
    pool = add_times(read_jsonl(OUT / "pool.jsonl"))
    write_jsonl(OUT / "pool.jsonl", pool)
    log(f"Edit times found for {sum(1 for p in pool if p.get('modified_utc'))} of {len(pool)} pooled articles")


def cmd_pool():
    catalog = read_jsonl(OUT / "catalog.jsonl")
    pool = []
    for it in catalog:
        hits = matches(f"{it['title']} {it['url']}", POOL_PATTERNS)
        if hits:
            pool.append({**it, "round": 1, "matched": hits})
    log(f"Pool: {len(pool)} of {len(catalog)} catalog articles matched the keywords; downloading text...")
    pool = add_times(add_text(pool))
    empty = sum(1 for p in pool if not p["text"])
    write_jsonl(OUT / "pool.jsonl", pool)
    log(f"Saved {len(pool)} articles ({empty} with no text) -> {OUT / 'pool.jsonl'}")


# ── 3. Scan: Claude reads each article ───────────────────────────────────────

def _nullable(s):
    return {"anyOf": [s, {"type": "null"}]}


SCAN_SCHEMA = {
    "type": "object",
    "properties": {
        "about_class_suspensions": {"type": "boolean"},
        "suspensions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "place_as_written": {"type": "string"},
                    "island": _nullable({"type": "string", "enum": ["Luzon", "Visayas", "Mindanao"]}),
                    "region": _nullable({"type": "string", "enum": REGIONS}),
                    "province": _nullable({"type": "string"}),
                    "city": _nullable({"type": "string"}),
                    "dates": {"type": "array", "items": {"type": "string", "format": "date"}},
                    "levels": {"type": "string"},
                    "status": {"type": "string", "enum": ["suspended", "lifted", "online_or_modular", "other"]},
                    "evidence_quote": {"type": "string"},
                },
                "required": ["place_as_written", "island", "region", "province", "city",
                             "dates", "levels", "status", "evidence_quote"],
                "additionalProperties": False,
            },
        },
        "keywords": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "kind": {"type": "string", "enum": ["event", "phrase"]},
                },
                "required": ["keyword", "kind"],
                "additionalProperties": False,
            },
        },
        "cited_sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["about_class_suspensions", "suspensions", "keywords", "cited_sources"],
    "additionalProperties": False,
}

SCAN_SYSTEM = """\
You read one Philippine news article and extract class suspensions ("walang pasok") from it.

Return about_class_suspensions = false (and empty lists) if the article does not announce or
report class suspensions for specific places. Articles about school openings, enrollment,
suspended officials, suspended operations of businesses, etc. are NOT about class suspensions.

For each place whose classes were suspended (or whose suspension was lifted, or whose classes
were moved online / modular), add one entry:
- place_as_written: the place exactly as the article names it.
- island / region / province / city: where it is. Metro Manila is region "NCR", province
  "Metro Manila". Negros Occidental/Oriental and Siquijor are "Negros Island Region".
  Use city "all cities" when a whole province suspended classes, and city null when the
  article names only a region or school.
  Use official names ("Quezon City", "City of Manila", "Malolos City").
- dates: the calendar date(s) the suspension applies to, as YYYY-MM-DD. Work out words like
  "tomorrow", "Wednesday" or "until further notice" from the article's publish date. For
  "until further notice", list only the dates the article itself covers.
- levels: the levels and details as written, e.g. "all levels (public and private)",
  "preschool to senior high school (public)".
- status: suspended, lifted, online_or_modular, or other.
- evidence_quote: copy the exact sentence or list line from the article that supports this
  entry, character for character. One line is enough. Never paraphrase.

Only include places named in the article text. Do not add places from your own knowledge.

keywords: names that would help find more articles about these suspensions: storm or event
names (kind "event", e.g. "Carina", "habagat") and Filipino/English phrasings for class
suspensions you saw that are not obvious (kind "phrase", e.g. "suspendido ang klase").
cited_sources: who the article says announced the suspensions (e.g. "Malolos City PIO",
"DepEd Region 3").
"""


def scan_one(client, item: dict) -> dict:
    content = (f"Outlet: {item['outlet']}\nPublished: {item['published']}\nTitle: {item['title']}\n"
               f"URL: {item['url']}\n\n<article>\n{item['text'][:60000]}\n</article>")
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=32000,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCAN_SCHEMA}},
        system=SCAN_SYSTEM,
        messages=[{"role": "user", "content": content}],
    ) as stream:
        resp = stream.get_final_message()
    usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
    if resp.stop_reason in ("refusal", "max_tokens"):
        return {"url": item["url"], "error": resp.stop_reason, "usage": usage}
    text = next(b.text for b in resp.content if b.type == "text")
    return {"url": item["url"], "result": json.loads(text), "usage": usage}


def load_api_key():
    """
    Find the API key: ANTHROPIC_API_KEY in the environment, else a line
    ANTHROPIC_API_KEY=... in .env in this folder, else the file ~/.anthropic_key.
    """
    import os
    if os.environ.get("ANTHROPIC_API_KEY"):
        return None
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "ANTHROPIC_API_KEY" and value.strip():
                return value.strip().strip("\"'")
    key_file = Path.home() / ".anthropic_key"
    if key_file.exists():
        return key_file.read_text().strip()
    sys.exit("No API key: add ANTHROPIC_API_KEY=... to .env in this folder")


def make_client():
    import anthropic
    key = load_api_key()
    # Ask for gzip: the brotli package installed here (1.0.9) is too old for the HTTP library,
    # and a brotli-compressed reply fails to decode with "process() takes no keyword arguments".
    return anthropic.Anthropic(max_retries=4, default_headers={"Accept-Encoding": "gzip, deflate"},
                               **({"api_key": key} if key else {}))


def run_scan(items: list) -> list:
    client = make_client()
    results_path = OUT / "scan.jsonl"
    done = {r["url"] for r in read_jsonl(results_path) if "result" in r}
    todo = [it for it in items if it["url"] not in done and it.get("text")]
    log(f"Scanning {len(todo)} articles ({len(done)} already done)...")
    out = read_jsonl(results_path)
    out = [r for r in out if "result" in r]

    def work(it):
        try:
            return scan_one(client, it)
        except Exception as e:  # keep going; failures are recorded and retried on the next run
            return {"url": it["url"], "error": f"{type(e).__name__}: {e}"}

    with cf.ThreadPoolExecutor(4) as ex:
        for i, res in enumerate(ex.map(work, todo), 1):
            out.append(res)
            write_jsonl(results_path, out)
            if i % 10 == 0 or i == len(todo):
                log(f"  {i}/{len(todo)}")
    errors = [r for r in out if "error" in r]
    if errors:
        log(f"  {len(errors)} articles failed (re-run scan to retry): {errors[0]['error']}")
    return out


def cmd_scan():
    run_scan(read_jsonl(OUT / "pool.jsonl"))


# ── 3b. Round 2: new keywords from the AI ────────────────────────────────────

def cmd_round2(max_new: int = 80):
    pool = read_jsonl(OUT / "pool.jsonl")
    scans = {r["url"]: r for r in read_jsonl(OUT / "scan.jsonl") if "result" in r}
    catalog = read_jsonl(OUT / "catalog.jsonl")
    in_pool = {p["url"] for p in pool}

    # keyword -> dates it was seen with (events only search near those dates; phrases search all)
    kw_dates = {}
    for url, s in scans.items():
        res = s["result"]
        if not res["about_class_suspensions"]:
            continue
        dates = {d for sp in res["suspensions"] for d in sp["dates"]}
        for k in res["keywords"]:
            word = k["keyword"].strip().lower()
            if len(word) < 4 or matches(word, POOL_PATTERNS):
                continue  # already covered by the round-1 filter
            entry = kw_dates.setdefault(word, {"kind": k["kind"], "dates": set()})
            entry["dates"] |= dates

    new = []
    for it in catalog:
        if it["url"] in in_pool:
            continue
        hay = f"{it['title']} {it['url']}".lower().replace("-", " ")
        pub = it["published"][:10]
        for word, info in kw_dates.items():
            if word not in hay:
                continue
            if info["kind"] == "event" and info["dates"]:
                lo = (min(date.fromisoformat(d) for d in info["dates"]) - timedelta(days=3)).isoformat()
                hi = max(info["dates"])
                if not (lo <= pub <= hi):
                    continue
            new.append({**it, "round": 2, "matched": [word]})
            break
    log(f"Round 2: {len(kw_dates)} new keywords {sorted(kw_dates)[:15]} -> {len(new)} new articles"
        + (f", reading the first {max_new}" if len(new) > max_new else ""))
    new = add_times(add_text(new[:max_new]))
    write_jsonl(OUT / "pool.jsonl", pool + new)
    (OUT / "round2_keywords.json").write_text(json.dumps(
        {k: {"kind": v["kind"], "dates": sorted(v["dates"])} for k, v in kw_dates.items()},
        ensure_ascii=False, indent=2), encoding="utf-8")
    run_scan(new)


# ── 4. Merge: verify, combine, compare ───────────────────────────────────────

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[‘’“”'\"`]", "", s)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


def place_key(name: str) -> str:
    s = norm(name)
    s = re.sub(r"^city of ", "", s)
    s = re.sub(r" city$", "", s)
    s = re.sub(r"^(municipality|town) of ", "", s)
    s = re.sub(r" (town|municipality)$", "", s)
    return PLACE_ALIASES.get(s, s)


# Old or alternate names used by different outlets
PLACE_ALIASES = {"montalban": "rodriguez", "general santos": "gensan"}


def match_keys(d: str, province: str, city: str) -> set:
    """Keys under which a place can match the other dataset, tolerant of province differences."""
    p, c = place_key(province or ""), place_key(city or "")
    keys = {(d, p, c)}
    if c and c != "all cities":
        keys.add((d, "*", c))          # same town, province named differently or missing
    if c == "all cities" and p:
        keys.add((d, "*", p))          # e.g. Rappler lists "Olongapo City" as its own province
    return keys


def verify(sp: dict, article_text: str) -> str:
    """'quote' = exact quote found; 'place' = place named in article; 'none' = neither."""
    text_n = norm(article_text)
    q = norm(sp["evidence_quote"])
    if q and q in text_n:
        return "quote"
    for name in (sp["place_as_written"], sp["city"], sp["province"]):
        k = place_key(name or "")
        if k and k not in ("all cities",) and k in text_n:
            return "place"
    return "none"


# ── Uniform levels / sector / notes ──────────────────────────────────────────

LEVELS = ["preschool", "elementary", "junior_high", "senior_high", "college"]
LEVEL_WORDS = [  # (level index, patterns), in school order
    (0, [r"pre-?\s?school", r"kinder", r"nursery", r"day ?care"]),
    (1, [r"elementary", r"grade school", r"grades? (one|two|three|four|five|six|[1-6])\b", r"primary"]),
    (2, [r"junior high", r"\bjhs\b", r"grades? (seven|eight|nine|ten|[7-9]|10)\b"]),
    (3, [r"senior high", r"\bshs\b", r"grades? (eleven|twelve|11|12)\b"]),
    (4, [r"college", r"universit", r"tertiary", r"higher education", r"graduate"]),
]
ALL_LEVELS = [r"all levels", r"lahat ng (antas|lebel|level|klase)", r"all (public and private )?schools",
              r"all classes", r"classes in all", r"lahat ng paaralan"]


def normalize_levels(text: str, status: str) -> dict:
    """Turn a free-text description into fixed fields the dashboard can show as columns."""
    s = (text or "").lower()
    notes = []
    specific = bool(re.search(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|selected|some|certain|several|"
                              r"affected|identified|specific)\s+(public\s+|private\s+)?schools?\b"
                              r"|school only|schools only|\bsome paaralan|\bilang(ng)? paaralan", s))
    if "postpone" in s or "ipinagpaliban" in s:
        notes.append("Opening postponed")
    if specific:
        notes.append("Specific schools only")

    if any(re.search(p, s) for p in ALL_LEVELS):
        idx = set(range(5))
    else:
        idx = {i for i, pats in LEVEL_WORDS if any(re.search(p, s) for p in pats)}
        if "secondary" in s or (re.search(r"\bhigh school", s) and not re.search(r"(junior|senior) high", s)):
            idx |= {2, 3}
        ranged = re.sub(r"face[\s-]to[\s-]face", "", s)
        if len(idx) >= 2 and re.search(r"\bto\b|hanggang|until", ranged):
            idx = set(range(min(idx), max(idx) + 1))
        elif "up to" in s and idx:
            idx = set(range(0, max(idx) + 1))
    # No level named at all: unknown, never assumed
    levels = {name: (i in idx) for i, name in enumerate(LEVELS)} if idx else None

    pub = bool(re.search(r"\bpublic\b|pampubliko", s))
    priv = bool(re.search(r"\bprivate\b|pribado", s))
    # "all levels (private); Brigada Eskwela for public schools" -> only private classes were suspended
    if pub and priv and re.search(r"\(private\)", s) and "brigada" in s:
        pub = False
    sector = "Public & private" if pub and priv else "Public only" if pub else "Private only" if priv else "Not stated"

    if "face-to-face" in s or "face to face" in s:
        notes.append("In-person only")
    t = re.search(r"starting (\d{1,2})(?::(\d{2}))?\s*([ap]m)", s)
    if t:
        notes.append(f"From {t.group(1)}{':' + t.group(2) if t.group(2) and t.group(2) != '00' else ''} {t.group(3).upper()}")
    elif "afternoon" in s:
        notes.append("From afternoon")
    if "except" in s:
        notes.append("Some schools excluded")
    if status == "online_or_modular":
        notes = [n for n in notes if n != "In-person only"] + ["Moved online / modular"]
    return {"levels": levels, "sector": sector, "notes": notes}


# ── Confidence (1-5) from who reported it ────────────────────────────────────

MAJOR_OUTLETS = {"Rappler", "GMA", "ABS-CBN", "Inquirer", "Philstar", "Manila Bulletin", "PNA", "PIA", "DepEd"}


def confidence(outlets: set, verification: str, resolution: str, details_missing: list) -> tuple:
    major = len(outlets & MAJOR_OUTLETS)
    support = len(outlets - MAJOR_OUTLETS)
    if major >= 2:
        score, why = 5, f"{major} major outlets"
    elif major == 1 and support:
        score, why = 4, "1 major + supporting outlet"
    elif major == 1:
        score, why = 3, "1 major outlet"
    elif support >= 2:
        score, why = 2, f"{support} supporting outlets"
    else:
        score, why = 1, "1 supporting outlet"
    if verification != "quote":
        score, why = max(1, score - 1), why + "; proof not found word for word"
    if resolution == "disagree":
        score, why = max(1, score - 1), why + "; sources disagree"
    if details_missing:
        score, why = max(1, score - 1), why + f"; {' and '.join(details_missing)} not confirmed"
    return score, why


SECTOR_SETS = {"Public & private": {"public", "private"}, "Public only": {"public"}, "Private only": {"private"}}
UPDATE_WORDS = re.compile(r"lifted|resume|binawi|bawiin|pinalawig|extended|expanded|moved to|shift(ed)? to|"
                          r"online|modular|update", re.I)


def _covers(a: dict, b: dict) -> bool:
    """Does claim a cover at least everything claim b covers (levels and public/private)?"""
    if a["levels"] is not None and b["levels"] is not None and \
            any(b["levels"][k] and not a["levels"][k] for k in LEVELS):
        return False
    sa, sb = SECTOR_SETS.get(a["sector"]), SECTOR_SETS.get(b["sector"])
    return not (sa and sb and not sa >= sb)


def _fill(target: dict, other: dict) -> bool:
    """Fill details target doesn't state from other. Returns True if anything was filled."""
    filled = False
    if target["levels"] is None and other["levels"] is not None:
        target["levels"], filled = other["levels"], True
    if target["sector"] == "Not stated" and other["sector"] != "Not stated":
        target["sector"], filled = other["sector"], True
    return filled


def resolve_sources(sources: list) -> dict:
    """
    Combine every source for one place-day into one answer, oldest claim first:
      - a claim that only adds missing details fills them in            -> "filled"
      - a later claim that changes things is an update when it is from
        the same outlet, expands the suspension, or lifts/moves it online -> "updated" (latest wins)
      - any other difference is a disagreement; the major outlet's
        version is kept                                                  -> "disagree"
    """
    claims = []
    for src in sources:
        c = normalize_levels(src["levels"], src["status"])
        # The AI labels postponed school openings inconsistently ("suspended" / "other"): use one status
        status = "opening_postponed" if "Opening postponed" in c["notes"] else src["status"]
        claims.append({**c, "src": src, "status": status, "time": src.get("time") or "",
                       "major": src["outlet"] in MAJOR_OUTLETS})
    claims.sort(key=lambda c: (c["time"], c["major"]))
    cur = dict(claims[0])
    filled, updates, disagreements = False, [], []
    for c in claims[1:]:
        differs = (c["levels"] is not None and cur["levels"] is not None and c["levels"] != cur["levels"]) or \
                  (c["sector"] != "Not stated" and cur["sector"] != "Not stated" and c["sector"] != cur["sector"]) or \
                  c["status"] != cur["status"]
        if not differs:
            filled |= _fill(cur, c)
            if c["major"] and not cur["major"]:  # same facts: show the major outlet's wording
                new = dict(c)
                filled |= _fill(new, cur)
                cur = new
            continue
        later = c["time"] > cur["time"]
        explicit = c["status"] in ("lifted", "online_or_modular") or UPDATE_WORDS.search(c["src"]["quote"] or "")
        same_outlet = c["src"]["outlet"] == cur["src"]["outlet"]
        # An expansion only counts as an update if it doesn't come from a lower tier than the current
        # version: a regional outlet's later recap shouldn't overrule a major outlet's roundup
        expands = _covers(c, cur) and (c["major"] or not cur["major"])
        if later and (same_outlet or expands or explicit):
            updates.append(f"Earlier: {cur['src']['levels']} ({cur['src']['outlet']}). "
                           f"Later: {c['src']['levels']} ({c['src']['outlet']}).")
            new = dict(c)
            _fill(new, cur)
            cur = new
        else:
            disagreements.append(f"{cur['src']['outlet']}: {cur['src']['levels']} / "
                                 f"{c['src']['outlet']}: {c['src']['levels']}")
            if c["major"] and not cur["major"]:
                new = dict(c)
                _fill(new, cur)
                cur = new
            else:
                _fill(cur, c)
    resolution = "disagree" if disagreements else "updated" if updates else "filled" if filled else "agree"
    return {
        "status": cur["status"],
        "suspension_info": cur["src"]["levels"],
        "levels": cur["levels"],
        "sector": cur["sector"],
        "notes": list(cur["notes"]),
        "resolution": resolution,
        "resolution_detail": disagreements or updates,
    }


# Highly urbanized and independent component cities (from AUTONOMOUS_CITY_MAP in the scraper
# notebook, plus Olongapo). A governor's province-wide suspension does not cover them.
AUTONOMOUS_CITIES = [
    "angeles city", "bacolod city", "baguio city", "butuan city", "cagayan de oro", "cagayan de oro city",
    "caloocan city", "cebu city", "city of manila", "cotabato city", "dagupan city", "davao city",
    "general santos city", "iligan city", "iloilo city", "lapu lapu city", "lapu-lapu city", "las pinas city",
    "las piñas city", "lucena city", "makati city", "malabon city", "mandaluyong city", "mandaue city",
    "manila", "marikina city", "muntinlupa city", "naga city", "navotas city", "ormoc city",
    "paranaque city", "parañaque city", "pasay city", "pasig city", "puerto princesa", "puerto princesa city",
    "quezon city", "san juan city", "santiago city", "tacloban city", "taguig city", "valenzuela city",
    "zamboanga city", "olongapo city",
]


def is_autonomous(city: str) -> bool:
    return bool(city) and place_key(city) in {place_key(c) for c in AUTONOMOUS_CITIES}


NATIONWIDE = re.compile(r"nationwide|buong bansa|whole country|entire country|all regions|\bphilippines\b", re.I)
REGION_WORDS = re.compile(r"region|rehiyon|\bncr\b|metro manila|calabarzon|mimaropa|soccsksargen|barmm|caraga|"
                          r"\bcar\b|cordillera|bicol|visayas|mindanao|luzon|ilocos|cagayan valley|zamboanga peninsula|davao region", re.I)


def build_records(pool: dict, scans: list, dates: set):
    """
    Turn AI reads into one record per place and date (only dates in `dates`).
    Returns (records, unplaced, articles, cost). Shared by the monthly trial and the live checker.

    A record's scope is "city", "schools" (some schools in a province, city null), "province"
    (city "all cities"), "region" (province null) or "national" (region and province null), so a
    parent's town can be matched against all of them.
    """
    scans = copy.deepcopy(scans)  # entries are adjusted below; the caller's reads stay as the AI gave them
    merged = {}       # (date, province_key, city_key) -> record
    unplaced = []     # entries that name no place we can use (e.g. "738 schools nationwide")
    articles = []     # per-article summary
    cost = {"input_tokens": 0, "output_tokens": 0}
    for s in scans:
        item = pool.get(s["url"], {})
        u = s.get("usage") or {}
        cost["input_tokens"] += u.get("input_tokens", 0)
        cost["output_tokens"] += u.get("output_tokens", 0)
        res = s.get("result")
        summary = {"url": s["url"], "outlet": item.get("outlet"), "title": item.get("title"),
                   "published": item.get("published"), "round": item.get("round", 1),
                   "matched": item.get("matched", []), "error": s.get("error"),
                   "relevant": bool(res and res["about_class_suspensions"]),
                   "entries": 0, "verified": 0,
                   "keywords": [k["keyword"] for k in (res or {}).get("keywords", [])],
                   "cited_sources": (res or {}).get("cited_sources", [])}
        if res and res["about_class_suspensions"]:
            for sp in res["suspensions"]:
                # The live checker stores the quote check with the read, since it doesn't keep article text
                v = sp.get("check") or verify(sp, item.get("text", ""))
                summary["entries"] += 1
                summary["verified"] += v != "none"
                specific = "Specific schools only" in normalize_levels(sp["levels"], sp["status"])["notes"]
                # Province or region named with no town and no "specific schools" -> it covers the whole area
                if sp["province"] and not sp["city"] and not specific:
                    sp["city"] = "all cities"
                scope = ("city" if sp["city"] not in (None, "all cities") else "province" if sp["city"] == "all cities"
                         else "schools" if sp["province"] else None)
                if not sp["province"] and not sp["city"] and not specific:
                    written = sp["place_as_written"] or ""
                    if NATIONWIDE.search(written) and not sp["region"]:
                        scope = "national"
                    elif sp["region"] and REGION_WORDS.search(written):
                        scope = "region"
                if scope is None:
                    unplaced.append({"date": (sp["dates"] or [None])[0], "outlet": item.get("outlet"),
                                     "url": s["url"], "title": item.get("title"),
                                     "place_as_written": sp["place_as_written"], "quote": sp["evidence_quote"]})
                    continue
                if scope in ("region", "national"):
                    sp["province"], sp["city"] = None, "all cities"
                    if scope == "national":
                        sp["region"] = None
                for d in sp["dates"]:
                    if d not in dates:
                        continue
                    pkey = place_key(sp["province"] or "") or f"{scope}:{place_key(sp['region'] or '')}"
                    key = (d, pkey, place_key(sp["city"] or sp["place_as_written"]))
                    rec = merged.setdefault(key, {
                        "date": d, "island": sp["island"], "region": sp["region"],
                        "province": sp["province"], "city": sp["city"], "scope": scope,
                        "sources": [],
                    })
                    rec["sources"].append({"outlet": item.get("outlet"), "url": s["url"],
                                           "title": item.get("title"), "quote": sp["evidence_quote"],
                                           "levels": sp["levels"], "status": sp["status"], "check": v,
                                           "time": item.get("modified_utc") or item.get("published", "")[:19]})
        articles.append(summary)

    records = []
    for key, rec in sorted(merged.items()):
        checks = [s["check"] for s in rec["sources"]]
        rec["n_outlets"] = len({s["outlet"] for s in rec["sources"]})
        rec["verification"] = "quote" if "quote" in checks else "place" if "place" in checks else "none"
        rec.update(resolve_sources(rec["sources"]))
        missing = [name for name, unknown in (("levels", rec["levels"] is None),
                                              ("public/private", rec["sector"] == "Not stated")) if unknown]
        rec["details_missing"] = missing
        rec["confidence"], rec["confidence_reason"] = confidence(
            {s["outlet"] for s in rec["sources"]}, rec["verification"], rec["resolution"], missing)
        records.append(rec)
    # A province-wide suspension covers its towns: drop "specific schools" rows it already covers,
    # and tell town rows that the wider suspension also applies.
    # Only a province-wide row backed by a major outlet (confidence 3+) is relied on here.
    # Independent cities are outside the governor's reach, except in Metro Manila where
    # a Malacañang / MMDA suspension covers every city.
    wide = {(r["date"], place_key(r["province"] or "")) for r in records
            if r["scope"] == "province" and r["status"] == "suspended" and r["confidence"] >= 3}
    covered = []
    for r in records:
        r["autonomous"] = r["scope"] == "city" and is_autonomous(r["city"])
        if r["scope"] not in ("city", "schools") or (r["date"], place_key(r["province"] or "")) not in wide:
            continue
        if r["city"] is None:
            covered.append(r)
        elif r["autonomous"] and r["province"] != "Metro Manila":
            r["notes"].append("Independent city: province-wide suspension doesn't cover it")
        else:
            r["notes"].append("Province-wide also applies")
    records = [r for r in records if r not in covered]
    return records, unplaced, articles, cost


def cmd_merge():
    pool = {p["url"]: p for p in read_jsonl(OUT / "pool.jsonl")}
    scans = read_jsonl(OUT / "scan.jsonl")
    month = {d.isoformat() for d in daterange(MONTH_START, MONTH_END)}
    records, unplaced, articles, cost = build_records(pool, scans, month)

    rappler = json.loads(RAPPLER_FILE.read_text(encoding="utf-8")) if RAPPLER_FILE.exists() else {}
    rappler_recs = [(d, r) for d in sorted(month) for r in rappler.get(d, {}).get("suspensions", [])]
    rappler_index = {}
    for d, r in rappler_recs:
        for k in match_keys(d, r.get("province"), r.get("city")):
            rappler_index.setdefault(k, r)
    merged_index = set()
    for rec in records:
        merged_index |= match_keys(rec["date"], rec["province"], rec["city"])
        rec["in_rappler_data"] = bool(match_keys(rec["date"], rec["province"], rec["city"]) & rappler_index.keys())

    only_rappler = [{"date": d, "province": r.get("province"), "city": r.get("city"),
                     "region": r.get("region"), "suspension_info": r.get("suspension_info")}
                    for d, r in rappler_recs
                    if not (match_keys(d, r.get("province"), r.get("city")) & merged_index)]

    # Raw data in the same shape as class_suspensions_YYYY.json, plus sources
    raw = {}
    for d in sorted(month):
        recs = [r for r in records if r["date"] == d and r["status"] != "lifted"]
        raw[d] = {"has_suspensions": bool(recs), "scrape_status": "trial_pipeline",
                  "suspensions": [{k: r[k] for k in ("island", "region", "province", "city",
                                                     "suspension_info", "levels", "sector", "notes", "autonomous",
                                                     "status", "resolution", "resolution_detail",
                                                     "details_missing", "confidence", "confidence_reason",
                                                     "n_outlets", "verification", "sources")} for r in recs]}
    (OUT / "raw_data.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    catalog = read_jsonl(OUT / "catalog.jsonl")
    kw = OUT / "round2_keywords.json"
    summary = {
        "month": MONTH_START.strftime("%Y-%m"),
        "generated": datetime.now().isoformat(timespec="seconds"),
        "outlets": list(OUTLETS),
        "funnel": {
            "catalog": len(catalog),
            "pool_round1": sum(1 for p in pool.values() if p.get("round", 1) == 1),
            "pool_round2": sum(1 for p in pool.values() if p.get("round") == 2),
            "scanned": sum(1 for s in scans if "result" in s),
            "relevant": sum(1 for a in articles if a["relevant"]),
            "place_days": len(records),
            "verified_quote": sum(1 for r in records if r["verification"] == "quote"),
        },
        "catalog_by_outlet": {o: sum(1 for c in catalog if c["outlet"] == o) for o in OUTLETS},
        "round2_keywords": json.loads(kw.read_text(encoding="utf-8")) if kw.exists() else {},
        "cost_usd": round(cost["input_tokens"] * PRICE_IN / 1e6 + cost["output_tokens"] * PRICE_OUT / 1e6, 2),
        "tokens": cost,
        "rappler_place_days": len(rappler_recs),
        "rappler_days": sorted({d for d, _ in rappler_recs}),
        "unplaced": len(unplaced),
    }
    dash = json.dumps({"summary": summary, "records": records, "only_rappler": only_rappler,
                       "unplaced": unplaced, "articles": articles}, ensure_ascii=False)
    (OUT / "dashboard_data.json").write_text(dash, encoding="utf-8")
    f = summary["funnel"]
    log(f"Merged: {f['place_days']} place-days from {f['relevant']} relevant articles "
        f"({f['verified_quote']} with quotes found word-for-word). "
        f"Rappler had {len(rappler_recs)}; {sum(r['in_rappler_data'] for r in records)} overlap, "
        f"{len(only_rappler)} only in Rappler. AI cost ~${summary['cost_usd']}")


def cmd_dashboard():
    """Combine every merged month under trial/ into the dashboard's data.json."""
    months = sorted(p.parent.name for p in (ROOT / "trial").glob("*/dashboard_data.json"))
    records, outlets = [], []
    for m in months:
        d = json.loads((ROOT / "trial" / m / "dashboard_data.json").read_text(encoding="utf-8"))
        records += d["records"]
        outlets += [o for o in d["summary"]["outlets"] if o not in outlets]
    out = {"summary": {"months": months, "outlets": outlets,
                       "generated": datetime.now().isoformat(timespec="seconds")},
           "records": records}
    (ROOT / "trial_dashboard").mkdir(exist_ok=True)
    path = ROOT / "trial_dashboard" / "data.json"
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"Dashboard: {len(records)} place-days across {len(months)} months -> {path} "
        f"({path.stat().st_size / 1e6:.1f} MB)")


def cmd_all():
    """Every step for the month. Steps whose output already exists are skipped, so a run
    that stopped part-way (network, credits) picks up where it left off."""
    if not (OUT / "catalog.jsonl").exists():
        cmd_catalog()
    if not (OUT / "pool.jsonl").exists():
        cmd_pool()
    def stop_if_failed(results):
        # Round 2 builds its keywords from round 1, and the merge would publish a half-read month:
        # stop here instead, and the next run retries only what failed.
        failed = [r for r in results if "error" in r]
        if failed:
            sys.exit(f"Stopped {MONTH_START:%Y-%m}: {len(failed)} articles weren't read ({failed[0]['error'][:160]}). "
                     f"Run it again to retry them.")

    stop_if_failed(run_scan(read_jsonl(OUT / "pool.jsonl")))  # resumes: only articles without a result are sent
    if not (OUT / "round2_keywords.json").exists():
        cmd_round2()
    stop_if_failed(run_scan(read_jsonl(OUT / "pool.jsonl")))  # retries any round-2 article that failed
    cmd_merge()


if __name__ == "__main__":
    cmds = {"catalog": cmd_catalog, "pool": cmd_pool, "times": cmd_times, "scan": cmd_scan,
            "round2": cmd_round2, "merge": cmd_merge, "all": cmd_all, "dashboard": cmd_dashboard}
    if len(sys.argv) not in (2, 3) or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(1)
    if len(sys.argv) == 3:
        if not re.fullmatch(r"\d{4}-\d{2}", sys.argv[2]):
            sys.exit("Month must look like 2024-09")
        set_month(sys.argv[2])
    if sys.argv[1] != "dashboard":
        log(f"== {sys.argv[1]} {MONTH_START:%Y-%m} ==")
    cmds[sys.argv[1]]()
