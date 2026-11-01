"""
AI class-suspension scraper.

Instead of building one Rappler URL per day, Claude searches Philippine news and
government sources for a given date, reads whatever announcements it finds, and
returns records in the same schema as class_suspensions_YYYY.json.

Two API calls per date, both on Claude Sonnet 5.5:
  1. research  - Claude + web_search/web_fetch, writes plain-text findings with source URLs
  2. structure - Claude turns those findings into JSON records (structured outputs)

Only the major outlets in sources.py are searched, minus Rappler, which opts out of AI tools.
"""

import json
import re
from datetime import date

import anthropic

MODEL = "claude-sonnet-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Sources Claude may search and read. Facebook/X posts can't be fetched, so LGU
# announcements are only picked up once a news outlet or PIA reports them.
NEWS_DOMAINS = [   # sources.py's outlets, in priority order; rappler.com is left out (it opts out of AI tools)
    "pna.gov.ph",
    "deped.gov.ph",
    "gmanetwork.com",
    "abs-cbn.com",
    "inquirer.net",
]

# Region names exactly as they appear in the existing JSON files / map COORDS.
REGIONS = [
    "NCR", "CAR", "Ilocos Region", "Cagayan Valley", "Central Luzon", "CALABARZON",
    "MIMAROPA", "Bicol Region", "Western Visayas", "Central Visayas",
    "Negros Island Region", "Eastern Visayas", "Zamboanga Peninsula",
    "Northern Mindanao", "Davao Region", "SOCCSKSARGEN", "Caraga", "BARMM",
]
ISLANDS = ["Luzon", "Visayas", "Mindanao"]

# Sonnet 5.5 pricing, USD per million tokens / per search
PRICE_IN, PRICE_OUT, PRICE_SEARCH = 2.00, 10.00, 0.01


# ── 1. Research ──────────────────────────────────────────────────────────────

RESEARCH_SYSTEM = """\
You research class suspensions ("walang pasok") in the Philippines for a single date.

Goal: find every class suspension that was IN EFFECT on the target date, from any
cause (typhoon, rain/habagat, flooding, heat index, earthquake, transport strike,
holiday declared by an LGU, etc.), announced by any authority (Malacañang, DepEd,
CHED, governors, mayors, school divisions).

How to search:
- Search in both English and Filipino, e.g. "class suspensions <Month> <day>, <year>",
  "walang pasok <Month> <day> <year>", "suspended classes <Month> <day> <year>",
  plus cause-specific queries ("heat index suspension", "habagat", storm names) once
  you know what was happening that day.
- Announcements are often published the evening BEFORE the target date. Only keep
  suspensions that apply to the target date itself.
- Fetch the actual articles; search snippets are not enough to list cities.
- Live-updated roundup articles (Rappler, GMA, Inquirer) are the richest sources,
  but also look for single-LGU stories they may have missed.

Write up your findings as a plain list, grouped by province, one line per place:
  <province> | <city or municipality, or "all cities" if province-wide> | <levels / details, e.g. "all levels (public and private)", "face-to-face classes, preschool to high school"> | <source URL(s)>
For Metro Manila cities use the province "Metro Manila".
Use the place's official name (e.g. "Quezon City", "City of Manila", "Pateros").
If you find nothing for that date after a thorough search, say "NO SUSPENSIONS FOUND"
and list what you searched. Do not invent places you did not see in a source.
"""


def _research_prompt(d: date) -> str:
    return (
        f"Target date: {d:%A, %B} {d.day}, {d.year} ({d.isoformat()}).\n"
        "Find all class suspensions in effect on this date."
    )


def _add_usage(totals: dict, usage) -> None:
    totals["input_tokens"] += usage.input_tokens or 0
    totals["input_tokens"] += getattr(usage, "cache_read_input_tokens", 0) or 0
    totals["input_tokens"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
    totals["output_tokens"] += usage.output_tokens or 0
    stu = getattr(usage, "server_tool_use", None)
    if stu is not None:
        totals["web_searches"] += getattr(stu, "web_search_requests", 0) or 0


def _check_stop(response, step: str, d: date) -> None:
    if response.stop_reason == "refusal":
        raise RuntimeError(f"{d} {step}: request declined ({getattr(response, 'stop_details', None)})")
    if response.stop_reason == "max_tokens":
        raise RuntimeError(f"{d} {step}: hit max_tokens, output is truncated")


def research_day(client: anthropic.Anthropic, d: date, totals: dict,
                 max_searches: int = 10, max_fetches: int = 15) -> str:
    """Let Claude search the web for the date; return its plain-text findings."""
    tools = [
        {"type": "web_search_20260209", "name": "web_search",
         "max_uses": max_searches, "allowed_domains": NEWS_DOMAINS,
         "user_location": {"type": "approximate", "country": "PH", "timezone": "Asia/Manila"}},
        {"type": "web_fetch_20260209", "name": "web_fetch",
         "max_uses": max_fetches, "allowed_domains": NEWS_DOMAINS},
    ]
    messages = [{"role": "user", "content": _research_prompt(d)}]
    assistant_content = []

    for _ in range(6):  # cap pause_turn resumptions
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=64000,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            output_config={"effort": "high"},
            system=RESEARCH_SYSTEM,
            tools=tools,
            messages=messages + ([{"role": "assistant", "content": assistant_content}]
                                 if assistant_content else []),
        ) as stream:
            response = stream.get_final_message()
        _add_usage(totals, response.usage)
        _check_stop(response, "research", d)
        assistant_content += response.content
        # Server-side search loop hit its iteration limit: re-send to let it resume
        if response.stop_reason != "pause_turn":
            break
    else:
        raise RuntimeError(f"{d} research: still paused after 6 resumptions")

    return "\n".join(b.text for b in assistant_content if b.type == "text").strip()


