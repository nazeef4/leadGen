"""Campaigns: CRUD, lead review dashboard, scraping, copy preview, dispatch."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Activity,
    Campaign,
    CampaignStatus,
    EmailAccount,
    Lead,
    LeadStatus,
    MessageStatus,
    OutboundMessage,
    PipelineStage,
    Suppression,
)
from ..schemas import (
    BulkLeadAction,
    CampaignCreate,
    CampaignUpdate,
    ComplianceCheckRequest,
    CopyPreviewRequest,
    DispatchRequest,
    GeoFilter,
    LeadUpdate,
    ScrapeRequest,
)
from ..services.compliance import get_compliance_engine
from ..services.copywriter import OfferConfig, get_copywriter
from ..services.delay import DelayConfig, DelayPlanner, utcnow
from ..services.dispatcher import get_engine
from ..services.geo import get_geo_service
from ..services.niche_advisor import get_niche_advisor
from ..services.scrapers.base import ScrapedLead
from ..services.scrapers.enrich import score_lead
from ..services.scrapers.pipeline import get_pipeline

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


# ------------------------------------------------------------------ helpers
def _counts(db: Session, campaign_id: int) -> dict:
    rows = db.execute(
        select(Lead.status, func.count(Lead.id)).where(Lead.campaign_id == campaign_id).group_by(
            Lead.status
        )
    ).all()
    by_status = {status: int(count) for status, count in rows}
    selected = db.execute(
        select(func.count(Lead.id)).where(
            Lead.campaign_id == campaign_id, Lead.selected.is_(True)
        )
    ).scalar_one()
    with_email = db.execute(
        select(func.count(Lead.id)).where(
            Lead.campaign_id == campaign_id, Lead.email != ""
        )
    ).scalar_one()
    sent = db.execute(
        select(func.count(OutboundMessage.id)).where(
            OutboundMessage.campaign_id == campaign_id,
            OutboundMessage.status == MessageStatus.SENT,
        )
    ).scalar_one()
    return {
        "byStatus": by_status,
        "total": sum(by_status.values()),
        "selected": int(selected or 0),
        "withEmail": int(with_email or 0),
        "sent": int(sent or 0),
    }


def _get_campaign(db: Session, campaign_id: int) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


def _campaign_dict(db: Session, campaign: Campaign, *, sender_name: str | None = None) -> dict:
    data = campaign.to_dict(_counts(db, campaign.id))
    account = (
        db.get(EmailAccount, campaign.sender_account_id) if campaign.sender_account_id else None
    )
    if account:
        data["sender_account"] = {"email": account.email, "display_name": account.display_name}
        data["sender_name"] = sender_name or account.display_name or account.email.split("@")[0]
    return data


# ---------------------------------------------------------------- campaigns
@router.get("")
def list_campaigns(db: Session = Depends(get_db)) -> dict:
    campaigns = db.execute(select(Campaign).order_by(Campaign.id.desc())).scalars().all()
    engine = get_engine()
    out = []
    for campaign in campaigns:
        data = _campaign_dict(db, campaign)
        data["isRunning"] = engine.state.running and engine.state.campaign_id == campaign.id
        out.append(data)
    return {"campaigns": out}


@router.post("", status_code=201)
def create_campaign(payload: CampaignCreate, db: Session = Depends(get_db)) -> dict:
    campaign = Campaign(
        name=payload.name,
        niche=payload.niche,
        service_offering=payload.service_offering,
        geo_filter=payload.geo_filter.to_plain() if payload.geo_filter else {},
        offers=payload.offers.model_dump() if payload.offers else {},
        tone=payload.tone,
        template_key=payload.template_key,
        sender_account_id=payload.sender_account_id,
        max_per_day=payload.max_per_day,
        delay_min=payload.delay_min,
        delay_max=payload.delay_max,
        track_replies=payload.track_replies,
        send_html=payload.send_html,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return {"campaign": _campaign_dict(db, campaign)}


@router.get("/{campaign_id}")
def get_campaign(campaign_id: int, db: Session = Depends(get_db)) -> dict:
    campaign = _get_campaign(db, campaign_id)
    data = _campaign_dict(db, campaign)
    data["geoSummary"] = get_geo_service().describe(campaign.geo_filter or {})
    data["geoProfile"] = get_geo_service().climate_profile(campaign.geo_filter or {})
    data["geoProblems"] = get_geo_service().validate(campaign.geo_filter or {})
    data["offerConfig"] = OfferConfig.from_dict(campaign.offers).to_dict()
    return {"campaign": data}


@router.patch("/{campaign_id}")
def update_campaign(
    campaign_id: int, payload: CampaignUpdate, db: Session = Depends(get_db)
) -> dict:
    campaign = _get_campaign(db, campaign_id)
    data = payload.model_dump(exclude_unset=True)
    if "geo_filter" in data and data["geo_filter"] is not None:
        data["geo_filter"] = GeoFilter(**data["geo_filter"]).to_plain()
    if "offers" in data and data["offers"] is not None:
        from ..schemas import OfferPayload

        data["offers"] = OfferPayload(**data["offers"]).model_dump()
    for key, value in data.items():
        setattr(campaign, key, value)
    if campaign.delay_max < campaign.delay_min:
        campaign.delay_max = campaign.delay_min
    db.commit()
    db.refresh(campaign)
    return {"campaign": _campaign_dict(db, campaign)}


@router.delete("/{campaign_id}")
def delete_campaign(campaign_id: int, db: Session = Depends(get_db)) -> dict:
    campaign = _get_campaign(db, campaign_id)
    engine = get_engine()
    if engine.state.running and engine.state.campaign_id == campaign_id:
        engine.stop()
    db.delete(campaign)
    db.commit()
    return {"ok": True, "detail": "Campaign deleted"}


# -------------------------------------------------------------------- leads
@router.get("/{campaign_id}/leads")
def list_leads(
    campaign_id: int,
    status: str | None = None,
    stage: str | None = None,
    q: str | None = None,
    selected: bool | None = None,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = 0,
    db: Session = Depends(get_db),
) -> dict:
    _get_campaign(db, campaign_id)
    stmt = select(Lead).where(Lead.campaign_id == campaign_id)
    if status:
        stmt = stmt.where(Lead.status == status)
    if stage:
        stmt = stmt.where(Lead.pipeline_stage == stage)
    if selected is not None:
        stmt = stmt.where(Lead.selected.is_(selected))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Lead.business_name.ilike(like),
                Lead.email.ilike(like),
                Lead.city.ilike(like),
                Lead.category.ilike(like),
            )
        )
    total = db.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()
    leads = db.execute(
        stmt.order_by(Lead.score.desc(), Lead.id.desc()).limit(limit).offset(offset)
    ).scalars().all()
    return {
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
        "leads": [lead.to_dict() for lead in leads],
        "counts": _counts(db, campaign_id),
    }


@router.patch("/{campaign_id}/leads/{lead_id}")
def update_lead(
    campaign_id: int, lead_id: int, payload: LeadUpdate, db: Session = Depends(get_db)
) -> dict:
    lead = db.get(Lead, lead_id)
    if not lead or lead.campaign_id != campaign_id:
        raise HTTPException(status_code=404, detail="Lead not found")
    data = payload.model_dump(exclude_unset=True)
    notes = data.pop("notes", None)
    if "email" in data and data["email"]:
        data["email"] = data["email"].lower()
    for key, value in data.items():
        setattr(lead, key, value)
    if notes:
        db.add(Activity(lead_id=lead.id, kind="note", note=notes))
    db.commit()
    db.refresh(lead)
    return {"lead": lead.to_dict()}


@router.delete("/{campaign_id}/leads/{lead_id}")
def delete_lead(campaign_id: int, lead_id: int, db: Session = Depends(get_db)) -> dict:
    lead = db.get(Lead, lead_id)
    if not lead or lead.campaign_id != campaign_id:
        raise HTTPException(status_code=404, detail="Lead not found")
    db.delete(lead)
    db.commit()
    return {"ok": True}


@router.post("/{campaign_id}/leads/bulk")
def bulk_leads(
    campaign_id: int, payload: BulkLeadAction, db: Session = Depends(get_db)
) -> dict:
    _get_campaign(db, campaign_id)
    stmt = select(Lead).where(Lead.campaign_id == campaign_id)
    if payload.lead_ids:
        stmt = stmt.where(Lead.id.in_(payload.lead_ids))
    if payload.only_with_email:
        stmt = stmt.where(Lead.email != "")
    leads = db.execute(stmt).scalars().all()
    affected = 0
    for lead in leads:
        if payload.action == "select":
            lead.selected = True
        elif payload.action == "deselect":
            lead.selected = False
        elif payload.action == "exclude":
            lead.selected = False
            lead.status = LeadStatus.EXCLUDED
        elif payload.action == "include":
            lead.selected = True
            if lead.status == LeadStatus.EXCLUDED:
                lead.status = LeadStatus.NEW
        elif payload.action == "queue":
            if lead.email:
                lead.selected = True
                lead.status = LeadStatus.QUEUED
            else:
                continue
        elif payload.action == "delete":
            db.delete(lead)
        affected += 1
    db.commit()
    return {"ok": True, "affected": affected, "counts": _counts(db, campaign_id)}


@router.get("/{campaign_id}/leads-export.csv")
def export_leads(campaign_id: int, db: Session = Depends(get_db)):
    import csv
    import io

    from fastapi.responses import StreamingResponse

    _get_campaign(db, campaign_id)
    leads = db.execute(
        select(Lead).where(Lead.campaign_id == campaign_id).order_by(Lead.id)
    ).scalars().all()
    buffer = io.StringIO()
    fields = [
        "business_name", "contact_name", "email", "phone", "website", "city", "state",
        "country", "category", "status", "pipeline_stage", "score", "source",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for lead in leads:
        data = lead.to_dict()
        writer.writerow({key: data.get(key, "") for key in fields})
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"content-disposition": f"attachment; filename=campaign-{campaign_id}-leads.csv"},
    )


# ----------------------------------------------------------------- scraping
@router.post("/{campaign_id}/scrape")
def scrape(campaign_id: int, payload: ScrapeRequest, db: Session = Depends(get_db)) -> dict:
    campaign = _get_campaign(db, campaign_id)
    offering = payload.offering or campaign.service_offering or campaign.niche
    if not offering:
        raise HTTPException(status_code=400, detail="Define the service offering before scraping")
    geo_filter = (
        payload.geo_filter.to_plain() if payload.geo_filter else (campaign.geo_filter or {})
    )
    sources = payload.sources or ["duckduckgo"]
    if payload.csv_text and "csv" not in sources:
        sources = ["csv", *sources]
    advisor = get_niche_advisor()
    signals = advisor.suggest(offering, geo_filter, use_llm=False, top_n=3)["buyerSignals"]
    kwargs = dict(
        offering=offering,
        geo_filter=geo_filter,
        sources=sources,
        max_results=payload.max_results,
        enrich=payload.enrich,
        max_places=payload.max_places,
        queries_per_place=payload.queries_per_place,
        buyer_signals=signals,
        csv_text=payload.csv_text,
        campaign_id=campaign_id,
    )
    pipeline = get_pipeline()
    if payload.sync:
        job = pipeline.run_sync(**kwargs)
        saved = _save_leads(db, campaign_id, job.results)
        return {"job": job.to_dict(include_results=True), "saved": saved}
    job = pipeline.start_async(**kwargs)
    return {"job": job.to_dict(), "detail": "Scrape started"}


@router.get("/scrape-jobs/{job_id}")
def scrape_status(
    job_id: str, include_results: bool = False, db: Session = Depends(get_db)
) -> dict:
    job = get_pipeline().get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job": job.to_dict(include_results=include_results)}


@router.post("/scrape-jobs/{job_id}/save")
def save_scrape_results(
    job_id: str,
    lead_ids: list[int] | None = None,
    campaign_id: int | None = None,
    db: Session = Depends(get_db),
) -> dict:
    job = get_pipeline().get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    target = campaign_id or job.campaign_id
    if not target:
        raise HTTPException(status_code=400, detail="No campaign specified")
    _get_campaign(db, target)
    selected = job.results
    if lead_ids:
        # results have no DB id yet; the UI passes indexes instead
        selected = [r for i, r in enumerate(job.results) if i in set(lead_ids)]
    return {"saved": _save_leads(db, target, selected)}


@router.post("/scrape-jobs/{job_id}/cancel")
def cancel_scrape(job_id: str) -> dict:
    return {"ok": get_pipeline().cancel(job_id)}


def _save_leads(db: Session, campaign_id: int, results: list[ScrapedLead]) -> dict:
    existing_emails = {
        row.email.lower()
        for row in db.execute(
            select(Lead.email).where(Lead.campaign_id == campaign_id, Lead.email != "")
        ).all()
    }
    suppressed = {row.email.lower() for row in db.execute(select(Suppression.email)).all()}
    advisor = get_niche_advisor()
    campaign = db.get(Campaign, campaign_id)
    signals = advisor.suggest(
        (campaign.service_offering if campaign else "") or "", None, use_llm=False, top_n=3
    )["buyerSignals"] if campaign else []
    created = duplicates = skipped = 0
    for result in results:
        email = (result.email or "").lower().strip()
        if email and (email in existing_emails or email in suppressed):
            duplicates += 1
            continue
        if not result.business_name:
            skipped += 1
            continue
        score, reasons = score_lead(result, buyer_signals=signals)
        lead = Lead(
            campaign_id=campaign_id,
            business_name=result.business_name[:250],
            contact_name=result.contact_name[:200],
            email=email[:250],
            phone=result.phone[:60],
            website=result.website[:500],
            address=result.address[:400],
            city=result.city[:120],
            state=result.state[:120],
            country=result.country[:120],
            category=result.category[:200],
            snippet=result.snippet[:1500],
            source=result.source,
            source_url=result.source_url[:900],
            rating=result.rating,
            review_count=result.review_count,
            score=score,
            signals={**result.signals, "scoreReasons": reasons},
            status=LeadStatus.NEW,
            selected=bool(email),
            is_suppressed=email in suppressed,
        )
        db.add(lead)
        if email:
            existing_emails.add(email)
        created += 1
    db.commit()
    return {"created": created, "duplicates": duplicates, "skipped": skipped}


# --------------------------------------------------------------------- copy
@router.post("/{campaign_id}/preview")
def preview_copy(
    campaign_id: int, payload: CopyPreviewRequest, db: Session = Depends(get_db)
) -> dict:
    campaign = _get_campaign(db, campaign_id)
    offers = (
        OfferConfig.from_dict(payload.offers.model_dump())
        if payload.offers
        else OfferConfig.from_dict(campaign.offers)
    )
    advisor = get_niche_advisor()
    hooks = advisor.suggest(
        campaign.service_offering or campaign.niche, campaign.geo_filter, use_llm=False, top_n=3
    )["hooks"]
    stmt = select(Lead).where(Lead.campaign_id == campaign_id)
    if payload.lead_ids:
        stmt = stmt.where(Lead.id.in_(payload.lead_ids))
    leads = db.execute(stmt.order_by(Lead.score.desc()).limit(payload.limit)).scalars().all()
    if not leads:
        raise HTTPException(status_code=400, detail="No leads in this campaign to preview against")
    account = db.get(EmailAccount, campaign.sender_account_id) if campaign.sender_account_id else None
    campaign_dict = campaign.to_dict()
    campaign_dict["sender_name"] = (
        payload.sender_name or (account.display_name if account else "") or "The team"
    )
    items = get_copywriter().preview_batch(
        [lead.to_dict() for lead in leads],
        campaign_dict,
        offers,
        hooks,
        prefer_llm=payload.prefer_llm,
        limit=payload.limit,
    )
    return {"previews": items, "hooks": hooks, "templates": get_copywriter().template_catalog()}


@router.post("/preview-sample")
def preview_sample(payload: CopyPreviewRequest) -> dict:
    """Preview against a synthetic lead — used before any scraping happened."""
    lead = payload.sample_lead or {
        "id": 0,
        "business_name": "Example Business Co",
        "contact_name": "",
        "email": "hello@example.com",
        "city": "Phoenix",
        "state": "AZ",
        "country": "United States",
        "category": payload.niche or "local business",
        "rating": 4.7,
        "review_count": 84,
    }
    campaign = {
        "service_offering": payload.service_offering,
        "niche": payload.niche,
        "tone": payload.tone,
        "template_key": payload.template_key,
        "sender_name": payload.sender_name or "The team",
    }
    offers = OfferConfig.from_dict(payload.offers.model_dump() if payload.offers else {})
    hooks = get_niche_advisor().suggest(
        payload.service_offering or payload.niche, None, use_llm=False, top_n=3
    )["hooks"]
    copy = get_copywriter().generate(lead, campaign, offers, hooks, prefer_llm=payload.prefer_llm)
    report = get_compliance_engine().check_content(copy.subject, copy.body_text, copy.body_html)
    return {"preview": copy.to_dict(), "compliance": report.to_dict(), "hooks": hooks}


@router.post("/compliance-check")
def compliance_check(payload: ComplianceCheckRequest) -> dict:
    report = get_compliance_engine().check_content(
        payload.subject, payload.body_text, payload.body_html
    )
    return report.to_dict()


@router.post("/{campaign_id}/plan-preview")
def plan_preview(campaign_id: int, db: Session = Depends(get_db)) -> dict:
    """Show the randomised, unordered send plan without sending anything."""
    from ..config import get_settings

    campaign = _get_campaign(db, campaign_id)
    settings = get_settings()
    lead_ids = [
        row.id
        for row in db.execute(
            select(Lead.id).where(
                Lead.campaign_id == campaign_id,
                Lead.selected.is_(True),
                Lead.email != "",
            )
        ).all()
    ]
    cfg = DelayConfig(
        min_seconds=campaign.delay_min,
        max_seconds=campaign.delay_max,
        long_pause_every=settings.long_pause_every,
        long_pause_min_seconds=settings.long_pause_min_seconds,
        long_pause_max_seconds=settings.long_pause_max_seconds,
        daily_cap=min(campaign.max_per_day, settings.daily_recipient_cap),
        hourly_cap=settings.hourly_recipient_cap,
        enforce_quiet_hours=settings.enforce_quiet_hours,
        quiet_start_hour=settings.quiet_hours_start,
        quiet_end_hour=settings.quiet_hours_end,
        seed=None,
    )
    plan = DelayPlanner(cfg).plan(lead_ids)
    return {
        "plan": plan.to_dict(),
        "config": {
            "minSeconds": cfg.min_seconds,
            "maxSeconds": cfg.max_seconds,
            "longPauseEvery": cfg.long_pause_every,
            "dailyCap": cfg.daily_cap,
            "hourlyCap": cfg.hourly_cap,
        },
        "problems": cfg.validate(),
        "selectedCount": len(lead_ids),
    }


# ----------------------------------------------------------------- dispatch
@router.post("/dispatch/prepare")
def prepare(campaign_id: int, db: Session = Depends(get_db)) -> dict:
    _get_campaign(db, campaign_id)
    result = get_engine().prepare_queue(campaign_id)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return {"ok": True, **result}


@router.post("/dispatch/start")
def start_dispatch(payload: DispatchRequest, db: Session = Depends(get_db)) -> dict:
    campaign = _get_campaign(db, payload.campaign_id)
    engine = get_engine()
    if payload.prepare:
        prepared = engine.prepare_queue(payload.campaign_id, prefer_llm=payload.prefer_llm)
    else:
        prepared = {"created": 0, "skipped": 0}
    if not payload.dry_run and not campaign.sender_account_id:
        raise HTTPException(status_code=400, detail="Attach a sending account before dispatching")
    ok, detail = engine.start(payload.campaign_id, dry_run=payload.dry_run)
    if not ok:
        raise HTTPException(status_code=409, detail=detail)
    campaign.status = CampaignStatus.RUNNING
    db.commit()
    return {"ok": ok, "detail": detail, "prepared": prepared, "state": engine.state.to_dict()}


@router.post("/dispatch/pause")
def pause_dispatch() -> dict:
    return {"ok": get_engine().pause(), "state": get_engine().state.to_dict()}


@router.post("/dispatch/resume")
def resume_dispatch() -> dict:
    return {"ok": get_engine().resume(), "state": get_engine().state.to_dict()}


@router.post("/dispatch/stop")
def stop_dispatch() -> dict:
    return {"ok": get_engine().stop(), "state": get_engine().state.to_dict()}


@router.get("/dispatch/state")
def dispatch_state() -> dict:
    return {"state": get_engine().state.to_dict()}


@router.post("/{campaign_id}/requeue")
def requeue(campaign_id: int, db: Session = Depends(get_db)) -> dict:
    _get_campaign(db, campaign_id)
    count = get_engine().requeue(campaign_id)
    return {"ok": True, "requeued": count}


@router.get("/{campaign_id}/messages")
def list_messages(
    campaign_id: int,
    status: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict:
    _get_campaign(db, campaign_id)
    stmt = select(OutboundMessage).where(OutboundMessage.campaign_id == campaign_id)
    if status:
        stmt = stmt.where(OutboundMessage.status == status)
    messages = db.execute(
        stmt.order_by(OutboundMessage.id.desc()).limit(limit)
    ).scalars().all()
    return {"messages": [m.to_dict() for m in messages]}
