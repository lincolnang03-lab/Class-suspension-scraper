"""
Where suspension news comes from: major outlets only, in this order of priority.

    PNA, DepEd, Rappler, GMA, Inquirer

How each one is read:
- PNA, DepEd, GMA: their own news pages / feeds list recent articles; each article's text is read
  from the page and, if it passes the free check, by Claude.
- Rappler: its daily #WalangPasok roundup, parsed by rappler_history.py with no AI. Rappler's
  robots.txt opts out of AI tools (anthropic-ai, ClaudeBot, ChatGPT-User and others), so its
  articles are never sent to Claude.
- Inquirer: not read for now (see BLOCKED). We say who we are when we fetch (web.UA), and
  Inquirer's article pages sit behind bot protection; we don't disguise the checker to get past it.
- ABS-CBN was dropped: its whole site refuses anything that isn't a web browser.

    python sources.py      # list what each outlet has right now (free)
"""

import html
import io
import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

from web import get, html_to_text, log

OUTLETS = ["PNA", "DepEd", "Rappler", "GMA", "Inquirer"]
OFFICIAL = {"PNA", "DepEd"}            # the government's own news agency and the education department
AI_READ = ["PNA", "DepEd", "GMA"]      # outlets whose articles Claude reads
BLOCKED = {
    "Inquirer": "Its article pages are behind bot protection; its feed has headlines but no text.",
}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _rss(url: str, outlet: str) -> list:
    r = get(url)
    if r is None or r.status_code != 200:
        return []
    rows = []
    for item in re.findall(r"<item>(.*?)</item>", r.text, re.S):
        link = re.search(r"<link>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</link>", item, re.S)
        title = re.search(r"<title>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</title>", item, re.S)
        pub = re.search(r"<pubDate>\s*(.*?)\s*</pubDate>", item, re.S)
        if not link:
            continue
        url_ = html.unescape(link.group(1)).split("?utm_")[0].split("?")[0]
        published = ""
        if pub:
            try:
                published = _iso(parsedate_to_datetime(pub.group(1)))
            except (TypeError, ValueError):
                pass
        rows.append({"outlet": outlet, "url": url_, "title": html.unescape(re.sub(r"<[^>]+>", "", title.group(1))).strip()
                     if title else "", "published": published, "modified_utc": published})
    return rows


# ── Listing what each outlet has now ────────────────────────────────────────

def list_pna() -> list:
    """PNA's latest, national, provincial and regional pages (20 articles each); dates come with the article."""
    rows = {}
    for page in ("latest-news", "news/national", "news/provincial", "news/regions"):
        r = get(f"https://www.pna.gov.ph/{page}")
        if r is None or r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=re.compile(r"^/article/")):
            url = "https://www.pna.gov.ph" + a["href"].split("?")[0]
            rows.setdefault(url, {"outlet": "PNA", "url": url, "title": a.get_text(" ", strip=True),
                                  "published": "", "modified_utc": ""})
    return list(rows.values())


def list_deped() -> list:
    rows = _rss("https://www.deped.gov.ph/feed/", "DepEd") + _rss("https://www.deped.gov.ph/feed/?paged=2", "DepEd")
    return list({r["url"]: r for r in rows}.values())


def list_gma() -> list:
    rows = []
    for section in ("", "nation/", "regions/", "metro/"):
        rows += _rss(f"https://data.gmanetwork.com/gno/rss/news/{section}feed.xml", "GMA")
    return list({r["url"]: r for r in rows}.values())


LISTERS = {"PNA": list_pna, "DepEd": list_deped, "GMA": list_gma}


# ── Listing past days (for rebuilding past months) ──────────────────────────
# Only DepEd keeps an archive we can read by date (its sitemap URLs carry /YYYY/MM/DD/). PNA's and
# GMA's feeds and pages reach back a few days at most; a past-month rebuild needs another way in.

_deped_archive = []


def deped_days(days) -> list:
    if not _deped_archive:
        n = 1
        while True:
            r = get(f"https://www.deped.gov.ph/wp-sitemap-posts-post-{n}.xml")
            if r is None or r.status_code != 200:
                break
            _deped_archive.extend(re.findall(r"<loc>\s*(https://www\.deped\.gov\.ph/(\d{4})/(\d{2})/(\d{2})/[^<]+?)\s*</loc>", r.text))
            n += 1
    wanted = {d.isoformat() for d in days}
    rows = []
    for url, y, m, d in _deped_archive:
        if f"{y}-{m}-{d}" in wanted:
            slug = url.rstrip("/").rsplit("/", 1)[-1]
            rows.append({"outlet": "DepEd", "url": url, "title": slug.replace("-", " "),
                         "published": f"{y}-{m}-{d}T00:00:00", "modified_utc": ""})
    return rows


