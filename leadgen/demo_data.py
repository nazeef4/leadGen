#!/usr/bin/env python3
"""Seeds a fully worked example: campaign -> leads -> sent messages -> replies.

    python -m leadgen demo
    python scripts/seed_demo.py --leads 8   (thin wrapper around this module)

Uses the deterministic demo scraper and the dry-run sender, so no network access
and no credentials are needed.  Everything it creates is labelled ``demo``.
"""

from __future__ import annotations

import argparse
import random
from datetime import timedelta

from .db import create_all, init_engine, session_scope
from .models import (
    Activity,
    Campaign,
    CampaignStatus,
    EmailAccount,
    Lead,
    LeadStatus,
    MessageStatus,
    OutboundMessage,
    PipelineStage,
    Reply,
)
from .services.classifier import classify_reply, summarise
from .services.copywriter import OfferConfig, get_copywriter
from .services.geo import get_geo_service
from .services.niche_advisor import get_niche_advisor
from .services.scrapers.demo import DemoScraper
from .services.sender import new_thread_id

OFFERING = "commercial HVAC maintenance contracts"
REPLY_SAMPLES = [
    ("Re: {subject}", "Hi, this sounds interesting — can you send pricing for a 12-unit building?", "interested"),
    ("Re: {subject}", "Thanks for reaching out. Let's talk next Tuesday, are you free at 10am?", "interested"),
    ("Re: {subject}", "We already have a contractor for this. Not interested, thanks.", "not_interested"),
    ("Re: {subject}", "Please remove me from your mailing list.", "not_interested"),
    ("Re: {subject}", "I'm out of the office until the 14th with limited access to email.", "out_of_office"),
    ("Re: {subject}", "How often would the maintenance visits be, and does it cover rooftop units?", "question"),
]


