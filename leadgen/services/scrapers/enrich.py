"""Lead enrichment: email discovery from a website, MX validation and scoring."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import dns.resolver

from ...config import Settings, get_settings
from .base import BaseScraper, ScrapedLead

log = logging.getLogger("leadgen.enrich")

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s\-.]?)?(?:\(\d{2,4}\)[\s\-.]?)?\d{3,4}[\s\-.]?\d{3,4}(?:[\s\-.]?\d{2,4})?"
)
MAILTO_RE = re.compile(r"mailto:([^\"'?>\s]+)", re.IGNORECASE)
SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)

# Addresses that are almost never a decision maker, and domains that are never
# a real business inbox.
ROLE_PREFIXES = {
    "info", "sales", "contact", "hello", "admin", "office", "support", "service",
    "enquiry", "enquiries", "inquiry", "inquiries", "help", "team", "mail", "email",
    "welcome", "general", "reception", "frontdesk", "hello@", "noreply", "no-reply",
    "donotreply", "webmaster", "postmaster", "abuse", "privacy", "legal", "billing",
    "accounts", "marketing", "hr", "jobs", "careers", "media", "press", "news",
}
GOOD_PREFIX_HINTS = {"owner", "director", "manager", "founder", "ceo", "gm", "principal"}
JUNK_DOMAINS = {
    "sentry.io", "wixpress.com", "example.com", "example.org", "domain.com",
    "email.com", "test.com", "yourdomain.com", "site.com", "godaddy.com",
    "w3.org", "schema.org", "googleapis.com", "gstatic.com", "gravatar.com",
    "cloudflare.com", "facebook.com", "instagram.com", "twitter.com", "linkedin.com",
}
JUNK_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js", ".ico", ".bmp"}


@dataclass
class EmailCandidate:
    address: str
    kind: str  # personal | role | unknown
    score: int


def is_plausible_email(address: str) -> bool:
    address = (address or "").strip().lower()
    if not EMAIL_RE.fullmatch(address):
        return False
    local, _, domain = address.partition("@")
    if not local or not domain or len(address) > 254:
        return False
    if any(address.lower().endswith(ext) for ext in JUNK_EXT):
        return False
    if domain in JUNK_DOMAINS:
        return False
    if ".." in local or local.startswith(".") or local.endswith("."):
        return False
    if ".." in domain or domain.startswith(".") or domain.endswith("."):
        return False
    if "." not in domain:
        return False
    return True


def classify_email(address: str) -> EmailCandidate:
    address = address.strip().lower()
    local, _, domain = address.partition("@")
    if local in ROLE_PREFIXES or any(local.startswith(p) for p in ("info", "sales", "office", "admin")):
        return EmailCandidate(address, "role", 55)
    if local in GOOD_PREFIX_HINTS:
        return EmailCandidate(address, "role", 70)
    if re.fullmatch(r"[a-z]+\.?[a-z]+", local) and len(local) > 3:
        return EmailCandidate(address, "personal", 88)
    return EmailCandidate(address, "unknown", 60)


def extract_emails(html: str, limit: int = 12) -> list[EmailCandidate]:
    """Pull candidate addresses out of a page, best-first."""
    if not html:
        return []
    text = SCRIPT_RE.sub(" ", html)
    raw: list[str] = []
    raw += [m.strip().lower() for m in MAILTO_RE.findall(html)]
    raw += [m.lower() for m in EMAIL_RE.findall(text)]
    seen: set[str] = set()
    out: list[EmailCandidate] = []
    for address in raw:
        if not is_plausible_email(address) or address in seen:
            continue
        seen.add(address)
        out.append(classify_email(address))
        if len(out) >= limit:
            break
    out.sort(key=lambda c: -c.score)
    return out


def extract_phone(html: str) -> str:
    if not html:
        return ""
    text = SCRIPT_RE.sub(" ", html)
    match = re.search(r"(?:tel:)([+\d][\d\s\-().]{6,20})", text)
    if match:
        return match.group(1).strip()
    for candidate in PHONE_RE.findall(text):
        digits = re.sub(r"\D", "", candidate)
        if 8 <= len(digits) <= 15:
            return candidate.strip()
    return ""


_mx_cache: dict[str, bool] = {}


def domain_has_mx(domain: str, timeout: float = 4.0) -> bool:
    """Cheap deliverability pre-check.  Cached per domain per process."""
    domain = domain.lower().strip()
    if domain in _mx_cache:
        return _mx_cache[domain]
    try:
        resolver = dns.resolver.Resolver(configure=True)
        resolver.lifetime = timeout
        answers = resolver.resolve(domain, "MX")
        ok = len(answers) > 0
    except Exception:
        # No DNS answer is not proof of an invalid domain (offline sandbox,
        # blocked UDP).  Treat unknown as "probably fine" and let SMTP decide.
        ok = True
    _mx_cache[domain] = ok
    return ok


def clear_mx_cache() -> None:
    _mx_cache.clear()


def score_lead(
    lead: ScrapedLead,
    *,
    mx_ok: bool | None = None,
    buyer_signals: list[str] | None = None,
    target_cities: set[str] | None = None,
) -> tuple[int, dict]:
    """0-100 targeting score plus the reasons behind it."""
    score = 20
    reasons: dict[str, object] = {}

    if lead.email:
        kind = classify_email(lead.email).kind
        base = {"personal": 32, "role": 22, "unknown": 26}.get(kind, 22)
        score += base
        reasons["emailKind"] = kind
        if mx_ok is False:
            score -= 25
            reasons["mxCheck"] = "failed"
        elif mx_ok:
            score += 4
            reasons["mxCheck"] = "passed"
    else:
        reasons["emailKind"] = "none"
        score -= 10

    if lead.website:
        score += 8
    if lead.phone:
        score += 4
    if lead.rating is not None:
        if lead.rating >= 4.5:
            score += 10
        elif lead.rating >= 4.0:
            score += 6
        elif lead.rating < 3.5:
            score -= 5
        reasons["rating"] = lead.rating
    if lead.review_count:
        if lead.review_count >= 100:
            score += 8
        elif lead.review_count >= 20:
            score += 5
        reasons["reviewCount"] = lead.review_count

    haystack = f"{lead.snippet} {lead.business_name} {lead.category}".lower()
    matched = [s for s in (buyer_signals or []) if s.lower() in haystack]
    if matched:
        score += min(len(matched) * 4, 12)
        reasons["buyerSignals"] = matched

    if target_cities and lead.city and lead.city.lower() in {c.lower() for c in target_cities}:
        score += 8
        reasons["cityMatch"] = True

    return max(0, min(100, score)), reasons


class Enricher:
    """Visits a lead's website to recover an email address and phone number."""

    def __init__(self, scraper: BaseScraper | None = None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.scraper = scraper or BaseScraper(self.settings)

    def normalise_website(self, website: str) -> str:
        website = (website or "").strip()
        if not website:
            return ""
        if not website.startswith(("http://", "https://")):
            website = "https://" + website
        return website

    def enrich(self, lead: ScrapedLead, fetch_pages: bool = True) -> ScrapedLead:
        lead.website = self.normalise_website(lead.website)
        if not fetch_pages or not lead.website:
            if lead.email:
                lead.signals["emailKind"] = classify_email(lead.email).kind
            return lead
        html = self.scraper.fetch(lead.website)
        if not html:
            lead.signals["enrichError"] = "site unreachable"
            return lead
        candidates = extract_emails(html)
        if candidates and not lead.email:
            lead.email = candidates[0].address
        if candidates:
            lead.signals["emailKind"] = candidates[0].kind
            lead.signals["emailCandidates"] = [c.address for c in candidates[:5]]
        if not lead.phone:
            lead.phone = extract_phone(html)
        if lead.email and self.settings.verify_mx_records:
            domain = lead.email.split("@")[-1]
            lead.signals["mxValid"] = domain_has_mx(domain)
        return lead
