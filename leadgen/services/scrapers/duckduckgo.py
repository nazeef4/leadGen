"""DuckDuckGo HTML scraper (no API key required).

Uses the lightweight HTML endpoint (``html.duckduckgo.com/html/``) which is the
same page a browser without JavaScript sees.  Results are business listings;
the enrichment stage then visits each site to recover an email address.

If the network is unavailable or the endpoint changes shape, ``search`` returns
an empty list and records the reason in ``self.last_error`` — it never
fabricates results.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedLead, clean_text

log = logging.getLogger("leadgen.scrape.ddg")

ENDPOINT = "https://html.duckduckgo.com/html/"
RESULT_LIMIT = 30


def unwrap_ddg(href: str) -> str:
    """DDG wraps outbound links in a redirect; pull the real URL back out."""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        query = parse_qs(parsed.query)
        if "uddg" in query:
            return unquote(query["uddg"][0])
    return href


class DuckDuckGoScraper(BaseScraper):
    name = "duckduckgo"
    requires_key = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_error = ""

    def search(self, query: str, limit: int = 20) -> list[ScrapedLead]:
        self.last_error = ""
        html = self.fetch(f"{ENDPOINT}?q={_quote(query)}", check_robots=False)
        if not html:
            self.last_error = f"No response from DuckDuckGo for query {query!r}"
            return []
        results = self.parse_results(html, limit=limit)
        if not results:
            self.last_error = f"No parseable business results for {query!r}"
        return results

    def parse_results(self, html: str, limit: int = 20) -> list[ScrapedLead]:
        """Turn a DuckDuckGo HTML results page into lead records.

        Kept separate from the network call so the parsing can be tested without
        outbound access.
        """
        results: list[ScrapedLead] = []
        soup = BeautifulSoup(html, "lxml")
        for item in soup.select("div.result, div.web-result")[: limit + RESULT_LIMIT]:
            anchor = item.select_one("a.result__a")
            if not anchor:
                continue
            url = unwrap_ddg(anchor.get("href", ""))
            if not url or _is_directory_or_junk(url):
                continue
            snippet_el = item.select_one(".result__snippet, a.result__snippet")
            title = clean_text(anchor.get_text())
            snippet = clean_text(snippet_el.get_text()) if snippet_el else ""
            name = clean_text(re.sub(r"\s*[-|–].*$", "", title)) or clean_text(title)
            if len(name) < 2:
                continue
            parsed = urlparse(url)
            results.append(
                ScrapedLead(
                    business_name=name[:180],
                    website=url,
                    snippet=snippet[:600],
                    source=self.name,
                    source_url=url,
                    category=_guess_category(f"{name} {snippet}"),
                    signals={"domain": parsed.netloc},
                )
            )
            if len(results) >= limit:
                break
        return results


def _quote(query: str) -> str:
    from urllib.parse import quote_plus

    return quote_plus(query)


_DIRECTORY_HOSTS = (
    "facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com",
    "youtube.com", "yelp.com", "tripadvisor.com", "wikipedia.org", "reddit.com",
    "amazon.com", "pinterest.com", "tiktok.com", "glassdoor.com", "indeed.com",
    "quora.com", "medium.com", "apple.com", "google.", "duckduckgo.com",
)


def _is_directory_or_junk(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) or d in host for d in _DIRECTORY_HOSTS)


_CATEGORY_HINTS = [
    ("hvac", "HVAC contractor"),
    ("air conditioning", "Air conditioning service"),
    ("heating", "Heating contractor"),
    ("roofing", "Roofing contractor"),
    ("plumbing", "Plumbing service"),
    ("electrical", "Electrical contractor"),
    ("landscap", "Landscaping company"),
    ("pool", "Pool service"),
    ("pest", "Pest control service"),
    ("cleaning", "Cleaning service"),
    ("solar", "Solar energy company"),
    ("dental", "Dental practice"),
    ("clinic", "Medical clinic"),
    ("restaurant", "Restaurant"),
    ("cafe", "Cafe"),
    ("hotel", "Hotel"),
    ("gym", "Fitness centre"),
    ("law", "Law firm"),
    ("accounting", "Accounting firm"),
    ("logistics", "Logistics company"),
    ("freight", "Freight forwarder"),
    ("it support", "IT support company"),
    ("marketing", "Marketing agency"),
    ("web design", "Web design company"),
    ("auto", "Auto repair shop"),
    ("veterinar", "Veterinary clinic"),
    ("school", "School"),
    ("real estate", "Real estate agency"),
    ("construction", "Construction company"),
]


def _guess_category(text: str) -> str:
    lowered = (text or "").lower()
    for needle, category in _CATEGORY_HINTS:
        if needle in lowered:
            return category
    return ""
