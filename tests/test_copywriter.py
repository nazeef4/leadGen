"""Copy generation: personalisation, offer weaving, variation, compliance."""

from __future__ import annotations

import random
import re

import pytest

from leadgen.services.compliance import ComplianceEngine
from leadgen.services.copywriter import (
    Copywriter,
    OfferConfig,
    expand_spintax,
    first_name_of,
    get_copywriter,
    resolve_tags,
    stable_seed,
)

LEAD = {
    "id": 7,
    "business_name": "Desert Air Conditioning LLC",
    "contact_name": "Maria Lopez",
    "email": "maria@desertair.example.com",
    "city": "Phoenix",
    "state": "AZ",
    "country": "United States",
    "category": "HVAC contractor",
    "rating": 4.8,
    "review_count": 126,
}
CAMPAIGN = {
    "service_offering": "commercial HVAC maintenance contracts",
    "niche": "HVAC",
    "template_key": "consultative",
    "sender_name": "Alex Mercer",
}
HOOKS = [
    "summer demand spikes blow past crew capacity",
    "commercial clients lose revenue for every hour the unit is down",
]


@pytest.fixture
def writer():
    return Copywriter()


def test_merge_tags_are_fully_resolved(writer):
    copy = writer.generate_offline(LEAD, CAMPAIGN, OfferConfig(), HOOKS)
    assert "{first_name}" not in copy.body_text
    assert re.search(r"\{[a-z_]+\}", copy.body_text) is None
    assert "Maria" in copy.body_text
    assert "Desert Air Conditioning" in copy.body_text
    assert "Phoenix" in copy.body_text


def test_greeting_never_invents_a_person_name(writer):
    lead = {**LEAD, "contact_name": ""}
    copy = writer.generate_offline(lead, CAMPAIGN, OfferConfig(), HOOKS)
    assert copy.body_text.startswith("Hi there,")
    assert "Hi Summit" not in copy.body_text
    assert first_name_of({"business_name": "Summit Roofing Co"}) == "there"


def test_offer_blocks_appear_only_when_toggled(writer):
    plain = writer.generate_offline(LEAD, CAMPAIGN, OfferConfig(), HOOKS)
    assert "demo call" not in plain.body_text.lower()

    with_offer = writer.generate_offline(
        LEAD, CAMPAIGN, OfferConfig(free_demo_call=True, calendar_url="https://cal.example/x"), HOOKS
    )
    assert "demo call" in with_offer.body_text.lower()
    assert "https://cal.example/x" in with_offer.body_text

    discount = writer.generate_offline(LEAD, CAMPAIGN, OfferConfig(discount_percent=15), HOOKS)
    assert "15%" in discount.body_text

    slots = writer.generate_offline(LEAD, CAMPAIGN, OfferConfig(limited_slots=3), HOOKS)
    assert "3 new Phoenix clients" in slots.body_text

    guarantee = writer.generate_offline(
        LEAD, CAMPAIGN, OfferConfig(guarantee="no results, no invoice"), HOOKS
    )
    assert "no results, no invoice" in guarantee.body_text

    audit = writer.generate_offline(LEAD, CAMPAIGN, OfferConfig(free_audit=True), HOOKS)
    assert "free audit" in audit.body_text.lower()

    low_pressure = writer.generate_offline(
        LEAD, CAMPAIGN, OfferConfig(no_follow_up_pressure=True), HOOKS
    )
    assert "won't follow up" in low_pressure.body_text


def test_legal_footer_is_always_appended(writer):
    copy = writer.generate_offline(LEAD, CAMPAIGN, OfferConfig(), HOOKS)
    lowered = copy.body_text.lower()
    assert "unsubscribe" in lowered or "reply stop" in lowered
    assert re.search(r"\d+\s+\w+.*(street|ave|suite|st)", copy.body_text, re.I)


def test_offline_copy_passes_the_compliance_check(writer):
    copy = writer.generate_offline(LEAD, CAMPAIGN, OfferConfig(free_demo_call=True), HOOKS)
    report = ComplianceEngine().check_content(copy.subject, copy.body_text, copy.body_html)
    assert not report.blocked, [i.message for i in report.issues if i.severity == "block"]


def test_different_leads_get_different_variants(writer):
    offers = OfferConfig(free_demo_call=True)
    first = writer.generate_offline(LEAD, CAMPAIGN, offers, HOOKS)
    other = writer.generate_offline(
        {**LEAD, "id": 8, "business_name": "Summit Roofing Co", "city": "Mesa"},
        CAMPAIGN, offers, HOOKS,
    )
    assert first.body_text != other.body_text


def test_same_lead_is_reproducible(writer):
    offers = OfferConfig()
    a = writer.generate_offline(LEAD, CAMPAIGN, offers, HOOKS)
    b = writer.generate_offline(LEAD, CAMPAIGN, offers, HOOKS)
    assert a.subject == b.subject
    assert a.body_text == b.body_text


