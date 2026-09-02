"""Policy-compliance guardrails for outbound email.

Two families of rules:

1. **Content hygiene** — spam-phrase scoring, subject length, shoutiness, link
   density, text/HTML balance and the legally required elements (a valid postal
   address and a working opt-out, per CAN-SPAM / CASL / PECR style rules).

2. **Sending behaviour** — Google's volume ceilings (free accounts: 500
   recipients/day, so we cap at 400), hourly burst limits, minimum gap between
   sends, quiet hours, suppression-list enforcement and per-domain concentration
   limits.  A ``block`` severity issue stops the dispatcher; ``warn`` lowers the
   campaign's compliance score.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import Settings, get_settings

RULES_PATH = Path(__file__).resolve().parents[1] / "data" / "spam_rules.json"

OPT_OUT_RE = re.compile(
    r"(unsubscrib\w*|opt[\s-]?out|opt out|remove me from|"
    r"reply\s+(?:with\s+)?stop|no longer (?:wish|want)|"
    r"stop (?:contacting|emailing)|don't email me|do not email me|"
    r"manage (?:my|your) (?:email )?preferences)",
    re.IGNORECASE,
)
ADDRESS_RE = re.compile(
    r"(\d{1,6}\s+[\w.'\- ]{2,40}\s+"
    r"(?:street|st|road|rd|avenue|ave|boulevard|blvd|drive|dr|lane|ln|way|court|ct|"
    r"place|pl|highway|hwy|parkway|pkwy|suite|ste|floor|fl|unit|building|bldg)\b"
    r"|p\.?\s?o\.?\s?box\s+\d+)",
    re.IGNORECASE,
)
CAPS_WORD_RE = re.compile(r"\b[A-Z]{4,}\b")
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
IMG_RE = re.compile(r"<img\b", re.IGNORECASE)


@dataclass
class ComplianceIssue:
    code: str
    severity: str  # block | warn | info
    message: str

    def to_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "message": self.message}


@dataclass
class ComplianceReport:
    score: int = 100
    issues: list[ComplianceIssue] = field(default_factory=list)
    checks: dict = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return any(i.severity == "block" for i in self.issues)

    @property
    def warnings(self) -> list[ComplianceIssue]:
        return [i for i in self.issues if i.severity == "warn"]

    def add(self, code: str, severity: str, message: str, penalty: int) -> None:
        self.issues.append(ComplianceIssue(code, severity, message))
        self.score = max(0, self.score - penalty)

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "blocked": self.blocked,
            "issues": [i.to_dict() for i in self.issues],
            "checks": self.checks,
        }


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", TAG_RE.sub(" ", text or "")))


def as_utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; arithmetic with aware ones raises.

    Everything we write is UTC, so a naive value is interpreted as UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Compile a spam phrase into a word-boundary-aware matcher.

    Naive substring matching produces false positives that matter in exactly the
    verticals this app targets: ``trial`` matches "indusTRIAL", ``prize`` matches
    "prized", ``urgent`` matches "urgently". Anchoring on word boundaries fixes
    that, while symbol phrases like ``$$$`` and ``!!!`` keep plain substring
    semantics because they have no word boundary to anchor to.
    """
    escaped = re.escape(phrase)
    prefix = r"\b" if phrase[:1].isalnum() else ""
    suffix = r"\b" if phrase[-1:].isalnum() else ""
    return re.compile(f"{prefix}{escaped}{suffix}", re.IGNORECASE)


