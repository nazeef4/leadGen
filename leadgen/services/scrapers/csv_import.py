"""Manual / CSV import source.

Covers the case where leads come from a bought list, a spreadsheet or an export
from another tool.  Accepts raw CSV text or a list of dicts, maps flexible column
names and validates every email address before it becomes a lead.
"""

from __future__ import annotations

import csv
import io
import re

from .base import BaseScraper, ScrapedLead
from .enrich import is_plausible_email

COLUMN_ALIASES = {
    "business_name": ["business", "business_name", "company", "company name", "name", "businessname", "account"],
    "contact_name": ["contact", "contact_name", "contactname", "first name", "full name", "person", "owner"],
    "email": ["email", "e-mail", "email address", "mail", "emailaddress"],
    "phone": ["phone", "telephone", "tel", "mobile", "cell", "phone number"],
    "website": ["website", "site", "url", "web", "domain", "homepage"],
    "address": ["address", "street", "street address", "address1", "full address"],
    "city": ["city", "town", "suburb", "locality", "location", "city/town"],
    "state": ["state", "province", "region", "state/province", "county"],
    "country": ["country", "nation", "country name"],
    "category": ["category", "industry", "niche", "vertical", "type", "sector"],
    "snippet": ["notes", "note", "description", "snippet", "summary", "details"],
    "rating": ["rating", "stars", "score", "google rating"],
    "review_count": ["reviews", "review_count", "reviewcount", "review count", "ratings"],
    "source_url": ["source", "source_url", "listing", "profile url", "link"],
}


def normalise_header(header: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", (header or "").strip().lower()).strip("_")
    for field, aliases in COLUMN_ALIASES.items():
        if key == field or key in [re.sub(r"[^a-z0-9]+", "_", a) for a in aliases]:
            return field
    return key


class CsvImportScraper(BaseScraper):
    name = "csv"
    requires_key = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_error = ""
        self.skipped: list[dict] = []

    def from_text(self, text: str, limit: int = 5000) -> list[ScrapedLead]:
        reader = csv.DictReader(io.StringIO(text or ""))
        rows = []
        for row in reader:
            rows.append({normalise_header(k): (v or "").strip() for k, v in row.items() if k})
            if len(rows) >= limit:
                break
        if not rows:
            self.last_error = "No rows parsed from the CSV text"
        return self.from_rows(rows)

    def from_rows(self, rows: list[dict]) -> list[ScrapedLead]:
        self.skipped = []
        results: list[ScrapedLead] = []
        for index, row in enumerate(rows):
            email = (row.get("email") or "").strip().lower()
            business = (row.get("business_name") or row.get("name") or "").strip()
            if not email:
                self.skipped.append({"row": index + 2, "reason": "missing email", "data": row})
                continue
            if not is_plausible_email(email):
                self.skipped.append({"row": index + 2, "reason": "invalid email", "data": row})
                continue
            results.append(
                ScrapedLead(
                    business_name=business or email.split("@")[0].title()[:80],
                    contact_name=row.get("contact_name", ""),
                    email=email,
                    phone=row.get("phone", ""),
                    website=row.get("website", ""),
                    address=row.get("address", ""),
                    city=row.get("city", ""),
                    state=row.get("state", ""),
                    country=row.get("country", ""),
                    category=row.get("category", ""),
                    snippet=row.get("snippet", ""),
                    source=self.name,
                    source_url=row.get("source_url", ""),
                    rating=_to_float(row.get("rating")),
                    review_count=_to_int(row.get("review_count")),
                )
            )
        return results


def _to_float(value) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