def seed(leads_per_place: int = 6, with_messages: bool = True) -> dict:
    geo = get_geo_service()
    advisor = get_niche_advisor()
    suggestion = advisor.suggest(OFFERING, None, use_llm=False, top_n=4)
    places = []
    for item in suggestion["suggestions"]:
        place = geo.place(item.get("countryCode") or "", item.get("state") or "*", item.get("city") or "*")
        if place:
            places.append(place)
    places = places[:3] or []

    with session_scope() as session:
        account = EmailAccount(
            email="demo-sender@leadgen.local",
            display_name="Demo Sender",
            provider="custom",
            smtp_host="smtp.localhost",
            imap_host="imap.localhost",
            daily_limit=400,
            hourly_limit=60,
            is_verified=False,
            is_active=True,
        )
        session.add(account)
        session.flush()

        campaign = Campaign(
            name="Demo — Commercial HVAC (hot climates)",
            niche="HVAC",
            service_offering=OFFERING,
            geo_filter={
                "selections": [
                    {"country": p.country_code, "state": p.state_code or "*", "city": p.city or "*"}
                    for p in places
                ],
                "extraCities": [],
            },
            offers=OfferConfig(
                free_demo_call=True, case_study=True, limited_slots=3, no_follow_up_pressure=True
            ).to_dict(),
            tone="professional",
            template_key="consultative",
            sender_account_id=account.id,
            status=CampaignStatus.RUNNING,
            max_per_day=50,
            delay_min=45,
            delay_max=240,
        )
        session.add(campaign)
        session.flush()

        rng = random.Random(7)
        writer = get_copywriter()
        hooks = suggestion["hooks"]
        created = 0
        leads: list[Lead] = []
        for place in places:
            scraper = DemoScraper(city=place.city, state=place.state, country=place.country)
            for result in scraper.search("hvac company", limit=leads_per_place):
                lead = Lead(
                    campaign_id=campaign.id,
                    business_name=result.business_name,
                    contact_name=result.contact_name,
                    email=result.email,
                    phone=result.phone,
                    website=result.website,
                    address=result.address,
                    city=result.city or place.city,
                    state=result.state or place.state,
                    country=result.country or place.country,
                    category=result.category,
                    snippet=result.snippet,
                    source="demo",
                    source_url=result.source_url,
                    rating=result.rating,
                    review_count=result.review_count,
                    score=rng.randint(58, 96),
                    signals={"synthetic": True},
                    status=LeadStatus.NEW,
                    selected=True,
                )
                session.add(lead)
                leads.append(lead)
                created += 1
        session.flush()

        sent = replies = interested = 0
        if with_messages:
            campaign_dict = campaign.to_dict()
            campaign_dict["sender_name"] = "Alex Mercer"
            base = campaign.created_at - timedelta(days=2)
            for index, lead in enumerate(leads):
                copy = writer.generate_offline(lead.to_dict(), campaign_dict, None, hooks)
                message = OutboundMessage(
                    lead_id=lead.id,
                    campaign_id=campaign.id,
                    account_id=account.id,
                    rfc_message_id=f"<demo-{index}@leadgen.local>",
                    thread_id=new_thread_id(),
                    subject=copy.subject,
                    body_text=copy.body_text,
                    body_html=copy.body_html,
                    status=MessageStatus.SENT,
                    delay_seconds=rng.randint(45, 240),
                    sent_at=base + timedelta(minutes=index * 7, seconds=rng.randint(0, 40)),
                    compliance_score=rng.randint(88, 100),
                )
                session.add(message)
                lead.status = LeadStatus.SENT
                lead.pipeline_stage = PipelineStage.CONTACTED
                session.add(
                    Activity(
                        lead_id=lead.id, kind="outbound",
                        payload={"status": "sent", "subject": copy.subject},
                        created_at=message.sent_at,
                    )
                )
                sent += 1

            # Guarantee a spread of intents so the CRM board shows progression
            # instead of whatever the random draw happened to produce.
            chosen_samples = [
                REPLY_SAMPLES[0],   # interested
                REPLY_SAMPLES[1],   # interested (meeting)
                REPLY_SAMPLES[5],   # question
                REPLY_SAMPLES[2],   # not interested
                REPLY_SAMPLES[4],   # out of office
            ]
            # strict=False: the sample is deliberately capped at min(5, len(leads)).
            for lead, sample in zip(
                rng.sample(leads, min(5, len(leads))), chosen_samples, strict=False
            ):
                template, body, intent = sample
                message = session.query(OutboundMessage).filter_by(lead_id=lead.id).first()
                subject = template.format(subject=(message.subject if message else "your email"))
                classification = classify_reply(subject, body)
                received = base + timedelta(hours=rng.randint(6, 44))
                session.add(
                    Reply(
                        lead_id=lead.id,
                        message_id=message.id if message else None,
                        account_id=account.id,
                        from_email=lead.email,
                        from_name=lead.contact_name,
                        subject=subject,
                        snippet=summarise(body),
                        body=body,
                        intent=classification.intent,
                        sentiment=classification.sentiment,
                        matched_by="reference",
                        imap_uid=f"demo-{lead.id}",
                        received_at=received,
                        is_read=rng.random() > 0.5,
                    )
                )
                lead.status = LeadStatus.REPLIED
                lead.pipeline_stage = (
                    PipelineStage.ENGAGED if classification.intent == "interested" else PipelineStage.REPLIED
                )
                session.add(
                    Activity(
                        lead_id=lead.id, kind="reply", note=body,
                        payload={"intent": classification.intent}, created_at=received,
                    )
                )
                replies += 1
                if classification.intent == "interested":
                    interested += 1

    return {
        "campaign": "Demo — Commercial HVAC (hot climates)",
        "places": [p.label for p in places],
        "leads": created,
        "sent": sent,
        "replies": replies,
        "interested": interested,
        "note": "Open http://127.0.0.1:8765 and pick the demo campaign.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--leads", type=int, default=6)
    parser.add_argument("--no-messages", action="store_true")
    args = parser.parse_args()
    init_engine()
    create_all()
    import json

    print(json.dumps(seed(args.leads, not args.no_messages), indent=2, default=str))


if __name__ == "__main__":
    main()