# Acronyms that are normal in B2B copy and must never count as "shouting".
# Without these, an HVAC campaign is penalised for writing "HVAC" and every
# email is penalised for the mandatory "Reply STOP" opt-out line.
DEFAULT_ALLOWED_CAPS = {
    "AC", "API", "AR", "AP", "ASAP", "ASHRAE", "B2B", "B2C", "BIM", "CAD", "CCPA",
    "CDL", "CEO", "CFO", "COO", "CPA", "CRM", "CSV", "DNS", "DOT", "EIA", "EMEA",
    "EOD", "EOM", "EPA", "ERP", "ETL", "EUR", "FAQ", "FDA", "FMCSA", "FTA", "GBP",
    "GDPR", "GMT", "GST", "HIPAA", "HR", "HTML", "HTTP", "HTTPS", "HVAC", "ISO",
    "IT", "JSON", "KPI", "LAN", "LLC", "LTD", "MEP", "MLM", "MSP", "NATE", "NDA",
    "OSHA", "PCI", "PDF", "PM", "PO", "PSA", "QA", "QC", "RFQ", "RFP", "ROI", "SAAS",
    "SEO", "SLA", "SOC", "SQL", "SSL", "TLS", "TOS", "URL", "USD", "USP", "UTC",
    "VAT", "VPN", "WAN", "W2", "W9", "XML", "STOP",  # STOP: the required opt-out keyword
}


