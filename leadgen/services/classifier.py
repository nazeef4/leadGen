"""Reply intent classification.

Pure, deterministic text heuristics — no model call, so it works offline and
gives the same answer for the same text (important when you audit why a lead was
flagged "interested").  Ordered so the strongest signal wins: a bounce is never
mistaken for interest, and an unsubscribe always wins over everything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Intent

OOO_PATTERNS = [
    r"\bout of (the )?office\b", r"\bautomatic(ally)? reply\b", r"\bauto[\s-]?reply\b",
    r"\bi am (currently )?(away|traveling|travelling|on (annual )?leave)\b",
    r"\bon vacation until\b", r"\bon holiday until\b", r"\bmaternity leave\b",
    r"\bwill be back on\b", r"\breturning on\b", r"\blimited access to (my )?email\b",
]
AUTO_PATTERNS = [
    r"\bdo not reply\b", r"\bdon'?t reply\b", r"\bno-?reply\b", r"\bnoreply\b",
    r"\bautomated message\b", r"\bdelivery status notification\b", r"\bundeliverable\b",
    r"\bmailer-?daemon\b", r"\bmessage could not be delivered\b",
    r"\bthis is an automated (e-?mail|response)\b", r"\bunsubscribe request (received|processed)\b",
]
UNSUB_PATTERNS = [
    r"\bunsubscribe\b", r"\bremove me\b", r"\btake me off\b", r"\bstop (e-?mailing|contacting)\b",
    r"\bdo not (e-?mail|contact) me\b", r"\bplease remove\b", r"\bopt( |-)?out\b",
    r"\bdelete my (data|details|information)\b", r"\breply stop\b",
]
NOT_INTERESTED = [
    r"\bnot interested\b", r"\bno (thanks|thank you)\b", r"\bwe('?re| are) not (looking|interested)\b",
    r"\balready (have|using|work with)\b", r"\bwe use\b", r"\bno need\b", r"\bnot (a )?good fit\b",
    r"\bnot right now\b", r"\bwrong (person|contact|email)\b", r"\bplease don'?t contact\b",
    r"\bwe'?re all set\b", r"\bnot in the market\b", r"\bno budget\b", r"\bstop\b",
]
INTERESTED = [
    r"\bi'?m interested\b", r"\bwe'?re interested\b", r"\bsounds (good|great|interesting)\b",
    r"\blet'?s (talk|chat|meet|do it)\b", r"\bbook (a |the )?(call|meeting|demo)\b",
    r"\bsend (me |over |through )?(more )?(info|details|pricing|a quote|proposal)\b",
    r"\bhow much\b", r"\bwhat('?s| is) (the )?(cost|price)\b", r"\bcan you (call|send)\b",
    r"\bi'?d like to\b", r"\bwe'?d like to\b", r"\btell me more\b", r"\bwhat are your rates\b",
    r"\bavailable (on|next|this)\b", r"\bforward (this|it) to\b", r"\bthis is (me|my team)\b",
    r"\bwhen are you (free|available)\b", r"\blet'?s move forward\b", r"\bquote please\b",
    r"\bdoes (this|that) include\b",
]
QUESTION_WORDS = [r"\bhow\b", r"\bwhat\b", r"\bwhen\b", r"\bwhere\b", r"\bwhy\b", r"\bwho\b", r"\bwhich\b"]
SPAM_PATTERNS = [
    r"\bviagra\b", r"\bcasino\b", r"\blottery\b", r"\bprize\b", r"\bwire transfer\b",
    r"\bcryptocurrency investment\b", r"\bdear (sir|friend)\b",
]

POSITIVE = ["thanks", "great", "good", "perfect", "excellent", "appreciate", "helpful", "interested", "yes"]
NEGATIVE = ["annoying", "spam", "stop", "not interested", "waste", "rude", "remove", "angry", "unacceptable"]


@dataclass
class Classification:
    intent: str
    sentiment: str
    matched: list[str]
    confidence: float

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "sentiment": self.sentiment,
            "matched": self.matched,
            "confidence": round(self.confidence, 2),
        }


def _find(patterns: list[str], text: str) -> list[str]:
    hits = []
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            hits.append(match.group(0).strip())
    return hits


def classify_reply(subject: str = "", body: str = "") -> Classification:
    text = f"{subject or ''}\n{body or ''}".strip()
    if not text:
        return Classification(Intent.UNKNOWN, "neutral", [], 0.1)

    auto_hits = _find(AUTO_PATTERNS, text)
    ooo_hits = _find(OOO_PATTERNS, text)
    unsub_hits = _find(UNSUB_PATTERNS, text)
    negative_hits = _find(NOT_INTERESTED, text)
    positive_hits = _find(INTERESTED, text)
    spam_hits = _find(SPAM_PATTERNS, text)

    # bounces / machine replies are never a human response
    if re.search(r"^(undeliverable|delivery status notification|returned mail)", subject or "", re.I):
        return Classification(Intent.AUTO_REPLY, "neutral", ["bounce subject"], 0.95)
    if auto_hits and not positive_hits:
        return Classification(Intent.AUTO_REPLY, "neutral", auto_hits, 0.85)
    if spam_hits:
        return Classification(Intent.SPAM, "negative", spam_hits, 0.7)
    if unsub_hits:
        return Classification(Intent.NOT_INTERESTED, "negative", unsub_hits, 0.95)
    if ooo_hits and not positive_hits and not negative_hits:
        return Classification(Intent.OUT_OF_OFFICE, "neutral", ooo_hits, 0.8)

    if positive_hits and not negative_hits:
        return Classification(Intent.INTERESTED, "positive", positive_hits, min(0.6 + 0.15 * len(positive_hits), 0.97))
    if negative_hits and not positive_hits:
        return Classification(Intent.NOT_INTERESTED, "negative", negative_hits, min(0.6 + 0.15 * len(negative_hits), 0.95))
    if positive_hits and negative_hits:
        # mixed — a human wrote something substantive; treat as a question
        return Classification(Intent.QUESTION, "neutral", positive_hits + negative_hits, 0.55)

    question_marks = text.count("?")
    if question_marks and any(re.search(p, text, re.I) for p in QUESTION_WORDS):
        return Classification(Intent.QUESTION, "neutral", [f"{question_marks} question(s)"], 0.5)

    sentiment = "neutral"
    lowered = text.lower()
    pos = sum(1 for w in POSITIVE if w in lowered)
    neg = sum(1 for w in NEGATIVE if w in lowered)
    if pos > neg:
        sentiment = "positive"
    elif neg > pos:
        sentiment = "negative"
    return Classification(Intent.UNKNOWN, sentiment, [], 0.3)


def wants_unsubscribe(subject: str = "", body: str = "") -> bool:
    classification = classify_reply(subject, body)
    return classification.intent == Intent.NOT_INTERESTED and bool(
        _find(UNSUB_PATTERNS, f"{subject}\n{body}")
    )


def summarise(body: str, limit: int = 240) -> str:
    text = " ".join((body or "").split())
    text = re.sub(r"^>.*?$", "", text, flags=re.MULTILINE)  # drop quoted lines
    return text[:limit] + ("…" if len(text) > limit else "")
