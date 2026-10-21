"""
Rappler "walang pasok" roundup scraper — the notebook's scraping cells (Class Suspensions Scraper.ipynb),
unchanged except for running as a script. Free: no AI.

    python rappler_history.py 2026-01-01 2026-10-21 class_suspensions_2026.json
"""
import requests
from bs4 import BeautifulSoup, NavigableString, Tag
import re
import json
import time
import random
from datetime import date, timedelta
tqdm = lambda xs, **kw: xs
from pathlib import Path



DELAY_MIN, DELAY_MAX = 1.5, 3.0

def build_url(d: date) -> str:
    """
    Construct the Rappler article URL for a given date.
    Format: /philippines/class-suspensions-walang-pasok-{month}-{d}-{yyyy}/
    The day is NOT zero-padded (e.g. '5', not '05').
    """
    month_name = d.strftime("%B").lower()  # 'january', 'february', ...
    return (
        f"https://www.rappler.com/philippines/"
        f"class-suspensions-walang-pasok-{month_name}-{d.day}-{d.year}/"
    )



HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

def fetch_page(url: str, timeout: int = 15) -> str | None:
    """
    GET a URL and return its HTML, or None if the page is missing (404)
    or a network error occurs.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        if resp.status_code == 404:
            return None  # No article → no suspensions that day
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        print(f"  ⚠️  Network error for {url}: {e}")
        return None



def extract_article_div(html: str) -> Tag | None:
    """
    Return the BeautifulSoup Tag for the article content div, or None.
    Tries the most-specific selector first, then falls back gracefully.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Primary: must have BOTH classes
    div = soup.find(
        "div",
        class_=lambda c: c and "post-single__content" in c and "entry-content" in c,
    )
    # Fallbacks
    if div is None:
        div = soup.find("div", class_="entry-content")
    if div is None:
        div = soup.find("div", class_="post-single__content")

    return div


ISLANDS = {"luzon", "visayas", "mindanao"}

# Independent Component Cities (ICCs) and Metro Manila cities are autonomous
# from their respective provinces. They are recorded with their own province
# assignment and flagged with is_autonomous = True.
# All HUCs (Highly Urbanized Cities) and ICCs (Independent Component Cities)
# are autonomous from their provinces. Mapped lowercase city name ->
# (province they are grouped under, region).
# HUCs: 16 in NCR + 17 outside NCR = 33 total.
# ICCs: 5 total.
AUTONOMOUS_CITY_MAP = {
    # ── NCR / Metro Manila HUCs (16) ─────────────────────────────────────
    "caloocan city":      ("Metro Manila", "NCR"),
    "las piñas city":     ("Metro Manila", "NCR"),
    "las pinas city":     ("Metro Manila", "NCR"),  # common spelling variant
    "malabon city":       ("Metro Manila", "NCR"),
    "mandaluyong city":   ("Metro Manila", "NCR"),
    "manila":             ("Metro Manila", "NCR"),
    "city of manila":     ("Metro Manila", "NCR"),
    "marikina city":      ("Metro Manila", "NCR"),
    "makati city":        ("Metro Manila", "NCR"),
    "muntinlupa city":    ("Metro Manila", "NCR"),
    "navotas city":       ("Metro Manila", "NCR"),
    "parañaque city":     ("Metro Manila", "NCR"),
    "paranaque city":     ("Metro Manila", "NCR"),  # common spelling variant
    "pasay city":         ("Metro Manila", "NCR"),
    "pasig city":         ("Metro Manila", "NCR"),
    "quezon city":        ("Metro Manila", "NCR"),
    "san juan city":      ("Metro Manila", "NCR"),
    "taguig city":        ("Metro Manila", "NCR"),
    "valenzuela city":    ("Metro Manila", "NCR"),
    # ── HUCs outside NCR (17) ────────────────────────────────────────────
    "angeles city":       ("Pampanga",          "Central Luzon"),
    "baguio city":        ("Benguet",           "CAR"),
    "bacolod city":       ("Negros Occidental", "Negros Island Region"),
    "butuan city":        ("Agusan del Norte",  "Caraga"),
    "cagayan de oro":     ("Misamis Oriental",  "Northern Mindanao"),
    "cagayan de oro city":("Misamis Oriental",  "Northern Mindanao"),
    "cebu city":          ("Cebu",              "Central Visayas"),
    "davao city":         ("Davao del Sur",     "Davao Region"),
    "general santos city":("South Cotabato",    "SOCCSKSARGEN"),
    "iligan city":        ("Lanao del Norte",   "Northern Mindanao"),
    "iloilo city":        ("Iloilo",            "Western Visayas"),
    "lapu-lapu city":     ("Cebu",              "Central Visayas"),
    "lapu lapu city":     ("Cebu",              "Central Visayas"),
    "lucena city":        ("Quezon",            "CALABARZON"),
    "mandaue city":       ("Cebu",              "Central Visayas"),
    "puerto princesa":    ("Palawan",           "MIMAROPA"),
    "puerto princesa city":("Palawan",          "MIMAROPA"),
    "tacloban city":      ("Leyte",             "Eastern Visayas"),
    "zamboanga city":     ("Zamboanga del Sur", "Zamboanga Peninsula"),
    # ── ICCs (5) ─────────────────────────────────────────────────────────
    "cotabato city":      ("Maguindanao del Norte", "BARMM"),
    "dagupan city":       ("Pangasinan",            "Ilocos Region"),
    "santiago city":      ("Isabela",               "Cagayan Valley"),
    "naga city":          ("Camarines Sur",         "Bicol Region"),
    "ormoc city":         ("Leyte",                 "Eastern Visayas"),
}

