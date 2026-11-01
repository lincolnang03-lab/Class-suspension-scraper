"""Shared plumbing: polite HTTP, HTML to text, JSONL files, logging."""

import json
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# Says who we are. Sites that only answer a browser are left out rather than fooled (see sources.py).
UA = {"User-Agent": "WalangPasokChecker/1.0 (class-suspension tracker; "
                    "+https://github.com/lincolnang03-lab/Class-suspension-scraper)"}


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


def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def write_jsonl(path: Path, rows: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