def test_html_variant_is_well_formed(writer):
    copy = writer.generate_offline(LEAD, CAMPAIGN, OfferConfig(), HOOKS)
    assert copy.body_html.startswith("<!doctype html>")
    assert "<p style=" in copy.body_html
    assert copy.subject in copy.body_html


def test_all_templates_render(writer):
    for template in ("consultative", "direct", "proof", "local"):
        campaign = {**CAMPAIGN, "template_key": template}
        copy = writer.generate_offline(LEAD, campaign, OfferConfig(case_study=True), HOOKS)
        assert copy.subject and len(copy.body_text) > 120
        assert re.search(r"\{[a-z_]+\}", copy.body_text) is None


def test_preview_batch_limits_output(writer):
    leads = [{**LEAD, "id": i} for i in range(1, 8)]
    previews = writer.preview_batch(leads, CAMPAIGN, OfferConfig(), HOOKS, limit=3)
    assert len(previews) == 3
    assert previews[0]["bodyText"]


def test_spintax_resolves_single_option(writer):
    text = "hello {a|b|c} world"
    out = expand_spintax(text, random.Random(1))
    assert out.split()[1] in {"a", "b", "c"}
    assert "{" not in out


def test_resolve_tags_keeps_unknown_tags():
    assert resolve_tags("hi {first_name} and {nope}", {"first_name": "Al"}) == "hi Al and {nope}"


def test_stable_seed_is_deterministic():
    assert stable_seed(1, "a") == stable_seed(1, "a")
    assert stable_seed(1, "a") != stable_seed(2, "a")


def test_offer_config_clamps_values():
    offers = OfferConfig.from_dict({"discount_percent": 500, "limited_slots": -3})
    assert offers.discount_percent == 90
    assert offers.limited_slots == 0
    assert OfferConfig.from_dict(None).active is False


def test_service_grammar_reads_naturally(writer):
    copy = writer.generate_offline(LEAD, CAMPAIGN, OfferConfig(), HOOKS)
    assert "contracts engagements" not in copy.body_text
    assert "HVAC teams" in copy.body_text or "HVAC contractor" in copy.body_text


# --------------------------------------------------------------------------
# Regression: the generator must not produce copy its own compliance engine
# rejects. Every template x seed combination has to score 100.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("template_key", ["consultative", "direct", "proof", "local"])
def test_every_generated_email_scores_100(template_key):
    from leadgen.services.compliance import ComplianceEngine

    cw = get_copywriter()
    eng = ComplianceEngine()
    offers = OfferConfig(
        free_demo_call=True, free_audit=True, case_study=True, discount_percent=50,
        limited_slots=3, guarantee="no results, no invoice", local_reference=True,
        no_follow_up_pressure=True, calendar_url="https://cal.com/x/15min",
    )
    lead = {
        "id": 7, "business_name": "Desert Air Cooling", "first_name": "there",
        "category": "industrial HVAC contractor", "city": "Phoenix", "state": "AZ",
        "email": "ops@desertair.example.com",
    }
    campaign = {"service_offering": "commercial HVAC maintenance contracts", "niche": "HVAC"}

    for seed in range(40):
        copy = cw.generate_offline(lead, campaign, offers, template_key=template_key, seed=seed)
        report = eng.check_content(copy.subject, copy.body_text)
        assert report.score == 100, (
            f"{template_key} seed={seed} scored {report.score}: "
            f"{[(i.severity, i.code, i.message) for i in report.issues]}"
        )
        assert not report.blocked


def test_subjects_rotate_instead_of_collapsing_to_one_variant():
    """
    When the seeded subject overflows the limit we fall back to a fitting
    variant — but it must still rotate, or every recipient gets an identical
    subject line, which is itself a spam signal.
    """
    cw = get_copywriter()
    lead = {
        "id": 3, "business_name": "Desert Air Cooling", "first_name": "there",
        "category": "HVAC contractor", "city": "Phoenix", "state": "AZ",
        "email": "ops@desertair.example.com",
    }
    campaign = {"service_offering": "commercial HVAC maintenance contracts", "niche": "HVAC"}
    subjects = {
        cw.generate_offline(lead, campaign, template_key="local", seed=s).subject
        for s in range(12)
    }
    assert len(subjects) >= 2, f"subject lines collapsed to one variant: {subjects}"


def test_generation_is_deterministic_for_a_given_seed():
    cw = get_copywriter()
    lead = {"id": 11, "business_name": "Summit Roofing", "email": "a@b.example.com",
            "category": "Roofing contractor", "city": "Tucson", "state": "AZ"}
    campaign = {"service_offering": "roof maintenance", "niche": "roofing"}
    first = cw.generate_offline(lead, campaign, template_key="proof", seed=5)
    second = cw.generate_offline(lead, campaign, template_key="proof", seed=5)
    assert first.subject == second.subject
    assert first.body_text == second.body_text