# Aliases for Metro Manila region — used to detect when a flat <ul> contains
# cities rather than provinces.
METRO_MANILA_ALIASES = {
    "metro manila", "ncr", "national capital region",
    "national capital region (ncr)",
}


# Province / area → (island, region) lookup using the current (2025)
# administrative map as the single source of truth.
# Provinces that changed region over time (Negros Occidental, Negros Oriental,
# Siquijor, Sulu, Maguindanao) are recorded under their current assignment.
PROVINCE_LOCATION_MAP = {
    # ── Metro Manila ──────────────────────────────────────────────────────
    "metro manila":              ("Luzon",    "NCR"),
    "ncr":                       ("Luzon",    "NCR"),
    # ── Ilocos Region ─────────────────────────────────────────────────────
    "ilocos norte":              ("Luzon",    "Ilocos Region"),
    "ilocos sur":                ("Luzon",    "Ilocos Region"),
    "la union":                  ("Luzon",    "Ilocos Region"),
    "pangasinan":                ("Luzon",    "Ilocos Region"),
    # ── Cagayan Valley ────────────────────────────────────────────────────
    "batanes":                   ("Luzon",    "Cagayan Valley"),
    "cagayan":                   ("Luzon",    "Cagayan Valley"),
    "isabela":                   ("Luzon",    "Cagayan Valley"),
    "nueva vizcaya":             ("Luzon",    "Cagayan Valley"),
    "quirino":                   ("Luzon",    "Cagayan Valley"),
    # ── Central Luzon ─────────────────────────────────────────────────────
    "aurora":                    ("Luzon",    "Central Luzon"),
    "bataan":                    ("Luzon",    "Central Luzon"),
    "bulacan":                   ("Luzon",    "Central Luzon"),
    "nueva ecija":               ("Luzon",    "Central Luzon"),
    "pampanga":                  ("Luzon",    "Central Luzon"),
    "tarlac":                    ("Luzon",    "Central Luzon"),
    "zambales":                  ("Luzon",    "Central Luzon"),
    "angeles city":              ("Luzon",    "Central Luzon"),
    # ── CALABARZON ────────────────────────────────────────────────────────
    "batangas":                  ("Luzon",    "CALABARZON"),
    "cavite":                    ("Luzon",    "CALABARZON"),
    "laguna":                    ("Luzon",    "CALABARZON"),
    "quezon":                    ("Luzon",    "CALABARZON"),
    "rizal":                     ("Luzon",    "CALABARZON"),
    # ── MIMAROPA ──────────────────────────────────────────────────────────
    "marinduque":                ("Luzon",    "MIMAROPA"),
    "occidental mindoro":        ("Luzon",    "MIMAROPA"),
    "oriental mindoro":          ("Luzon",    "MIMAROPA"),
    "palawan":                   ("Luzon",    "MIMAROPA"),
    "romblon":                   ("Luzon",    "MIMAROPA"),
    "puerto princesa":           ("Luzon",    "MIMAROPA"),
    # ── Bicol Region ──────────────────────────────────────────────────────
    "albay":                     ("Luzon",    "Bicol Region"),
    "camarines norte":           ("Luzon",    "Bicol Region"),
    "camarines sur":             ("Luzon",    "Bicol Region"),
    "catanduanes":               ("Luzon",    "Bicol Region"),
    "masbate":                   ("Luzon",    "Bicol Region"),
    "sorsogon":                  ("Luzon",    "Bicol Region"),
    # ── CAR ───────────────────────────────────────────────────────────────
    "abra":                      ("Luzon",    "CAR"),
    "apayao":                    ("Luzon",    "CAR"),
    "benguet":                   ("Luzon",    "CAR"),
    "ifugao":                    ("Luzon",    "CAR"),
    "kalinga":                   ("Luzon",    "CAR"),
    "mountain province":         ("Luzon",    "CAR"),
    "baguio city":               ("Luzon",    "CAR"),
    # ── Western Visayas ───────────────────────────────────────────────────
    "aklan":                     ("Visayas",  "Western Visayas"),
    "antique":                   ("Visayas",  "Western Visayas"),
    "capiz":                     ("Visayas",  "Western Visayas"),
    "guimaras":                  ("Visayas",  "Western Visayas"),
    "iloilo":                    ("Visayas",  "Western Visayas"),
    "iloilo city":               ("Visayas",  "Western Visayas"),
    # ── Central Visayas ───────────────────────────────────────────────────
    "bohol":                     ("Visayas",  "Central Visayas"),
    "cebu":                      ("Visayas",  "Central Visayas"),
    "cebu city":                 ("Visayas",  "Central Visayas"),
    # ── Eastern Visayas ───────────────────────────────────────────────────
    "biliran":                   ("Visayas",  "Eastern Visayas"),
    "eastern samar":             ("Visayas",  "Eastern Visayas"),
    "leyte":                     ("Visayas",  "Eastern Visayas"),
    "northern samar":            ("Visayas",  "Eastern Visayas"),
    "samar":                     ("Visayas",  "Eastern Visayas"),
    "southern leyte":            ("Visayas",  "Eastern Visayas"),
    "tacloban city":             ("Visayas",  "Eastern Visayas"),
    # ── Negros Island Region (NIR) — current as of June 2024 ─────────────
    # Negros Occidental + Negros Oriental previously in Western/Central Visayas.
    # Siquijor previously in Central Visayas. All now NIR.
    "negros occidental":         ("Visayas",  "Negros Island Region"),
    "negros oriental":           ("Visayas",  "Negros Island Region"),
    "siquijor":                  ("Visayas",  "Negros Island Region"),
    "bacolod city":              ("Visayas",  "Negros Island Region"),
    "dumaguete city":            ("Visayas",  "Negros Island Region"),
    # ── Zamboanga Peninsula ───────────────────────────────────────────────
    # Sulu transferred from BARMM to Zamboanga Peninsula on 2025-07-30.
    "zamboanga del norte":       ("Mindanao", "Zamboanga Peninsula"),
    "zamboanga del sur":         ("Mindanao", "Zamboanga Peninsula"),
    "zamboanga sibugay":         ("Mindanao", "Zamboanga Peninsula"),
    "zamboanga city":            ("Mindanao", "Zamboanga Peninsula"),
    "isabela city":              ("Mindanao", "Zamboanga Peninsula"),
    "sulu":                      ("Mindanao", "Zamboanga Peninsula"),
    # ── Northern Mindanao ─────────────────────────────────────────────────
    "bukidnon":                  ("Mindanao", "Northern Mindanao"),
    "camiguin":                  ("Mindanao", "Northern Mindanao"),
    "lanao del norte":           ("Mindanao", "Northern Mindanao"),
    "misamis occidental":        ("Mindanao", "Northern Mindanao"),
    "misamis oriental":          ("Mindanao", "Northern Mindanao"),
    "cagayan de oro":            ("Mindanao", "Northern Mindanao"),
    # ── Davao Region ──────────────────────────────────────────────────────
    "davao del norte":           ("Mindanao", "Davao Region"),
    "davao del sur":             ("Mindanao", "Davao Region"),
    "davao de oro":              ("Mindanao", "Davao Region"),
    "davao occidental":          ("Mindanao", "Davao Region"),
    "davao oriental":            ("Mindanao", "Davao Region"),
    "davao city":                ("Mindanao", "Davao Region"),
    # ── SOCCSKSARGEN ──────────────────────────────────────────────────────
    "cotabato":                  ("Mindanao", "SOCCSKSARGEN"),
    "sarangani":                 ("Mindanao", "SOCCSKSARGEN"),
    "south cotabato":            ("Mindanao", "SOCCSKSARGEN"),
    "sultan kudarat":            ("Mindanao", "SOCCSKSARGEN"),
    "general santos city":       ("Mindanao", "SOCCSKSARGEN"),
    # ── Caraga ────────────────────────────────────────────────────────────
    "agusan del norte":          ("Mindanao", "Caraga"),
    "agusan del sur":            ("Mindanao", "Caraga"),
    "dinagat islands":           ("Mindanao", "Caraga"),
    "surigao del norte":         ("Mindanao", "Caraga"),
    "surigao del sur":           ("Mindanao", "Caraga"),
    "butuan city":               ("Mindanao", "Caraga"),
    # ── BARMM ─────────────────────────────────────────────────────────────
    # Maguindanao split into del Norte + del Sur in January 2023.
    # 'maguindanao' kept as a fallback for older articles.
    "basilan":                   ("Mindanao", "BARMM"),
    "tawi-tawi":                 ("Mindanao", "BARMM"),
    "lanao del sur":             ("Mindanao", "BARMM"),
    "maguindanao del norte":     ("Mindanao", "BARMM"),
    "maguindanao del sur":       ("Mindanao", "BARMM"),
    "maguindanao":               ("Mindanao", "BARMM"),
}

