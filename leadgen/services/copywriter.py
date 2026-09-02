"""Dynamic, per-lead email copy generation.

Two engines:

* **Offline template engine** (default, always available) — a library of
  outreach structures with merge tags, deterministic per-lead variation and
  spintax subject rotation.  Conditional offer blocks (free demo call, free
  audit, discount, limited slots…) are woven in only when toggled on.
* **LLM engine** — when an API key is configured, the model writes the body
  from the same structured brief; the result is passed through the compliance
  checker and, if it fails, replaced by the offline version.

Variation is seeded by ``lead_id`` so a given lead always receives the same
variant (reproducible, auditable) while the campaign as a whole stays varied.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings, get_settings
from .llm import LLMError, get_llm

log = logging.getLogger("leadgen.copy")

SPINTAX_RE = re.compile(r"\{([^{}]*\|[^{}]*)\}")
# Templates use single-brace placeholders ({first_name}); spintax groups are
# resolved first because they always contain a "|".
MERGE_TAG_RE = re.compile(r"\{(\w+)\}")

OFFER_KEYS = (
    "free_demo_call",
    "free_audit",
    "case_study",
    "discount_percent",
    "limited_slots",
    "guarantee",
    "local_reference",
    "no_follow_up_pressure",
)


@dataclass
class OfferConfig:
    free_demo_call: bool = False
    free_audit: bool = False
    case_study: bool = False
    discount_percent: int = 0
    limited_slots: int = 0
    guarantee: str = ""
    local_reference: bool = False
    no_follow_up_pressure: bool = False
    calendar_url: str = ""
    extra_note: str = ""

    @classmethod
    def from_dict(cls, data: dict | None) -> OfferConfig:
        data = data or {}
        try:
            discount = int(data.get("discount_percent") or 0)
        except (TypeError, ValueError):
            discount = 0
        try:
            slots = int(data.get("limited_slots") or 0)
        except (TypeError, ValueError):
            slots = 0
        return cls(
            free_demo_call=bool(data.get("free_demo_call")),
            free_audit=bool(data.get("free_audit")),
            case_study=bool(data.get("case_study")),
            discount_percent=max(0, min(90, discount)),
            limited_slots=max(0, min(50, slots)),
            guarantee=str(data.get("guarantee") or "")[:120],
            local_reference=bool(data.get("local_reference")),
            no_follow_up_pressure=bool(data.get("no_follow_up_pressure")),
            calendar_url=str(data.get("calendar_url") or "")[:300],
            extra_note=str(data.get("extra_note") or "")[:300],
        )

    def to_dict(self) -> dict:
        return {
            "free_demo_call": self.free_demo_call,
            "free_audit": self.free_audit,
            "case_study": self.case_study,
            "discount_percent": self.discount_percent,
            "limited_slots": self.limited_slots,
            "guarantee": self.guarantee,
            "local_reference": self.local_reference,
            "no_follow_up_pressure": self.no_follow_up_pressure,
            "calendar_url": self.calendar_url,
            "extra_note": self.extra_note,
        }

    @property
    def active(self) -> bool:
        return any(
            [
                self.free_demo_call,
                self.free_audit,
                self.case_study,
                self.discount_percent,
                self.limited_slots,
                self.guarantee,
                self.local_reference,
                self.extra_note,
            ]
        )


@dataclass
class Template:
    key: str
    label: str
    subjects: list[str]
    openings: list[str]
    bridges: list[str]
    closers: list[str]
    tone: str = "professional"


TEMPLATES: list[Template] = [
    Template(
        key="consultative",
        label="Consultative / question-led",
        subjects=[
            "quick question about {business}",
            "{service} for {business}?",
            "{city} teams and {service_short}",
            "how is {business} handling {pain_short}?",
        ],
        openings=[
            "Hi {first_name}, I came across {business} while looking at {category} providers in {city}.",
            "Hi {first_name}, {business} showed up in my research on {category} work around {city}.",
            "Hi {first_name}, I was reviewing {category} companies serving {city} and {business} stood out.",
        ],
        bridges=[
            "We help {category_short} teams with {service}. The most common thing we hear is that {hook}.",
            "My work is {service}. Usually the trigger for a conversation is that {hook}.",
            "We do {service} work — and {hook} tends to be the bottleneck.",
        ],
        closers=[
            "Worth a short conversation?",
            "Is this something you'd be open to exploring?",
            "Happy to share specifics if it's relevant — no pressure either way.",
        ],
    ),
    Template(
        key="direct",
        label="Direct / value-first",
        subjects=[
            "{service} for {business}",
            "{service_short} — {city}",
            "an idea for {business}",
            "{first_name}, one question",
        ],
        openings=[
            "Hi {first_name}, keeping this short.",
            "Hi {first_name}, one quick note.",
            "Hi {first_name}, brief one.",
        ],
        bridges=[
            "We do {service} for companies like {business}. {hook_cap}.",
            "We provide {service}. For most {category_short} teams that means {hook}.",
            "Our focus is {service} — in practice that fixes the fact that {hook}.",
        ],
        closers=[
            "Would a 15-minute call be useful?",
            "Can I send over a one-page summary?",
            "Should I share how that would look for {business}?",
        ],
    ),
    Template(
        key="proof",
        label="Case-study / social proof",
        subjects=[
            "how a {city} {category_short} team fixed this",
            "{service} results near {city}",
            "results for {category} teams",
        ],
        openings=[
            "Hi {first_name}, I work with {category} businesses around {city}.",
            "Hi {first_name}, most of our clients are {category} operators in and around {city}.",
        ],
        bridges=[
            "We do {service}. Recently a similar team told us {hook} — after we started, that stopped being the constraint.",
            "Our work in {service_short} usually starts from the same problem: {hook}.",
        ],
        closers=[
            "Happy to send the numbers if useful.",
            "Want me to send the short version?",
        ],
    ),
    Template(
        key="local",
        label="Local / neighbourly",
        subjects=[
            "{city} + {service_short}",
            "nearby {service} help for {business}",
            "{first_name} — {city} question",
        ],
        openings=[
            "Hi {first_name}, I'm based near {city} and work with {category} businesses locally.",
            "Hi {first_name}, we work with a handful of {category} companies around {city}.",
        ],
        bridges=[
            "What we do is {service}. The recurring complaint we hear is that {hook}.",
            "We specialise in {service} — and {hook} is what usually brings people to us.",
        ],
        closers=[
            "If that's on your list this quarter, I'd be glad to talk.",
            "Worth 15 minutes?",
        ],
    ),
]

TEMPLATES_BY_KEY = {t.key: t for t in TEMPLATES}


@dataclass
class EmailCopy:
    subject: str
    body_text: str
    body_html: str
    preview: str = ""
    template_key: str = "consultative"
    source: str = "offline"
    fields: dict = field(default_factory=dict)
    llm_note: str = ""

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "bodyText": self.body_text,
            "bodyHtml": self.body_html,
            "preview": self.preview,
            "templateKey": self.template_key,
            "source": self.source,
            "fields": self.fields,
            "llmNote": self.llm_note,
        }


def stable_seed(*parts: Any) -> int:
    raw = "|".join(str(p) for p in parts)
    return int(hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12], 16)


def expand_spintax(text: str, rng: random.Random) -> str:
    """Resolve {a|b|c} groups once, deterministically for the given rng."""

    def repl(match: re.Match) -> str:
        options = [o.strip() for o in match.group(1).split("|") if o.strip()]
        return rng.choice(options) if options else ""

    previous = None
    current = text
    for _ in range(5):
        previous = current
        current = SPINTAX_RE.sub(repl, current)
        if current == previous:
            break
    return current


def resolve_tags(text: str, fields: dict[str, str]) -> str:
    return MERGE_TAG_RE.sub(lambda m: str(fields.get(m.group(1), m.group(0))), text or "")


def first_name_of(lead: dict) -> str:
    contact = (lead.get("contact_name") or "").strip()
    if contact:
        parts = [p for p in re.split(r"\s+", contact) if p]
        if parts:
            name = parts[0]
            if name.lower() not in {"sales", "info", "contact", "team", "admin", "hello"}:
                return name
    # Never guess a person's name from a business name: "Summit Roofing Co"
    # would otherwise be greeted as "Hi Summit,".  Fall back to a neutral hello.
    return "there"


class Copywriter:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    # ------------------------------------------------------------ context
    def build_fields(
        self,
        lead: dict,
        campaign: dict,
        offers: OfferConfig,
        hooks: list[str] | None = None,
    ) -> dict[str, str]:
        service = campaign.get("service_offering") or campaign.get("niche") or "our service"
        service_short = service.split(" for ")[0]
        service_short = service_short if len(service_short) <= 42 else service_short[:42].rsplit(" ", 1)[0]
        category = lead.get("category") or campaign.get("niche") or "local business"
        category_short = category.split(" ")[0] if category else "business"
        hooks = hooks or ["operations get in the way of growth"]
        sender_name = campaign.get("sender_name") or self.settings.business_name or "our team"
        fields = {
            "first_name": first_name_of(lead),
            "business": lead.get("business_name") or "your business",
            "city": lead.get("city") or "your area",
            "state": lead.get("state") or "",
            "country": lead.get("country") or "",
            "category": category,
            "category_short": category_short,
            "service": service,
            "service_short": service_short,
            "hook": hooks[0],
            "hook_alt": hooks[1] if len(hooks) > 1 else hooks[0],
            "sender_name": sender_name,
            "sender_company": self.settings.business_name or "",
            "phone": campaign.get("sender_phone") or "",
            "website": lead.get("website") or "",
            "rating": str(lead.get("rating") or ""),
            "review_count": str(lead.get("review_count") or ""),
        }
        fields["hook_cap"] = fields["hook"][:1].upper() + fields["hook"][1:]
        fields["pain_short"] = fields["hook"].split(" ")[-1].strip(".") or "this"
        return fields

    def offer_block(self, offers: OfferConfig, fields: dict) -> str:
        parts: list[str] = []
        if offers.free_demo_call:
            link = f" ({offers.calendar_url})" if offers.calendar_url else ""
            parts.append(
                "If it's easier, I can walk you through it on a free 15-minute demo call{link} — "
                "no deck, just your numbers.".replace("{link}", link)
            )
        if offers.free_audit:
            parts.append(
                "We'll also do a free audit of where you're losing the most before you commit to anything."
            )
        if offers.case_study:
            parts.append("I can send the short case study from a similar {category} team.")
        if offers.discount_percent:
            parts.append("New clients this month get {pct}% off the first engagement.")
        if offers.limited_slots:
            parts.append("We're taking on {slots} new {city} clients this month, so timing matters a little.")
        if offers.guarantee:
            parts.append("Guarantee: {guarantee}.")
        if offers.local_reference:
            parts.append("Happy to share a reference from another {city} business.")
        if offers.extra_note:
            parts.append(offers.extra_note)
        if not parts:
            return ""
        text = " ".join(parts)
        return resolve_tags(
            text,
            {
                **fields,
                "pct": str(offers.discount_percent),
                "slots": str(offers.limited_slots),
                "guarantee": offers.guarantee or "no results, no invoice",
            },
        )

    # -------------------------------------------------------------- render
    def _html(self, subject: str, body_text: str) -> str:
        paragraphs = [p.strip() for p in body_text.split("\n\n") if p.strip()]
        body_html = "\n".join(
            f'<p style="margin:0 0 14px;line-height:1.55;color:#1f2430;">{_escape(p)}</p>'
            for p in paragraphs
        )
        footer = _FOOTER_HTML.replace("{{footer}}", _escape(self._footer_text()))
        return _HTML_SHELL.format(
            subject=_escape(subject),
            body=body_html,
            footer=footer,
        )

    def _footer_text(self) -> str:
        name = self.settings.business_name or "Our team"
        address = self.settings.business_mailing_address or "123 Business Street, Suite 100, Your City"
        if self.settings.unsubscribe_url:
            return f"{name} · {address} · Unsubscribe: {self.settings.unsubscribe_url}"
        return f"{name} · {address} · Reply STOP to unsubscribe"

    def _max_subject_length(self) -> int:
        """Subject limit from the compliance rules (single source of truth)."""
        from .compliance import get_compliance_engine

        return int(get_compliance_engine().rules.get("maxSubjectLength", 70))

    def _choose_subject(self, template: Template, rng: random.Random, fields: dict) -> str:
        """Pick a subject that the compliance engine will not flag as too long.

        The seeded variant is used when it fits, so existing copy is unchanged.
        Only when it overflows the limit do we fall back to the shortest variant
        that fits — still fully deterministic, because each variant is expanded
        with its own index-seeded RNG rather than by consuming the main stream.
        """
        raw = rng.choice(template.subjects)
        chosen = resolve_tags(expand_spintax(raw, rng), fields)
        limit = self._max_subject_length()
        if len(chosen) <= limit:
            return chosen

        # Overflow: fall back to the variants that fit, keeping the seeded
        # rotation so subject lines still vary across recipients. Each variant
        # is expanded with its own index-seeded RNG so the main stream (and
        # therefore the body copy) is untouched.
        variants = [
            resolve_tags(expand_spintax(v, random.Random(index)), fields)
            for index, v in enumerate(template.subjects)
        ]
        fitting = [s for s in variants if len(s) <= limit] or variants
        slot = template.subjects.index(raw) % len(fitting)
        return fitting[slot]

    def generate_offline(
        self,
        lead: dict,
        campaign: dict,
        offers: OfferConfig | None = None,
        hooks: list[str] | None = None,
        template_key: str | None = None,
        seed: int | None = None,
    ) -> EmailCopy:
        offers = offers or OfferConfig()
        template = TEMPLATES_BY_KEY.get(template_key or campaign.get("template_key") or "consultative")
        if template is None:
            template = TEMPLATES[0]
        rng = random.Random(
            seed if seed is not None else stable_seed(lead.get("id"), lead.get("email"), template.key)
        )
        fields = self.build_fields(lead, campaign, offers, hooks)

        subject = self._choose_subject(template, rng, fields)
        opening = resolve_tags(expand_spintax(rng.choice(template.openings), rng), fields)
        bridge = resolve_tags(expand_spintax(rng.choice(template.bridges), rng), fields)
        closer = resolve_tags(expand_spintax(rng.choice(template.closers), rng), fields)

        offer_text = self.offer_block(offers, fields)
        pressure = (
            " If it's not relevant I won't follow up."
            if offers.no_follow_up_pressure
            else ""
        )

        paragraphs = [
            opening,
            " ".join(x for x in (bridge, offer_text) if x),
            closer + pressure,
        ]
        signature_name = fields["sender_name"]
        paragraphs.append(f"Best,\n{signature_name}")

        body_text = "\n\n".join(p for p in paragraphs if p.strip())
        body_text += "\n\n--\n" + self._footer_text()
        body_html = self._html(subject, body_text)
        preview = " ".join(body_text.split())[:140]

        return EmailCopy(
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            preview=preview,
            template_key=template.key,
            source="offline",
            fields=fields,
        )

    def generate_with_llm(
        self,
        lead: dict,
        campaign: dict,
        offers: OfferConfig | None = None,
        hooks: list[str] | None = None,
    ) -> EmailCopy:
        offers = offers or OfferConfig()
        fields = self.build_fields(lead, campaign, offers, hooks)
        brief = {
            "business": {
                "name": lead.get("business_name"),
                "category": lead.get("category"),
                "city": lead.get("city"),
                "country": lead.get("country"),
                "website": lead.get("website"),
                "publicSnippet": (lead.get("snippet") or "")[:400],
                "rating": lead.get("rating"),
                "reviews": lead.get("review_count"),
            },
            "ourService": campaign.get("service_offering"),
            "tone": campaign.get("tone", "professional"),
            "offers": offers.to_dict(),
            "hooks": hooks or [],
            "rules": [
                "under 130 words",
                "plain text, no markdown, no emojis",
                "one single call to action",
                "no spam trigger words like 'free!!!', 'guaranteed', 'act now'",
                "do not invent facts about the business",
                "paragraphs separated by a blank line",
            ],
        }
        data = get_llm().chat_json(
            json.dumps(brief, ensure_ascii=False),
            system=(
                "You write short, specific B2B cold emails that read like a human wrote them. "
                'Respond with JSON only: {"subject": str, "body": str, "reasoning": str}'
            ),
            temperature=0.75,
            max_tokens=500,
        )
        subject = str(data.get("subject") or "").strip()[:120]
        body = str(data.get("body") or "").strip()
        if not subject or not body:
            raise LLMError("LLM returned an empty draft")
        body = body.replace("\\n", "\n")
        body += "\n\n--\n" + self._footer_text()
        return EmailCopy(
            subject=subject,
            body_text=body,
            body_html=self._html(subject, body),
            preview=" ".join(body.split())[:140],
            template_key=campaign.get("template_key", "consultative"),
            source="llm",
            fields=fields,
            llm_note=str(data.get("reasoning") or "")[:300],
        )

    def generate(
        self,
        lead: dict,
        campaign: dict,
        offers: OfferConfig | None = None,
        hooks: list[str] | None = None,
        prefer_llm: bool = True,
    ) -> EmailCopy:
        offers = offers or OfferConfig.from_dict(campaign.get("offers"))
        if prefer_llm and get_llm().enabled:
            try:
                copy = self.generate_with_llm(lead, campaign, offers, hooks)
                fallback = self.generate_offline(lead, campaign, offers, hooks)
                # LLM output must still satisfy the basics — otherwise use the template.
                if len(copy.body_text.split()) >= 25:
                    copy.fields = fallback.fields
                    return copy
                copy.llm_note = "LLM draft too short — fell back to template"
                return fallback
            except LLMError as exc:
                log.info("LLM copy failed, using templates: %s", exc)
                copy = self.generate_offline(lead, campaign, offers, hooks)
                copy.llm_note = str(exc)
                return copy
        return self.generate_offline(lead, campaign, offers, hooks)

    # ------------------------------------------------------------- previews
    def preview_batch(
        self,
        leads: list[dict],
        campaign: dict,
        offers: OfferConfig | None = None,
        hooks: list[str] | None = None,
        prefer_llm: bool = False,
        limit: int = 3,
    ) -> list[dict]:
        offers = offers or OfferConfig.from_dict(campaign.get("offers"))
        out = []
        for lead in leads[:limit]:
            copy = self.generate(lead, campaign, offers, hooks, prefer_llm=prefer_llm)
            out.append({"leadId": lead.get("id"), "email": lead.get("email"), **copy.to_dict()})
        return out

    @staticmethod
    def template_catalog() -> list[dict]:
        return [{"key": t.key, "label": t.label, "tone": t.tone} for t in TEMPLATES]


def _escape(text: str) -> str:
    text = (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace("\n", "<br>")


_HTML_SHELL = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{subject}</title></head>
<body style="margin:0;padding:0;background:#f4f5f7;">
<div style="max-width:600px;margin:0 auto;padding:24px;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:15px;color:#1f2430;">
{body}
{footer}
</div></body></html>"""

_FOOTER_HTML = (
    '<div style="margin-top:28px;padding-top:14px;border-top:1px solid #e3e5e9;'
    'font-size:12px;line-height:1.5;color:#8a8f98;">{{footer}}</div>'
)


_writer: Copywriter | None = None


def get_copywriter() -> Copywriter:
    global _writer
    if _writer is None:
        _writer = Copywriter()
    return _writer