# ── 2. Structure ─────────────────────────────────────────────────────────────

def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


RECORDS_SCHEMA = {
    "type": "object",
    "properties": {
        "has_suspensions": {"type": "boolean"},
        "suspensions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "island":          _nullable({"type": "string", "enum": ISLANDS}),
                    "region":          _nullable({"type": "string", "enum": REGIONS}),
                    "province":        _nullable({"type": "string"}),
                    "city":            _nullable({"type": "string"}),
                    "suspension_info": {"type": "string"},
                    "is_autonomous":   {"type": "boolean"},
                    "source_urls":     {"type": "array", "items": {"type": "string"}},
                },
                "required": ["island", "region", "province", "city",
                             "suspension_info", "is_autonomous", "source_urls"],
                "additionalProperties": False,
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["has_suspensions", "suspensions", "warnings"],
    "additionalProperties": False,
}

STRUCTURE_SYSTEM = """\
Convert research notes about Philippine class suspensions into JSON records.

Rules:
- One record per place (city/municipality, or "all cities" for a province-wide suspension).
  Merge duplicates of the same place from different sources; combine their source_urls.
- island: Luzon, Visayas or Mindanao. region: one of the allowed region names
  (Metro Manila is "NCR"; Cordillera is "CAR"; Negros Occidental, Negros Oriental and
  Siquijor are "Negros Island Region"). Use null only if truly unknown.
- province: the province the place belongs to; "Metro Manila" for NCR cities.
  Highly urbanized and independent component cities (e.g. Baguio, Iloilo City,
  Cebu City, Naga City) are listed under the province they sit in geographically.
- is_autonomous: true if the city is a highly urbanized city (HUC) or an
  independent component city (ICC), including all Metro Manila cities; else false.
- suspension_info: the levels and details as stated, e.g.
  "all levels (public and private)", "preschool to senior high school (public)",
  "face-to-face classes, all levels". Keep the source's wording where possible.
- Add a warning string for anything uncertain (date ambiguity, conflicting sources,
  place you could not assign to a province).
- If the notes say no suspensions were found, return has_suspensions false and an
  empty list.
"""


def structure_day(client: anthropic.Anthropic, d: date, notes: str, totals: dict) -> dict:
    """Turn the research notes into schema-valid records."""
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=64000,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": RECORDS_SCHEMA},
        },
        system=STRUCTURE_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Target date: {d.isoformat()}\n\n<research_notes>\n{notes}\n</research_notes>",
        }],
    ) as stream:
        response = stream.get_final_message()
    _add_usage(totals, response.usage)
    _check_stop(response, "structure", d)
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


# ── Per-date entry (same shape as the Rappler JSON, plus sources) ────────────

def new_totals() -> dict:
    return {"input_tokens": 0, "output_tokens": 0, "web_searches": 0}


def estimate_cost(totals: dict) -> float:
    return (totals["input_tokens"] * PRICE_IN / 1e6
            + totals["output_tokens"] * PRICE_OUT / 1e6
            + totals["web_searches"] * PRICE_SEARCH)


def scrape_day(client: anthropic.Anthropic, d: date) -> dict:
    totals = new_totals()
    notes = research_day(client, d, totals)
    result = structure_day(client, d, notes, totals)
    return {
        "has_suspensions": bool(result["suspensions"]),
        "scrape_status": "ai_search",
        "warnings": result["warnings"],
        "suspensions": result["suspensions"],
        "research_notes": notes,
        "usage": {**totals, "est_cost_usd": round(estimate_cost(totals), 4)},
    }


# ── Comparison against the Rappler scraper ──────────────────────────────────

def _place_key(rec: dict) -> tuple:
    def norm(s):
        s = (s or "").lower().strip()
        s = re.sub(r"^city of ", "", s)
        s = re.sub(r" city$", "", s)
        return s
    return (norm(rec.get("province")), norm(rec.get("city")))


def compare_with_rappler(ai_entry: dict, rappler_entry: dict | None) -> dict:
    """Which (province, city) pairs each method found for one date."""
    ai = {_place_key(r) for r in ai_entry["suspensions"]}
    rp = {_place_key(r) for r in (rappler_entry or {}).get("suspensions", [])}
    return {
        "both": sorted(ai & rp),
        "only_ai": sorted(ai - rp),
        "only_rappler": sorted(rp - ai),
    }
