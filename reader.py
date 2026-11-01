"""
Claude reads one article in two steps:

  1. read      - Claude Haiku 5.5 (the entry-level model) reads the whole article and copies out,
                 word for word, every passage about class suspensions. Most articles stop here.
  2. structure - Claude Sonnet 5.5 turns only those passages into records: place, dates, levels,
                 status, and a word-for-word quote as proof.

Every quote is later checked against the full article text (trial_pipeline.verify), and every
call's tokens and price go to the cost log (costs.py).
"""

import json
import os
import sys
from pathlib import Path

import costs

ROOT = Path(__file__).parent
READ_MODEL = "claude-haiku-5-5"
STRUCTURE_MODEL = "claude-sonnet-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"   # Sonnet 5.5 only; Haiku 5.5 has no server-side fallback
MAX_ARTICLE_CHARS = 60000

REGIONS = [
    "NCR", "CAR", "Ilocos Region", "Cagayan Valley", "Central Luzon", "CALABARZON",
    "MIMAROPA", "Bicol Region", "Western Visayas", "Central Visayas",
    "Negros Island Region", "Eastern Visayas", "Zamboanga Peninsula",
    "Northern Mindanao", "Davao Region", "SOCCSKSARGEN", "Caraga", "BARMM",
]


def _nullable(s):
    return {"anyOf": [s, {"type": "null"}]}


# ── Step 1: read (Haiku) ────────────────────────────────────────────────────

READ_SCHEMA = {
    "type": "object",
    "properties": {
        "about_class_suspensions": {"type": "boolean"},
        "passages": {"type": "array", "items": {"type": "string"}},
        "announced_by": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["about_class_suspensions", "passages", "announced_by"],
    "additionalProperties": False,
}

READ_SYSTEM = """\
You read one Philippine news article and find what it says about class suspensions ("walang pasok").

about_class_suspensions: true only if the article announces or reports classes being suspended,
cancelled, lifted, or moved online / modular in specific places, or a school opening being
postponed. Articles about school openings in general, enrollment, suspended officials or
suspended business operations are not about class suspensions.

passages: if true, copy every sentence or list line that names a place, a date or day, the school
levels, public or private schools, or who announced it. Copy each one exactly as written,
character for character, including the dateline at the start of the article (for example
"MALOLOS CITY, Bulacan -"). One sentence or list line per item. Never paraphrase or combine.
If false, return an empty list.

announced_by: who the article says made the announcement (for example "Malolos City PIO",
"DepEd Region 3", "Malacañang").
"""

# ── Step 2: structure (Sonnet) ──────────────────────────────────────────────

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
You get passages copied word for word from one Philippine news article, with the article's outlet,
publish date and title. Turn them into class suspension ("walang pasok") records.

Return about_class_suspensions = false (and empty lists) if the passages don't announce or report
class suspensions for specific places.

For each place whose classes were suspended (or whose suspension was lifted, or whose classes
were moved online / modular), add one entry:
- place_as_written: the place exactly as the passages name it.
- island / region / province / city: where it is. Metro Manila is region "NCR", province
  "Metro Manila". Negros Occidental/Oriental and Siquijor are "Negros Island Region".
  Use city "all cities" when a whole province suspended classes, and city null when the
  passages name only a region or school.
  Use official names ("Quezon City", "City of Manila", "Malolos City").
- dates: the calendar date(s) the suspension applies to, as YYYY-MM-DD. Work out words like
  "tomorrow", "Wednesday" or "until further notice" from the publish date. For "until further
  notice", list only the dates the passages themselves cover.
- levels: the levels and details as written, e.g. "all levels (public and private)",
  "preschool to senior high school (public)".
- status: suspended, lifted, online_or_modular, or other.
- evidence_quote: copy the exact passage that supports this entry, character for character.
  Never paraphrase.

Only include places named in the passages. Do not add places from your own knowledge.

keywords: names that would help find more articles about these suspensions: storm or event
names (kind "event", e.g. "Carina", "habagat") and Filipino/English phrasings for class
suspensions you saw that are not obvious (kind "phrase", e.g. "suspendido ang klase").
cited_sources: who the passages say announced the suspensions.
"""


def load_api_key():
    """ANTHROPIC_API_KEY in the environment, else .env in this folder, else ~/.anthropic_key."""
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
    # Ask for gzip: an old brotli package (1.0.9) breaks the HTTP library's brotli decoding.
    return anthropic.Anthropic(max_retries=4, default_headers={"Accept-Encoding": "gzip, deflate"},
                               **({"api_key": key} if key else {}))


def _header(item: dict) -> str:
    return (f"Outlet: {item['outlet']}\nPublished: {item.get('published', '')}\n"
            f"Title: {item.get('title', '')}\nURL: {item['url']}\n")


def _text_of(resp) -> str:
    return next(b.text for b in resp.content if b.type == "text")


def read_article(client, item: dict) -> dict:
    """-> {"url", "result" in SCAN_SCHEMA's shape, "usage": [calls]} or {"url", "error", "usage"}."""
    calls = []
    # Step 1: Haiku reads the whole article
    resp = client.messages.create(
        model=READ_MODEL,
        max_tokens=16000,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": READ_SCHEMA}},
        system=READ_SYSTEM,
        messages=[{"role": "user", "content": _header(item) + f"\n<article>\n{item['text'][:MAX_ARTICLE_CHARS]}\n</article>"}],
    )
    calls.append(costs.usage_of(resp))
    if resp.stop_reason in ("refusal", "max_tokens"):
        return {"url": item["url"], "error": f"read: {resp.stop_reason}", "usage": calls}
    found = json.loads(_text_of(resp))
    if not found["about_class_suspensions"] or not found["passages"]:
        return {"url": item["url"], "usage": calls,
                "result": {"about_class_suspensions": False, "suspensions": [], "keywords": [],
                           "cited_sources": found["announced_by"]}}

    # Step 2: Sonnet structures only the passages
    passages = "\n".join(f"- {p}" for p in found["passages"])
    with client.beta.messages.stream(
        model=STRUCTURE_MODEL,
        max_tokens=32000,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCAN_SCHEMA}},
        system=SCAN_SYSTEM,
        messages=[{"role": "user", "content": _header(item) + f"\n<passages>\n{passages}\n</passages>"}],
    ) as stream:
        resp = stream.get_final_message()
    calls.append(costs.usage_of(resp))
    if resp.stop_reason in ("refusal", "max_tokens"):
        return {"url": item["url"], "error": f"structure: {resp.stop_reason}", "usage": calls}
    result = json.loads(_text_of(resp))
    result["cited_sources"] = list(dict.fromkeys(result["cited_sources"] + found["announced_by"]))
    return {"url": item["url"], "result": result, "usage": calls}
