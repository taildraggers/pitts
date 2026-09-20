"""Scraper for Pitts Special listings on barnstormers.com.

This searches Barnstormers' advanced headline search for "pitts" rather
than the site's single-manufacturer Pitts biplane category page it used
previously (category-17053-Biplane--Pitts.html). Both a category page
(the pattern seen in the companion Aviat, CubCrafters, de Havilland,
Maule, Van's RV, RANS, Luscombe, Just Aircraft, Kitfox, Bellanca,
Stearman, and Waco repos) and a headline search can mix in off-brand or
off-topic listings with no distinguishing HTML markup from the genuine
ones - if anything a keyword search is looser, since it can surface ads
that only mention "Pitts" in passing rather than being scoped to the
make at all. So results are filtered by title against a small allowlist
of Pitts-specific terms before being published, same as before.

Pitts model codes ("S-1", "S-2", and their letter suffixes - S-1C, S-1S,
S-1T, S-2A, S-2B, S-2C, S-2S, etc.) are short and generic-looking enough
(bare "S1"/"S2" could collide with all sorts of unrelated part numbers or
abbreviations) that, unlike RANS's S-numbers or Luscombe's 8-series codes,
they are NOT trusted standalone - every model match here requires the
title to also say "Pitts" explicitly (the same lesson learned the hard
way in the companion Piper repo, where a bare "Cub" mislabeled non-Piper
homebuilts as genuine Pipers). On top of that, and unlike every repo
before Stearman/Waco, a bare mention of "Pitts" with no specific model
code stated is enough on its own to publish too, since plenty of genuine
listings just say "Pitts Special" or "Pitts biplane" without stating an
exact variant.

Titles that read as parts, accessories, services, or raffles are still
dropped regardless. Surviving titles are rewritten to a canonical "YEAR
PITTS MODEL" form when the ad states a model year and a specific model,
"YEAR Pitts" when only the model is missing, "PITTS MODEL" when only the
year is missing, or plain "Pitts" when neither is stated.

All Pitts Special models share the same fixed, non-retractable tailwheel
gear by design - there is no tricycle-gear Pitts - so no categorical gear
exclusion is needed here (unlike RANS S-19, Luscombe 11E, Kitfox Vixen/
Voyager, or Van's RV's "A"-suffix models). The standard text-based
tricycle/nosewheel safety net used in those repos is still applied to
every listing as a general precaution.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from .common import (
    Listing,
    extract_date,
    extract_location,
    extract_price,
    fetch,
    format_aircraft_title,
)

SITE_NAME = "Barnstormers.com"
BASE = "https://www.barnstormers.com"
MAKE = "Pitts"

# Barnstormers' advanced-search results for classified headlines containing
# "pitts", used in place of the site's Pitts biplane category page (which
# this previously pointed at via category-17053-Biplane--Pitts.html) - the
# existing TARGET_MODEL_PHRASES/_BRAND_RE allowlist filtering below already
# assumes off-topic results can slip in unfiltered, so it applies equally
# well to a keyword search as it did to the category page.
SEARCH_URLS = [
    f"{BASE}/cat_search.php?headline=pitts&body=&part_num=&mfg=&model="
    "&user__profile__company=&user__last_name=&user__first_name="
    "&user__profile__country=&specialcase__state=&user__profile__city="
    "&user__profile__uzip=&specialcase__phone=&user__email=&my_cats__name="
    "&price__gte=&price__lte=&search_type=advanced&keyword=",
]

MAX_PAGES = 10
LISTING_LINK_RE = re.compile(r"^/classified-(\d+)-(.+)\.html$")
GENERIC_SITE_TITLE_SNIPPET = "barnstormers.com find aircraft"


def _compact(text: str) -> str:
    return re.sub(r"[\s-]", "", text.lower())


# "Pitts" is the only coarse-gate phrase used - the short model codes
# below carry too much substring-collision risk to use safely as a coarse
# filter. The fine-grained _extract_model has its own word-boundary-safe
# checks and, more importantly, requires "pitts" too.
TARGET_MODEL_PHRASES = ["pitts"]


def _matches_target_models(title: str) -> bool:
    compact = _compact(title)
    return any(phrase in compact for phrase in TARGET_MODEL_PHRASES)


_BRAND_RE = re.compile(r"\bpitts\b", re.IGNORECASE)

_MARKETING_NAME_RULES = [
    (re.compile(r"\bsuper\s*stinker\b", re.IGNORECASE), "Super Stinker"),
    (re.compile(r"\bmodel\s*12\b", re.IGNORECASE), "Model 12"),
    # "Pitts 12" with no "Model" stated is a real title pattern that would
    # otherwise fall through to a bare "Pitts" match, losing the "12"
    # designation - anchored to immediately follow "pitts" (not a bare
    # \b12\b scanned over the whole title) so it can't misfire on an
    # unrelated "12" elsewhere in the title (a price, hours, a tail number).
    (re.compile(r"\bpitts\s*12\b", re.IGNORECASE), "Model 12"),
    (re.compile(r"\bpython\b", re.IGNORECASE), "Model 12"),
]

# S-1/S-2 plus an optional 0-2 letter trim suffix (S-1C, S-1S, S-1T,
# S-1SS, S-2A, S-2B, S-2C, S-2S). The prefix and number may be separated
# by a space, a hyphen, or nothing, since _title_from_url() turns the
# source URL's hyphens into spaces.
_MODEL_CODE_RE = re.compile(r"\bs[\s-]?([12])[\s-]?([a-z]{0,2})\b", re.IGNORECASE)


def _extract_model(title: str) -> tuple[str, str] | None:
    if not _BRAND_RE.search(title):
        return None

    for pattern, canonical in _MARKETING_NAME_RULES:
        if pattern.search(title):
            return MAKE, canonical

    match = _MODEL_CODE_RE.search(title)
    if match:
        number, suffix = match.groups()
        return MAKE, f"S-{number}{suffix.upper()}"

    return MAKE, ""


# Ads whose title or body text explicitly calls out tricycle/nosewheel gear
# are dropped, regardless of which model they are - see module docstring.
_NON_TAILWHEEL_KEYWORDS = (
    "tricycle gear",
    "tricycle landing gear",
    "trike gear",
    "tri-gear",
    "tri gear",
    "nosewheel",
    "nose wheel",
    "nose-wheel",
)


def _is_non_tailwheel(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in _NON_TAILWHEEL_KEYWORDS)


def _page_url(search_url: str, page: int) -> str:
    """Build a search results page's URL directly.

    Unlike Barnstormers' category pages (which paginate via a documented
    ?seocategory=<path>&page=<n> pattern - see the companion Van's RV,
    Stearman, and Waco repos, or this repo's own prior category-page
    version), this environment couldn't reach barnstormers.com to confirm
    the equivalent for cat_search.php results (see the companion Legend
    repo, which hit the same limitation), so this appends the same
    "&page=<n>" convention used elsewhere on the site as a best effort. If
    that guess is wrong, the "no new links" check in scrape() below still
    stops the loop safely after page 2 rather than looping or duplicating
    results - it just means only page 1 gets scraped.
    """
    if page <= 1:
        return search_url
    return f"{search_url}&page={page}"


def _title_from_url(url: str) -> str:
    """Listing pages share a generic <title>/<h1>, but the URL slug is the ad's own title."""
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    match = LISTING_LINK_RE.match("/" + slug)
    if not match:
        return unquote(slug)
    return unquote(match.group(2)).replace("-", " ").strip()


