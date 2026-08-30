"""Reply classification, matching and inbox ingestion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from leadgen.models import Intent, Lead, LeadStatus, OutboundMessage, PipelineStage, Reply, Suppression
from leadgen.db import session_scope
from leadgen.services.classifier import classify_reply, summarise, wants_unsubscribe
from leadgen.services.inbox_sync import (
    FetchedMessage,
    InboxSyncService,
    match_reply,
    normalise_subject,
    parse_message,
)


# ------------------------------------------------------------- classification
@pytest.mark.parametrize(
    "text,expected",
    [
        ("Sounds good, can you send pricing?", Intent.INTERESTED),
        ("Let's talk next Tuesday at 10am", Intent.INTERESTED),
        ("We already have a contractor. Not interested, thanks.", Intent.NOT_INTERESTED),
        ("Please remove me from your mailing list.", Intent.NOT_INTERESTED),
        ("I'm out of the office until the 14th with limited access to email.", Intent.OUT_OF_OFFICE),
        ("How often would the visits be, and does it cover rooftop units?", Intent.QUESTION),
        ("This is an automated message, do not reply.", Intent.AUTO_REPLY),
        ("", Intent.UNKNOWN),
    ],
)
def test_intent_classification(text, expected):
    assert classify_reply("Re: your email", text).intent == expected


def test_bounce_subject_is_an_auto_reply():
    result = classify_reply("Undeliverable: quick question", "message could not be delivered")
    assert result.intent == Intent.AUTO_REPLY


def test_unsubscribe_detection():
    assert wants_unsubscribe("Re: hi", "Please unsubscribe me from this list.")
    assert not wants_unsubscribe("Re: hi", "Send me the pricing please.")


def test_unsubscribe_beats_interest():
    result = classify_reply("Re: hi", "Sounds interesting but please unsubscribe me.")
    assert result.intent == Intent.NOT_INTERESTED


def test_sentiment_is_set():
    assert classify_reply("", "Thanks, this is great").sentiment == "positive"
    assert classify_reply("", "This is spam, stop emailing me").sentiment == "negative"


def test_summarise_truncates_and_strips_quotes():
    body = "> quoted line\n" + ("word " * 200)
    assert len(summarise(body, 120)) <= 121


def test_subject_normalisation():
    assert normalise_subject("Re: Fwd: Quick question") == "quick question"
    assert normalise_subject("AW: Angebot") == "angebot"


# ------------------------------------------------------------------ matching
def test_match_by_message_id_reference():
    fetched = FetchedMessage(
        uid="1", from_email="a@b.com", from_name="A", subject="Re: x", body="",
        date=datetime.now(timezone.utc), in_reply_to=["<abc@x.com>"],
    )
    result = match_reply(
        fetched,
        message_ids={"<abc@x.com>": (10, 20)},
        subjects={},
        lead_emails={},
    )
    assert result.matched and result.matched_by == "reference"
    assert result.message_id == 10 and result.lead_id == 20


def test_match_by_references_header():
    fetched = FetchedMessage(
        uid="2", from_email="a@b.com", from_name="", subject="anything", body="",
        date=datetime.now(timezone.utc), references=["<old@x.com>", "<abc@x.com>"],
    )
    result = match_reply(
        fetched, message_ids={"<abc@x.com>": (1, 2)}, subjects={}, lead_emails={}
    )
    assert result.matched_by == "reference"


def test_match_by_subject():
    fetched = FetchedMessage(
        uid="3", from_email="new@b.com", from_name="", subject="Re: Quick Question", body="",
        date=datetime.now(timezone.utc),
    )
    result = match_reply(
        fetched, message_ids={}, subjects={"quick question": (5, 6)}, lead_emails={}
    )
    assert result.matched and result.matched_by == "subject"


def test_match_by_address_as_last_resort():
    fetched = FetchedMessage(
        uid="4", from_email="owner@acme.com", from_name="", subject="hello", body="",
        date=datetime.now(timezone.utc),
    )
    result = match_reply(
        fetched, message_ids={}, subjects={}, lead_emails={"owner@acme.com": 7}
    )
    assert result.matched and result.matched_by == "address"
    assert result.lead_id == 7


def test_no_match():
    fetched = FetchedMessage(
        uid="5", from_email="random@x.com", from_name="", subject="spam", body="",
        date=datetime.now(timezone.utc),
    )
    result = match_reply(fetched, message_ids={}, subjects={}, lead_emails={})
    assert not result.matched


# -------------------------------------------------------------------- parsing
def _raw_email(uid_subject="Re: quick question", body="Sounds good, send pricing."):
    return (
        b"From: Maria Lopez <maria@desertair.example.com>\r\n"
        b"To: alex@testcompany.example\r\n"
        b"Subject: " + uid_subject.encode() + b"\r\n"
        b"Date: Tue, 10 Mar 2026 09:12:00 +0000\r\n"
        b"In-Reply-To: <demo-1@leadgen.local>\r\n"
        b"References: <demo-1@leadgen.local>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n" + body.encode() + b"\r\n"
    )


def test_parse_message_extracts_headers_and_body():
    parsed = parse_message("42", _raw_email())
    assert parsed is not None
    assert parsed.from_email == "maria@desertair.example.com"
    assert parsed.from_name == "Maria Lopez"
    assert parsed.subject == "Re: quick question"
    assert "send pricing" in parsed.body
    assert parsed.in_reply_to == ["<demo-1@leadgen.local>"]
    assert parsed.date.year == 2026


def test_parse_prefers_plain_text_part():
    raw = (
        b"From: A <a@b.com>\r\nSubject: multipart\r\n"
        b'Content-Type: multipart/alternative; boundary="ZZ"\r\n\r\n'
        b"--ZZ\r\nContent-Type: text/plain\r\n\r\nPLAIN VERSION\r\n"
        b"--ZZ\r\nContent-Type: text/html\r\n\r\n<p>HTML VERSION</p>\r\n--ZZ--\r\n"
    )
    parsed = parse_message("7", raw)
    assert "PLAIN VERSION" in parsed.body
    assert "HTML VERSION" not in parsed.body


# ------------------------------------------------------------------- ingestion
def _seed_outbound(email="maria@desertair.example.com"):
    from leadgen.models import EmailAccount

    with session_scope() as session:
        if session.get(EmailAccount, 1) is None:
            session.add(
                EmailAccount(
                    id=1, email="alex@testcompany.example", display_name="Alex",
                    provider="custom", is_active=True,
                )
            )
        lead = Lead(
            campaign_id=None, business_name="Desert Air Conditioning", email=email,
            status=LeadStatus.SENT, pipeline_stage=PipelineStage.CONTACTED, selected=True,
        )
        session.add(lead)
        session.flush()
        message = OutboundMessage(
            lead_id=lead.id, rfc_message_id="<demo-1@leadgen.local>",
            subject="quick question about Desert Air", status="sent",
            sent_at=datetime.now(timezone.utc),
        )
        session.add(message)
        session.flush()
        return lead.id, message.id


def test_ingest_creates_reply_and_moves_lead():
    lead_id, message_id = _seed_outbound()
    fetched = FetchedMessage(
        uid="99", from_email="maria@desertair.example.com", from_name="Maria Lopez",
        subject="Re: quick question about Desert Air",
        body="Sounds good — can you send pricing?",
        date=datetime.now(timezone.utc), in_reply_to=["<demo-1@leadgen.local>"],
    )
    stats = InboxSyncService().ingest(1, [fetched], account_email="alex@testcompany.example")
    assert stats["replies"] == 1
    assert stats["interested"] == 1
    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        assert lead.status == LeadStatus.REPLIED
        assert lead.pipeline_stage == PipelineStage.REPLIED
        reply = session.get(Reply, 1)
        assert reply.intent == Intent.INTERESTED
        assert reply.matched_by == "reference"
        assert reply.lead_id == lead_id
        assert reply.message_id == message_id


def test_ingest_is_idempotent():
    _seed_outbound()
    fetched = FetchedMessage(
        uid="100", from_email="maria@desertair.example.com", from_name="",
        subject="Re: quick question about Desert Air", body="thanks",
        date=datetime.now(timezone.utc), in_reply_to=["<demo-1@leadgen.local>"],
    )
    service = InboxSyncService()
    service.ingest(1, [fetched])
    second = service.ingest(1, [fetched])
    assert second["duplicates"] == 1
    assert second["replies"] == 0


def test_unsubscribe_reply_suppresses_the_address():
    lead_id, _ = _seed_outbound()
    fetched = FetchedMessage(
        uid="101", from_email="maria@desertair.example.com", from_name="",
        subject="Re: quick question about Desert Air",
        body="Please remove me from your mailing list.",
        date=datetime.now(timezone.utc), in_reply_to=["<demo-1@leadgen.local>"],
    )
    stats = InboxSyncService().ingest(1, [fetched])
    assert stats["unsubscribes"] == 1
    with session_scope() as session:
        assert session.query(Suppression).filter_by(email="maria@desertair.example.com").first()
        lead = session.get(Lead, lead_id)
        assert lead.status == LeadStatus.UNSUBSCRIBED
        assert lead.is_suppressed is True


def test_out_of_office_does_not_mark_interested():
    _seed_outbound()
    fetched = FetchedMessage(
        uid="102", from_email="maria@desertair.example.com", from_name="",
        subject="Re: quick question about Desert Air",
        body="I am out of the office until Monday with limited access to email.",
        date=datetime.now(timezone.utc), in_reply_to=["<demo-1@leadgen.local>"],
    )
    stats = InboxSyncService().ingest(1, [fetched])
    assert stats["interested"] == 0
    assert stats["replies"] == 1


def test_own_outbound_copy_is_ignored():
    _seed_outbound(email="alex@testcompany.example")
    fetched = FetchedMessage(
        uid="103", from_email="alex@testcompany.example", from_name="Me",
        subject="quick question about Desert Air", body="sent copy",
        date=datetime.now(timezone.utc),
    )
    stats = InboxSyncService().ingest(1, [fetched], account_email="alex@testcompany.example")
    assert stats["replies"] == 0


def test_unmatched_reply_is_recorded_but_not_linked():
    _seed_outbound()
    fetched = FetchedMessage(
        uid="104", from_email="someone.else@other.example", from_name="",
        subject="hello there", body="what is this?", date=datetime.now(timezone.utc),
    )
    stats = InboxSyncService().ingest(1, [fetched])
    assert stats["unmatched"] == 1
    with session_scope() as session:
        reply = session.get(Reply, 1)
        assert reply.lead_id is None
        assert reply.matched_by == "none"