# Maps each region name to its island.
# Used to fill in current_island when an article goes straight to region
# headings without an island heading.
REGION_ISLAND_MAP = {
    "Metro Manila":         "Luzon",
    "NCR":                  "Luzon",
    "Ilocos Region":        "Luzon",
    "Cagayan Valley":       "Luzon",
    "Central Luzon":        "Luzon",
    "CALABARZON":           "Luzon",
    "MIMAROPA":             "Luzon",
    "Bicol Region":         "Luzon",
    "CAR":                  "Luzon",
    "Western Visayas":      "Visayas",
    "Central Visayas":      "Visayas",
    "Eastern Visayas":      "Visayas",
    "Negros Island Region": "Visayas",
    "Zamboanga Peninsula":  "Mindanao",
    "Northern Mindanao":    "Mindanao",
    "Davao Region":         "Mindanao",
    "SOCCSKSARGEN":         "Mindanao",
    "Caraga":               "Mindanao",
    "BARMM":                "Mindanao",
}

# Maps raw <h5> region text (any case) to the canonical form used in records.
# Needed because .title() mangles all-caps acronyms like CALABARZON -> Calabarzon.
REGION_NAME_MAP = {
    "calabarzon":                      "CALABARZON",
    "mimaropa":                        "MIMAROPA",
    "soccsksargen":                    "SOCCSKSARGEN",
    "barmm":                           "BARMM",
    "bangsamoro autonomous region in muslim mindanao": "BARMM",
    "car":                             "CAR",
    "cordillera administrative region": "CAR",
    "ncr":                             "NCR",
    "national capital region":         "NCR",
    "national capital region (ncr)":   "NCR",
    "metro manila":                    "NCR",
    "negros island region":            "Negros Island Region",
    "nir":                             "Negros Island Region",
    "bicol":                           "Bicol Region"
}


