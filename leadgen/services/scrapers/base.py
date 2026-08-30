"""Scraper framework.

Scrapers are pluggable.  Each one returns :class:`ScrapedLead` records; the
pipeline then de-duplicates, enriches and scores them.

Two rules every scraper here follows:

* **politeness** — a randomised delay between requests, a real User-Agent, an
  honourable timeout, ``robots.txt`` checks and a hard per-campaign page cap;
* **honesty** — a scraper that cannot reach the network returns zero results
  with an error message rather than inventing data.  Deterministic demo data is
  available only through the explicitly named :mod:`leadgen.services.scrapers.demo`
  scraper, which is labelled in the UI.
"""

from __future__ import annotations

import logging
import random
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from ...config import Settings, get_settings

log = logging.getLogger("leadgen.scrape")


@dataclass
class ScrapedLead:
    business_name: str
    email: str = ""
    website: str = ""
    phone: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    category: str = ""
    contact_name: str = ""
    snippet: str = ""
    source: str = ""
    source_url: str = ""
    rating: float | None = None
    review_count: int | None = None
    signals: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    def dedupe_key(self) -> str:
        if self.email:
            return f"email:{self.email.lower().strip()}"
        if self.website:
            return f"site:{urlparse(self.website).netloc.lower().removeprefix('www.')}"
        return f"name:{(self.business_name or '').lower().strip()}|{self.city.lower()}"

    def to_dict(self) -> dict:
        return {
            "business_name": self.business_name,
            "email": self.email,
            "website": self.website,
            "phone": self.phone,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "country": self.country,
            "category": self.category,
            "contact_name": self.contact_name,
            "snippet": self.snippet,
            "source": self.source,
            "source_url": self.source_url,
            "rating": self.rating,
            "review_count": self.review_count,
            "signals": self.signals,
        }


class ScraperError(RuntimeError):
    pass


class BaseScraper:
    name = "base"
    requires_key = False

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last_request = 0.0

    # ------------------------------------------------------------- helpers
    @property
    def available(self) -> bool:
        return True

    def wait_polite(self, rng: random.Random | None = None) -> None:
        rng = rng or random.Random()
        elapsed = time.monotonic() - self._last_request
        delay = rng.uniform(self.settings.scrape_delay_min, self.settings.scrape_delay_max)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request = time.monotonic()

    def robots_allows(self, url: str, user_agent: str | None = None) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        parser = self._robots.get(origin)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(f"{origin}/robots.txt")
            try:
                parser.read()
            except Exception:  # unreachable robots.txt -> assume allowed
                log.debug("robots.txt unreadable for %s", origin)
            self._robots[origin] = parser
        try:
            return parser.can_fetch(user_agent or self.settings.scrape_user_agent, url)
        except Exception:  # pragma: no cover
            return True

    def client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.settings.scrape_request_timeout,
            headers={
                "user-agent": self.settings.scrape_user_agent,
                "accept-language": "en-US,en;q=0.9",
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            follow_redirects=True,
        )

    def fetch(self, url: str, check_robots: bool = True) -> str | None:
        """Fetch a page, honouring robots.txt and the polite delay."""
        if check_robots and not self.robots_allows(url):
            log.info("robots.txt disallows %s", url)
            return None
        self.wait_polite()
        try:
            with self.client() as client:
                res = client.get(url)
                res.raise_for_status()
                return res.text
        except httpx.HTTPError as exc:
            log.info("fetch failed for %s: %s", url, exc)
            return None

    # -------------------------------------------------------------- search
    def search(self, query: str, limit: int = 20) -> list[ScrapedLead]:  # pragma: no cover
        raise NotImplementedError


def clean_text(value: str | None) -> str:
    return " ".join((value or "").split())