def _find_listing_links(html: str) -> set[str]:
    soup = BeautifulSoup(html, "lxml")
    links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0]
        if LISTING_LINK_RE.match(href):
            links.add(urljoin(BASE, href))
    return links


def _debug_dump_hrefs(html: str, limit: int = 25) -> None:
    soup = BeautifulSoup(html, "lxml")
    hrefs = [a["href"] for a in soup.find_all("a", href=True)]
    interesting = [h for h in hrefs if "classified" in h.lower() or "pitts" in h.lower()]
    sample = interesting[:limit] or hrefs[:limit]
    print(f"  [debug] {len(hrefs)} total <a href> on page; sample: {sample}")


def _parse_detail_page(url: str, html: str) -> Listing | None:
    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None
    if title:
        title = re.sub(r"\s*[\|\-]\s*Barnstormers.*$", "", title, flags=re.IGNORECASE).strip()
    if not title or GENERIC_SITE_TITLE_SNIPPET in title.lower():
        title = _title_from_url(url)
    if not title:
        return None

    if not _matches_target_models(title):
        return None

    text = soup.get_text(" ", strip=True)

    if _is_non_tailwheel(title) or _is_non_tailwheel(text):
        return None

    formatted_title = format_aircraft_title(title, text, _extract_model)
    if not formatted_title:
        return None
    # A bare-"Pitts" match (no specific model code) leaves a trailing
    # space from format_aircraft_title's "{make} {model}" join, since
    # _extract_model returns an empty model string in that case.
    title = formatted_title.rstrip()

    price = extract_price(text)
    location = extract_location(text)
    date_posted = extract_date(text)

    return Listing(
        title=title,
        price=price,
        location=location,
        date_posted=date_posted,
        site=SITE_NAME,
        url=url,
    )


def scrape() -> list[Listing]:
    print(f"[{SITE_NAME}] starting scrape")
    all_links: set[str] = set()

    for search_url in SEARCH_URLS:
        seen_this_search: set[str] = set()
        for page in range(1, MAX_PAGES + 1):
            url = _page_url(search_url, page)
            html = fetch(url)
            if not html:
                break
            links = _find_listing_links(html)
            new_links = links - seen_this_search
            print(f"  [{search_url}] page {page}: {len(links)} links ({len(new_links)} new)")
            if page == 1 and not links:
                _debug_dump_hrefs(html)
            seen_this_search |= links
            if not new_links:
                break
        all_links |= seen_this_search

    print(f"[{SITE_NAME}] {len(all_links)} unique listing URLs found")

    candidate_links = {url for url in all_links if _matches_target_models(_title_from_url(url))}
    print(f"[{SITE_NAME}] {len(candidate_links)} match Pitts product names")

    listings: list[Listing] = []
    for url in sorted(candidate_links):
        html = fetch(url)
        if not html:
            continue
        listing = _parse_detail_page(url, html)
        if listing:
            listings.append(listing)

    print(f"[{SITE_NAME}] parsed {len(listings)} listings")
    return listings