def lookup_location(name: str) -> tuple:
    # Look up (island, region) for a province/area name.
    # Returns (None, None) if not found.
    # Tries exact match first, then strips common suffixes for fuzzy match.
    key = name.lower().strip()
    if key in PROVINCE_LOCATION_MAP:
        return PROVINCE_LOCATION_MAP[key]
    # Strip 'province', 'city', 'del norte/sur' suffixes for partial match
    for suffix in (" province", " city"):
        stripped = key.replace(suffix, "").strip()
        if stripped in PROVINCE_LOCATION_MAP:
            return PROVINCE_LOCATION_MAP[stripped]
    return (None, None)



def get_place_name(li) -> str:
    # Extract the place name from a <li> tag.
    # Prefers the text of the first <a> direct child.
    # Falls back to direct NavigableString text, then full text minus nested <ul>s.
    from bs4 import NavigableString, Tag

    a_tag = li.find("a", recursive=False)
    if a_tag:
        return a_tag.get_text(strip=True)

    direct_text = "".join(
        str(child) for child in li.children
        if isinstance(child, NavigableString)
    ).strip()

    if not direct_text:
        full_text = li.get_text(separator=" ", strip=True)
        for nested_ul in li.find_all("ul", recursive=False):
            full_text = full_text.replace(
                nested_ul.get_text(separator=" ", strip=True), ""
            ).strip()
        direct_text = full_text

    return direct_text