class ComplianceEngine:
    def __init__(self, rules_path: Path | None = None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.rules_path = rules_path or RULES_PATH
        self.rules = self._load_rules()
        self.allowed_caps_words: set[str] = {
            w.upper() for w in self.rules.get("allowedCapsWords", DEFAULT_ALLOWED_CAPS)
        }
        self.spam_phrases: list[str] = [p.lower() for p in self.rules.get("spamPhrases", [])]
        self.spam_patterns: list[tuple[str, re.Pattern[str]]] = [
            (phrase, _phrase_pattern(phrase)) for phrase in self.spam_phrases
        ]

    def find_spam_phrases(self, subject: str, body: str) -> list[str]:
        """Return the spam-trigger phrases present in the given copy."""
        haystack = f"{subject} {body}"
        return [phrase for phrase, pattern in self.spam_patterns if pattern.search(haystack)]

    def _load_rules(self) -> dict:
        try:
            return json.loads(self.rules_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):  # pragma: no cover - shipped with app
            return {
                "spamPhrases": [],
                "capsWordsThreshold": 3,
                "maxExclamationMarks": 1,
                "maxSubjectLength": 70,
                "idealSubjectLength": 45,
                "maxLinks": 3,
                "minBodyWords": 40,
                "maxBodyWords": 220,
            }

    # ------------------------------------------------------------ content
    def check_content(self, subject: str, body_text: str, body_html: str = "") -> ComplianceReport:
        report = ComplianceReport()
        subject = subject or ""
        body = body_text or ""
        html = body_html or ""

        report.checks["subjectLength"] = len(subject)
        report.checks["bodyWords"] = _word_count(body)

        if not subject.strip():
            report.add("subject_empty", "block", "Subject line is empty.", 30)
        elif len(subject) > self.rules.get("maxSubjectLength", 70):
            report.add(
                "subject_long",
                "warn",
                f"Subject is {len(subject)} chars — mobile clients truncate past ~60.",
                6,
            )
        if subject.isupper() and len(subject) > 8:
            report.add("subject_shouting", "warn", "Subject is entirely upper case.", 8)

        words = _word_count(body)
        if words < self.rules.get("minBodyWords", 40):
            report.add(
                "body_too_short",
                "warn",
                f"Body has {words} words — very short mail reads as bulk spam.",
                8,
            )
        if words > self.rules.get("maxBodyWords", 220):
            report.add(
                "body_too_long",
                "info",
                f"Body has {words} words — cold mail converts better under ~200.",
                2,
            )

        # legally required elements
        if not OPT_OUT_RE.search(body) and not OPT_OUT_RE.search(html):
            report.add(
                "no_opt_out",
                "block",
                "No opt-out / unsubscribe language found (required by CAN-SPAM & CASL).",
                25,
            )
        else:
            report.checks["optOut"] = True
        if not ADDRESS_RE.search(body) and not ADDRESS_RE.search(html):
            report.add(
                "no_postal_address",
                "block",
                "No physical postal address found (required by CAN-SPAM §5(a)(3)).",
                25,
            )
        else:
            report.checks["postalAddress"] = True

        # spam phrase scoring (word-boundary aware; see _phrase_pattern)
        hits = self.find_spam_phrases(subject, body)
        report.checks["spamPhraseHits"] = hits[:10]
        if len(hits) >= 4:
            report.add(
                "spam_phrases",
                "block",
                f"{len(hits)} spam-trigger phrases detected: {', '.join(hits[:5])}…",
                25,
            )
        elif hits:
            report.add(
                "spam_phrases",
                "warn",
                f"{len(hits)} spam-trigger phrase(s): {', '.join(hits[:5])}",
                min(4 * len(hits), 16),
            )

        caps = [w for w in CAPS_WORD_RE.findall(body) if w not in self.allowed_caps_words]
        report.checks["capsWords"] = len(caps)
        if len(caps) > self.rules.get("capsWordsThreshold", 3):
            report.add("shouting", "warn", f"{len(caps)} ALL-CAPS words in the body.", 8)

        bangs = subject.count("!") + body.count("!")
        report.checks["exclamationMarks"] = bangs
        if bangs > self.rules.get("maxExclamationMarks", 1):
            report.add("excessive_punctuation", "warn", f"{bangs} exclamation marks.", 6)

        links = URL_RE.findall(body)
        report.checks["linkCount"] = len(links)
        if len(links) > self.rules.get("maxLinks", 3):
            report.add("too_many_links", "warn", f"{len(links)} links — keep cold mail to one CTA.", 8)

        if html:
            text_in_html = _word_count(html)
            images = len(IMG_RE.findall(html))
            report.checks["htmlWords"] = text_in_html
            report.checks["htmlImages"] = images
            if images and text_in_html < 20:
                report.add("image_heavy", "warn", "HTML is image-heavy with almost no text.", 10)
            if words and text_in_html:
                ratio = words / max(text_in_html, 1)
                report.checks["textToHtmlRatio"] = round(ratio, 2)
        if re.search(r"\.(exe|zip|scr|bat|js)\b", body, re.IGNORECASE):
            report.add("attachment_like", "warn", "Body references an executable/zip attachment.", 10)
        return report

    # ----------------------------------------------------------- behaviour
    def check_behaviour(
        self,
        *,
        now: datetime,
        sent_today: int,
        sent_this_hour: int,
        last_send_at: datetime | None = None,
        suppressed: bool = False,
        domain_count: int = 0,
        max_per_domain: int = 5,
        quiet_hours_active: bool = False,
        daily_cap: int | None = None,
        hourly_cap: int | None = None,
        min_gap_seconds: int | None = None,
        pending_gap_seconds: int = 0,
    ) -> ComplianceReport:
        report = ComplianceReport()
        daily_cap = daily_cap if daily_cap is not None else self.settings.daily_recipient_cap
        hourly_cap = hourly_cap if hourly_cap is not None else self.settings.hourly_recipient_cap

        report.checks.update(
            {
                "sentToday": sent_today,
                "sentThisHour": sent_this_hour,
                "dailyCap": daily_cap,
                "hourlyCap": hourly_cap,
            }
        )

        if suppressed:
            report.add("suppressed", "block", "Recipient is on the suppression list.", 100)

        if sent_today >= daily_cap:
            report.add(
                "daily_cap",
                "block",
                f"Daily cap reached ({sent_today}/{daily_cap} recipients today).",
                100,
            )
        elif sent_today >= daily_cap * 0.9:
            report.add(
                "daily_cap_near",
                "warn",
                f"At {sent_today}/{daily_cap} of today's budget — 10% headroom left.",
                6,
            )

        if sent_this_hour >= hourly_cap:
            report.add(
                "hourly_cap",
                "block",
                f"Hourly cap reached ({sent_this_hour}/{hourly_cap}) — pausing until the next hour.",
                100,
            )

        if last_send_at is not None:
            # The dispatcher evaluates this *before* it sleeps the slot's gap, so
            # the delay that is about to be applied has to count towards the gap —
            # otherwise every send looks like a burst.
            gap = (now - as_utc(last_send_at)).total_seconds() + max(0, pending_gap_seconds)
            report.checks["secondsSinceLastSend"] = int(gap)
            # The campaign's own pacing governs; the global setting is only the
            # default floor, and 5s is the absolute minimum we will accept.
            floor = max(5, min_gap_seconds if min_gap_seconds is not None
                        else self.settings.min_delay_seconds)
            report.checks["minGapSeconds"] = floor
            if gap < floor:
                report.add(
                    "burst",
                    "block",
                    f"Only {int(gap)}s since the last send (minimum {floor}s).",
                    100,
                )

        if domain_count >= max_per_domain:
            report.add(
                "domain_concentration",
                "warn",
                f"{domain_count} messages already sent to this recipient domain today.",
                12,
            )

        if quiet_hours_active:
            report.add(
                "quiet_hours",
                "block",
                "Inside configured quiet hours — holding the queue.",
                100,
            )
        return report

    def full_check(
        self, subject: str, body_text: str, body_html: str = "", **behaviour_kwargs
    ) -> ComplianceReport:
        content = self.check_content(subject, body_text, body_html)
        behaviour = self.check_behaviour(**behaviour_kwargs)
        merged = ComplianceReport(
            score=min(content.score, behaviour.score),
            issues=content.issues + behaviour.issues,
            checks={**content.checks, **behaviour.checks},
        )
        return merged

    # -------------------------------------------------------------- helpers
    def build_footer(self) -> str:
        """CAN-SPAM compliant footer appended to every outbound message."""
        name = self.settings.business_name or "Our team"
        address = self.settings.business_mailing_address or "123 Business Street, Suite 100, Your City"
        unsub = self.settings.unsubscribe_url
        line3 = (
            f'<a href="{unsub}">unsubscribe</a>' if unsub else "reply with STOP and we will remove you"
        )
        return (
            f'<p style="color:#8a8f98;font-size:12px;margin-top:24px;">'
            f"You are receiving this because your business was listed publicly as operating in a "
            f"region we serve. {name} &middot; {address}<br>"
            f"If this is not relevant, {line3} — no further emails will be sent.</p>"
        )

    def headers(self, message_id: str, list_unsubscribe_url: str = "") -> dict[str, str]:
        """Headers that reduce spam scoring and satisfy bulk-sender expectations."""
        headers = {
            "Message-ID": message_id,
            "X-Mailer": "LeadGen Studio",
            "Precedence": "bulk",
            "Auto-Submitted": "auto-generated",
            "MIME-Version": "1.0",
        }
        if list_unsubscribe_url:
            headers["List-Unsubscribe"] = f"<{list_unsubscribe_url}>"
            headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        return headers

    @staticmethod
    def safe_subject(subject: str, max_len: int = 65) -> str:
        subject = (subject or "").strip()
        if len(subject) <= max_len:
            return subject
        cut = subject[: max_len - 1].rstrip(" ,.;:-")
        return cut + "…"

    @staticmethod
    def next_available_slot(
        now: datetime,
        *,
        sent_today: int,
        sent_this_hour: int,
        daily_cap: int,
        hourly_cap: int,
    ) -> datetime | None:
        """When the next send would be permitted, or None if the day is done."""
        if sent_today >= daily_cap:
            tomorrow = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
            return tomorrow
        if sent_this_hour >= hourly_cap:
            return (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        return now


_engine: ComplianceEngine | None = None


def get_compliance_engine() -> ComplianceEngine:
    global _engine
    if _engine is None:
        _engine = ComplianceEngine()
    return _engine