DAY_LISTERS = {"DepEd": deped_days}


def list_recent(outlet: str) -> list:
    return LISTERS[outlet]() if outlet in LISTERS else []


# ── Reading one article ─────────────────────────────────────────────────────

def _ld(soup) -> dict:
    """The page's schema.org article data (headline, dates, sometimes the body)."""
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except (TypeError, ValueError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop()
            if isinstance(d, dict):
                stack += d.get("@graph", []) if isinstance(d.get("@graph"), list) else []
                if d.get("articleBody") or d.get("datePublished"):
                    return d
    return {}


def _main_text(soup) -> str:
    """The block holding the most paragraphs: the article body on pages without a fixed layout."""
    blocks = soup.find_all(["div", "section", "article"])
    best = max(blocks, key=lambda b: len(b.find_all("p", recursive=False)), default=None)
    return html_to_text(str(best)) if best is not None else ""


def _pdf_text(url: str, max_pages: int = 6) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    r = get(url)
    if r is None or r.status_code != 200:
        return ""
    try:
        reader = PdfReader(io.BytesIO(r.content))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:max_pages])
    except Exception:  # a damaged or scanned PDF just gives no text
        return ""


def fetch(item: dict) -> dict:
    """Fill in the article's text, title and dates. Leaves text empty if the page can't be read."""
    item["text"] = ""
    if item["outlet"] in BLOCKED or item["outlet"] == "Rappler":
        return item
    r = get(item["url"])
    if r is None or r.status_code != 200:
        return item
    soup = BeautifulSoup(r.text, "html.parser")
    ld = _ld(soup)
    if ld.get("headline") and not item.get("title"):
        item["title"] = html.unescape(ld["headline"])
    for key, field in (("datePublished", "published"), ("dateModified", "modified_utc")):
        if ld.get(key):
            try:
                item[field] = _iso(datetime.fromisoformat(ld[key].replace("Z", "+00:00")))
            except ValueError:
                pass
    if item["outlet"] == "DepEd":
        for prop, field in (("article:published_time", "published"), ("article:modified_time", "modified_utc")):
            m = soup.find("meta", property=prop)
            if m and m.get("content"):
                item[field] = _iso(datetime.fromisoformat(m["content"]))
        body = soup.select_one(".entry-content") or soup
        text = html_to_text(str(body)) if body is not soup else _main_text(soup)
        # Advisories are often a short post with the memo attached as a PDF
        pdfs = [a["href"] for a in body.find_all("a", href=True) if a["href"].lower().endswith(".pdf")]
        for pdf in pdfs[:2]:
            text += "\n" + _pdf_text(pdf)
        item["text"] = text.strip()
    elif item["outlet"] == "GMA" and ld.get("articleBody"):
        item["text"] = html_to_text(html.unescape(ld["articleBody"]))
    else:
        item["text"] = _main_text(soup)
    item.setdefault("modified_utc", item.get("published", ""))
    if not item.get("modified_utc"):
        item["modified_utc"] = item.get("published", "")
    return item


# ── Rappler: the daily roundup, without AI ──────────────────────────────────

def rappler_roundup(d) -> dict:
    """Rappler's #WalangPasok list for one date, parsed by the notebook's parser. No AI."""
    import rappler_history as rh
    url = rh.build_url(d)
    page = rh.fetch_page(url)
    if page is None:
        return {"url": url, "found": False, "suspensions": []}
    div = rh.extract_article_div(page)
    recs = rh.parse_suspensions(div) if div is not None else []
    modified = ""
    r = get("https://www.rappler.com/wp-json/wp/v2/posts",
            params={"slug": url.rstrip("/").rsplit("/", 1)[-1], "_fields": "modified_gmt,title"})
    if r is not None and r.status_code == 200 and r.json():
        modified = r.json()[0].get("modified_gmt") or ""
    return {"url": url, "found": True, "suspensions": recs, "modified_utc": modified}


if __name__ == "__main__":
    for outlet in OUTLETS:
        if outlet in BLOCKED:
            log(f"{outlet:9} not read: {BLOCKED[outlet]}")
        elif outlet == "Rappler":
            log(f"{outlet:9} daily roundup, parsed without AI")
        else:
            rows = list_recent(outlet)
            dated = sorted(r["published"] for r in rows if r["published"])
            log(f"{outlet:9} {len(rows):3} articles listed" + (f", {dated[0]} to {dated[-1]}" if dated else ""))