def get_suspension_info(li) -> str:
    # Extract the info string following the first <a> tag in a <li>.
    # e.g. '<li><a>Cebu City</a> -- all levels (public and private)</li>'
    #      -> 'all levels (public and private)'
    # Returns empty string if no <a> tag is present.
    from bs4 import NavigableString, Tag

    info_parts = []
    past_anchor = False
    for child in li.children:
        if isinstance(child, Tag) and child.name == "a":
            past_anchor = True
            continue
        if past_anchor and isinstance(child, NavigableString):
            info_parts.append(str(child))

    raw = "".join(info_parts).strip()
    return re.sub(r'^[-\u2013\u2014\s]+', '', raw).strip()


def is_summary_ul(ul, content_div) -> bool:
    # Return True if this <ul> is the optional flat province summary list
    # at the top of the article (present in August / September format).
    #
    # Conditions that must ALL be true:
    #   1. No nested <ul>s inside its <li>s
    #   2. No <a> tags on its <li>s (plain text province names only)
    #   3. At least one <h5> heading exists elsewhere in the content div
    #      (if there are no headings at all, this IS the real data)
    from bs4 import Tag
    lis = ul.find_all("li", recursive=False)
    if not lis:
        return False
    has_nested_ul  = any(li.find("ul") for li in lis)
    has_anchor     = any(li.find("a") for li in lis)
    has_h5_sibling = any(
        isinstance(el, Tag) and el.name == "h5"
        for el in content_div.children
    )
    return not has_nested_ul and not has_anchor and has_h5_sibling


def is_ad_div(tag) -> bool:
    # Return True if this tag is a Rappler ad container to be skipped.
    classes = tag.get("class") or []
    return "rappler-ad-container" in classes


def is_stop_element(tag) -> bool:
    # Return True when we have hit trailing non-suspension content.
    # Stops on <h6> (e.g. 'ALSO ON RAPPLER') or <hr> separator.
    return tag.name in ("h6", "hr")


def clean_province_name(name: str) -> str:
    # Remove the word "province" (case-insensitive) and clean up whitespace.
    cleaned = re.sub(r'\bprovince\b', '', name, flags=re.IGNORECASE).strip()
    return ' '.join(cleaned.split())


def make_record(island, region, province, city, info, is_autonomous=False) -> dict:
    # If island is missing but region is known, look it up automatically.
    if island is None and region is not None:
        island = REGION_ISLAND_MAP.get(region)
    return {
        "island":         island,
        "region":         region,
        "province":       province,
        "city":           city,
        "suspension_info": info,
        "is_autonomous":  is_autonomous,
    }



def normalise_region(raw: str) -> str:
    # Convert raw <h5> heading text to canonical region name.
    titled = raw.strip().title()
    return REGION_NAME_MAP.get(titled.lower(), titled)


def parse_province_li(province_li, current_island, current_region) -> list:
    # Parse a single province <li> and return all city records under it.
    #
    # Sub-cases:
    #   A) Province has one or more nested <ul>s -> each nested <li> is a city.
    #   B) Province <li> has no nested <ul>      -> province-wide suspension.
    records = []
    raw_province = get_place_name(province_li)
    province_name = clean_province_name(raw_province)
    city_uls = province_li.find_all("ul", recursive=False)

    if not city_uls:
        # Case B: no nested <ul> -> province-wide
        if province_name:
            info = get_suspension_info(province_li)
            records.append(make_record(
                current_island, current_region,
                province_name, "all cities", info,
            ))
        return records

    # Case A: one or more nested <ul>s -> iterate ALL of them
    for city_ul in city_uls:
        for city_li in city_ul.find_all("li", recursive=False):
            city_name = get_place_name(city_li)
            info      = get_suspension_info(city_li)
            if not city_name:
                continue
            # ICCs inside a province wrapper still get their own province
            auto = AUTONOMOUS_CITY_MAP.get(city_name.lower())
            if auto:
                auto_province, auto_region = auto
                records.append(make_record(
                    current_island, auto_region,
                    auto_province, city_name, info, is_autonomous=True,
                ))
            else:
                records.append(make_record(
                    current_island, current_region,
                    province_name, city_name, info,
                ))

    return records


