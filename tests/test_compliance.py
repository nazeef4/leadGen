"""Compliance guardrails: content rules and sending behaviour."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from leadgen.services.compliance import ComplianceEngine

GOOD_BODY = """Hi Maria,

I came across Desert Air while looking at HVAC contractors in Phoenix. We help
teams like yours with commercial maintenance contracts, and the most common thing
we hear is that summer demand spikes blow past crew capacity.

Worth a short conversation? If it's not relevant I won't follow up.

Best,
Alex

--
Test Company Ltd · 12 Test Street, Suite 4, Springfield, ST 00000
Reply STOP to unsubscribe
"""


@pytest.fixture
def engine():
    return ComplianceEngine()


def test_clean_email_passes(engine):
    report = engine.check_content("quick question about Desert Air", GOOD_BODY)
    assert not report.blocked
    assert report.score >= 80
    assert report.checks["optOut"] is True
    assert report.checks["postalAddress"] is True


def test_missing_opt_out_blocks(engine):
    body = GOOD_BODY.replace("Reply STOP to unsubscribe", "")
    report = engine.check_content("hello", body)
    assert report.blocked
    assert any(i.code == "no_opt_out" for i in report.issues)


def test_missing_postal_address_blocks(engine):
    body = GOOD_BODY.replace("12 Test Street, Suite 4, Springfield, ST 00000", "somewhere")
    report = engine.check_content("hello", body)
    assert report.blocked
    assert any(i.code == "no_postal_address" for i in report.issues)


def test_spam_phrases_are_flagged(engine):
    spammy = (
        "ACT NOW and get 100% free guaranteed risk free money back offer. "
        "You have won a free gift and free trial with unlimited access!!! "
        "Reply STOP to unsubscribe. 12 Test Street, Suite 4, Springfield, ST 00000"
    )
    report = engine.check_content("YOU HAVE WON!!!", spammy)
    assert report.blocked
    assert any(i.code == "spam_phrases" for i in report.issues)


def test_all_caps_and_punctuation_warn(engine):
    body = GOOD_BODY + " CALL NOW!!! THIS IS HUGE!!! AMAZING DEAL!!!"
    report = engine.check_content("hello", body)
    codes = {i.code for i in report.issues}
    assert "excessive_punctuation" in codes


def test_long_subject_warns(engine):
    report = engine.check_content("x" * 95, GOOD_BODY)
    assert any(i.code == "subject_long" for i in report.issues)


def test_short_body_warns(engine):
    report = engine.check_content("hi", "Hello. Reply STOP to unsubscribe. 12 Test Street, Suite 4, Springfield, ST 00000")
    assert any(i.code == "body_too_short" for i in report.issues)


def test_daily_cap_blocks(engine):
    now = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    report = engine.check_behaviour(
        now=now, sent_today=400, sent_this_hour=0, daily_cap=400, hourly_cap=60
    )
    assert report.blocked
    assert any(i.code == "daily_cap" for i in report.issues)


def test_near_daily_cap_warns_but_allows(engine):
    now = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    report = engine.check_behaviour(
        now=now, sent_today=380, sent_this_hour=0, daily_cap=400, hourly_cap=60
    )
    assert not report.blocked
    assert any(i.code == "daily_cap_near" for i in report.issues)


def test_hourly_cap_blocks(engine):
    now = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    report = engine.check_behaviour(
        now=now, sent_today=50, sent_this_hour=60, daily_cap=400, hourly_cap=60
    )
    assert report.blocked
    assert any(i.code == "hourly_cap" for i in report.issues)


def test_burst_sending_blocks(engine, settings):
    now = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    report = engine.check_behaviour(
        now=now, sent_today=5, sent_this_hour=5, last_send_at=now - timedelta(seconds=5)
    )
    assert report.blocked
    assert any(i.code == "burst" for i in report.issues)


def test_minimum_gap_satisfied(engine):
    now = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    report = engine.check_behaviour(
        now=now, sent_today=5, sent_this_hour=5,
        last_send_at=now - timedelta(seconds=300),
    )
    assert not report.blocked


def test_campaign_pacing_overrides_the_global_floor(engine):
    """A campaign configured for 10s gaps must not be stalled by the 45s default."""
    now = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    stalled = engine.check_behaviour(
        now=now, sent_today=5, sent_this_hour=5, last_send_at=now - timedelta(seconds=12)
    )
    assert stalled.blocked, "the default 45s floor should block a 12s gap"

    allowed = engine.check_behaviour(
        now=now, sent_today=5, sent_this_hour=5,
        last_send_at=now - timedelta(seconds=12), min_gap_seconds=10,
    )
    assert not allowed.blocked
    assert allowed.checks["minGapSeconds"] == 10


def test_min_gap_floor_cannot_go_below_five_seconds(engine):
    now = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    report = engine.check_behaviour(
        now=now, sent_today=1, sent_this_hour=1,
        last_send_at=now - timedelta(seconds=1), min_gap_seconds=0,
    )
    assert report.blocked
    assert report.checks["minGapSeconds"] == 5


def test_suppressed_recipient_blocks(engine):
    now = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    report = engine.check_behaviour(
        now=now, sent_today=0, sent_this_hour=0, suppressed=True
    )
    assert report.blocked
    assert any(i.code == "suppressed" for i in report.issues)


def test_quiet_hours_block(engine):
    now = datetime(2026, 3, 10, 3, 0, tzinfo=timezone.utc)
    report = engine.check_behaviour(
        now=now, sent_today=0, sent_this_hour=0, quiet_hours_active=True
    )
    assert report.blocked


def test_domain_concentration_warns(engine):
    now = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    report = engine.check_behaviour(
        now=now, sent_today=20, sent_this_hour=5, domain_count=6, max_per_domain=5
    )
    assert any(i.code == "domain_concentration" for i in report.issues)


def test_full_check_merges_content_and_behaviour(engine):
    now = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    report = engine.full_check(
        "hello", GOOD_BODY, "",
        now=now, sent_today=400, sent_this_hour=0, daily_cap=400, hourly_cap=60,
    )
    assert report.blocked
    assert report.checks["optOut"] is True


def test_footer_contains_required_elements(engine):
    footer = engine.build_footer()
    assert "unsubscribe" in footer.lower() or "STOP" in footer


def test_headers_include_bulk_sender_fields(engine):
    headers = engine.headers("<abc@x.com>", "https://example.com/u")
    assert headers["Message-ID"] == "<abc@x.com>"
    assert headers["Precedence"] == "bulk"
    assert headers["Auto-Submitted"] == "auto-generated"
    assert "List-Unsubscribe" in headers
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_safe_subject_truncates(engine):
    assert len(ComplianceEngine.safe_subject("x" * 200)) <= 65


def test_next_available_slot_rolls_over(engine):
    now = datetime(2026, 3, 10, 15, 30, tzinfo=timezone.utc)
    assert ComplianceEngine.next_available_slot(
        now, sent_today=400, sent_this_hour=0, daily_cap=400, hourly_cap=60
    ).day == 11
    rolled = ComplianceEngine.next_available_slot(
        now, sent_today=10, sent_this_hour=60, daily_cap=400, hourly_cap=60
    )
    assert rolled.hour == 16 and rolled.minute == 0