def parse_suspensions(content_div) -> list:
    # Walk the article content div and return a list of suspension records.
    #
    # Each record:
    #   {
    #     "island":          str | None,
    #     "region":          str | None,
    #     "province":        str | None,
    #     "city":            str,         # place name, or 'all cities'
    #     "suspension_info": str,
    #     "is_autonomous":   bool,        # True for Metro Manila cities and ICCs
    #   }
    #
    # Handles all known structural variants:
    #
    #   Structured (Sep / normal Oct):
    #     <h5 island> -> <h5 region> -> <ul>
    #       <li>Province
    #         <ul><li><a>City</a> info</li></ul>  (one or more <ul>s per province)
    #       </li>
    #     </ul>
    #
    #   Flat (typhoon articles, e.g. Metro Manila):
    #     <h5 region> -> <ul>
    #       <li><a>City</a> info</li>  (cities directly, no province wrapper)
    #     </ul>
    #
    #   Mixed (e.g. Bicol in Oct 22):
    #     <h5 region> -> <ul>
    #       <li>Province <ul>...</ul></li>       (structured province entry)
    #       <li><a>Province/City</a> info</li>   (flat entry)
    #     </ul>
    from bs4 import NavigableString, Tag

    records          = []
    current_island   = None
    current_region   = None
    summary_skipped  = False
    last_p_text      = ""   # text of the most recent <p> seen before a <ul>

    for element in content_div.children:
        if not isinstance(element, Tag):
            continue

        # Skip ad containers injected mid-content
        if is_ad_div(element):
            continue

        # Hard stop on footer elements
        if is_stop_element(element):
            break

        # Skip <p> tags — suspension data is never directly in a <p>.
        # However, in the no-heading format (e.g. August 26 2025), the <p>
        # before the flat <ul> contains the suspension info for all entries
        # (e.g. 'classes in all levels at public and private schools').
        # Capture it so flat no-info entries can use it.
        if element.name == "p":
            last_p_text = element.get_text(separator=" ", strip=True)
            continue

        # Island / region headings
        if element.name == "h5":
            heading_id      = (element.get("id") or "").lower()
            heading_classes = element.get("class") or []
            raw_text        = element.get_text(strip=True)
            slug = re.sub(r'^h-', '', heading_id)

            if slug in ISLANDS or "has-text-align-center" in heading_classes:
                current_island = raw_text.title()
                current_region = None
            else:
                current_region = normalise_region(raw_text)
                # If no island heading was seen yet, look it up from the region
                if current_island is None:
                    current_island = REGION_ISLAND_MAP.get(current_region)
            continue

        # List blocks
        if element.name == "ul":
            # Skip the optional flat summary <ul> at the very top
            if not summary_skipped and is_summary_ul(element, content_div):
                summary_skipped = True
                continue
            summary_skipped = True

            for top_li in element.find_all("li", recursive=False):
                has_nested_ul = bool(top_li.find("ul", recursive=False))

                if has_nested_ul:
                    # Structured entry: top_li is a province wrapper
                    records.extend(
                        parse_province_li(top_li, current_island, current_region)
                    )
                else:
                    # Flat entry: top_li is a city (or province-wide)
                    place_name = get_place_name(top_li)
                    info       = get_suspension_info(top_li)
                    a_tag      = top_li.find("a", recursive=False)

                    if not place_name:
                        continue

                    if a_tag is None and not info:
                        # No <a> and no info -> province-wide suspension.
                        # Use last_p_text as fallback info (covers the
                        # no-heading format where info is in the preceding <p>).
                        # If island/region context is missing (no <h5> headings),
                        # look up the province name in PROVINCE_LOCATION_MAP.
                        is_mm = place_name.lower() in METRO_MANILA_ALIASES
                        province = "Metro Manila" if is_mm else clean_province_name(place_name)
                        isl  = current_island
                        reg  = current_region
                        if isl is None or reg is None:
                            looked_up = lookup_location(province)
                            isl = isl or looked_up[0]
                            reg = reg or looked_up[1]
                        records.append(make_record(
                            isl, reg, province, "all cities", last_p_text,
                        ))
                    else:
                        # Has <a> or info -> check if this is a city or province.
                        #
                        # Outside Metro Manila, flat <li>s with no nested <ul>
                        # are province-level entries (the whole province suspended
                        # but no individual cities were listed). Metro Manila flat
                        # <li>s are always cities since MM has no sub-provinces.
                        #
                        # ICC check always takes priority regardless of region.
                        city_lower = place_name.lower()
                        auto = AUTONOMOUS_CITY_MAP.get(city_lower)
                        if auto:
                            # HUC or ICC — autonomous city with known province
                            auto_province, auto_region = auto
                            records.append(make_record(
                                current_island, auto_region,
                                auto_province, place_name, info,
                                is_autonomous=True,
                            ))
                        elif (current_region or '').lower() in METRO_MANILA_ALIASES:
                            # Region is Metro Manila but city not in AUTONOMOUS_CITY_MAP
                            # (e.g. Pateros, a municipality). Still treat as Metro Manila.
                            records.append(make_record(
                                current_island, current_region,
                                'Metro Manila', place_name, info,
                                is_autonomous=True,
                            ))
                        else:
                            # All other regions: flat <li> is a province-wide entry
                            records.append(make_record(
                                current_island, current_region,
                                clean_province_name(place_name), 'all cities', info,
                            ))

    return records




def daterange(start: date, end: date):
    """Yield every date from start to end inclusive."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def scrape_range(start: date, end: date) -> dict:
    """
    Scrape class suspension data for every date in [start, end].

    Returns:
      {
        "YYYY-MM-DD": {
          "url": str,
          "has_suspensions": bool,
          "suspensions": [
            {
              "island":          str | null,
              "region":          str | null,
              "province":        str,
              "city":            str | null,
              "suspension_info": str
            },
            ...
          ]
        }
      }
    """
    results = {}

    for d in tqdm(list(daterange(start, end)), desc="Scraping dates"):
        date_key = d.isoformat()
        url      = build_url(d)
        html     = fetch_page(url)

        if html is None:
            results[date_key] = {
                "url":             url,
                "has_suspensions": False,
                "scrape_status":   "no_article",
                "warnings":        [],
                "suspensions":     [],
            }
        else:
            content_div = extract_article_div(html)
            if content_div is None:
                records = []
                status  = "no_content_div"
            else:
                records = parse_suspensions(content_div)
                if not records:
                    status = "empty_parse"
                elif all(
                    r["city"] == "all cities" and not r["is_autonomous"]
                    for r in records
                ):
                    # Every record is a province-wide entry with no named cities
                    # and no autonomous cities (Metro Manila / ICCs) — no city-
                    # level detail was available in this article.
                    status = "province_only"
                else:
                    status = "city_level"

            # ── Per-record warnings ───────────────────────────────────────────
            warnings = []
            if status in ("no_content_div", "empty_parse"):
                warnings.append(f"{status}: article was fetched but no records were parsed")
            for rec in records:
                loc = f"{rec['province']} / {rec['city']}"
                if rec["island"] is None:
                    warnings.append(f"missing island context for: {loc}")
                if rec["region"] is None:
                    warnings.append(f"missing region context for: {loc}")
                if not rec["suspension_info"]:
                    warnings.append(f"missing suspension_info for: {loc}")

            results[date_key] = {
                "url":             url,
                "has_suspensions": len(records) > 0,
                "scrape_status":   status,
                "warnings":        warnings,
                "suspensions":     records,
            }

        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    return results




if __name__ == "__main__":
    import sys
    start, end, out = date.fromisoformat(sys.argv[1]), date.fromisoformat(sys.argv[2]), sys.argv[3]
    data = scrape_range(start, end)
    Path(out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(data)} days, {sum(v['has_suspensions'] for v in data.values())} with suspensions, "
          f"{sum(len(v['suspensions']) for v in data.values())} records -> {out}")
