"""CRM: reply inbox, pipeline board, activities, suppression list."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Activity,
    EmailAccount,
    Lead,
    LeadStatus,
    OutboundMessage,
    PipelineStage,
    Reply,
    Suppression,
)
from ..schemas import NoteCreate, StageUpdate, SuppressionCreate
from ..services.inbox_sync import get_inbox_service

router = APIRouter(prefix="/api/crm", tags=["crm"])


@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict:
    service = get_inbox_service()
    campaigns = service.campaign_overview()
    totals = {
        "leads": db.execute(select(func.count(Lead.id))).scalar_one(),
        "sent": db.execute(
            select(func.count(OutboundMessage.id)).where(OutboundMessage.sent_at.is_not(None))
        ).scalar_one(),
        "replies": db.execute(select(func.count(Reply.id))).scalar_one(),
        "interested": db.execute(
            select(func.count(Reply.id)).where(Reply.intent == "interested")
        ).scalar_one(),
        "unread": db.execute(
            select(func.count(Reply.id)).where(Reply.is_read.is_(False))
        ).scalar_one(),
        "suppressed": db.execute(select(func.count(Suppression.id))).scalar_one(),
    }
    sent = int(totals["sent"] or 0)
    totals["replyRate"] = round(int(totals["replies"] or 0) / sent, 4) if sent else 0.0
    totals["interestRate"] = (
        round(int(totals["interested"] or 0) / sent, 4) if sent else 0.0
    )
    intents = db.execute(
        select(Reply.intent, func.count(Reply.id)).group_by(Reply.intent)
    ).all()
    stages = db.execute(
        select(Lead.pipeline_stage, func.count(Lead.id)).group_by(Lead.pipeline_stage)
    ).all()
    return {
        "totals": totals,
        "campaigns": campaigns["campaigns"],
        "intents": {intent: int(count) for intent, count in intents},
        "stages": {stage: int(count) for stage, count in stages},
        "stageOrder": PipelineStage.ORDER,
    }


@router.get("/replies")
def list_replies(
    intent: str | None = None,
    unread_only: bool = False,
    lead_id: int | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(Reply)
    if intent:
        stmt = stmt.where(Reply.intent == intent)
    if unread_only:
        stmt = stmt.where(Reply.is_read.is_(False))
    if lead_id:
        stmt = stmt.where(Reply.lead_id == lead_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Reply.subject.ilike(like), Reply.snippet.ilike(like), Reply.from_email.ilike(like)))
    replies = db.execute(
        stmt.order_by(Reply.received_at.desc()).limit(limit)
    ).scalars().all()
    out = []
    for reply in replies:
        data = reply.to_dict()
        if reply.lead:
            data["lead"] = {
                "id": reply.lead.id,
                "businessName": reply.lead.business_name,
                "campaignId": reply.lead.campaign_id,
                "pipelineStage": reply.lead.pipeline_stage,
            }
        out.append(data)
    return {"replies": out}


@router.post("/replies/{reply_id}/read")
def mark_read(reply_id: int, read: bool = True, db: Session = Depends(get_db)) -> dict:
    reply = db.get(Reply, reply_id)
    if not reply:
        raise HTTPException(status_code=404, detail="Reply not found")
    reply.is_read = read
    db.commit()
    return {"ok": True, "reply": reply.to_dict()}


@router.post("/replies/mark-all-read")
def mark_all_read(db: Session = Depends(get_db)) -> dict:
    from sqlalchemy import update

    result = db.execute(update(Reply).where(Reply.is_read.is_(False)).values(is_read=True))
    db.commit()
    return {"ok": True, "updated": int(result.rowcount or 0)}


@router.post("/sync")
def sync_inbox(limit: int = 100, db: Session = Depends(get_db)) -> dict:
    accounts = db.execute(select(EmailAccount).where(EmailAccount.is_active.is_(True))).scalars().all()
    if not accounts:
        raise HTTPException(status_code=400, detail="No active sending accounts configured")
    service = get_inbox_service()
    results = []
    for account in accounts:
        if not account.imap_host:
            results.append({"accountId": account.id, "ok": False, "error": "no IMAP host"})
            continue
        result = service.sync_account(account.id, limit=limit)
        service.record_sync(account.id)
        results.append({"accountId": account.id, "email": account.email, **result})
    return {"ok": True, "results": results}


@router.get("/pipeline")
def pipeline(stage: str | None = None, q: str | None = None, db: Session = Depends(get_db)) -> dict:
    stmt = select(Lead).where(Lead.email != "")
    if stage:
        stmt = stmt.where(Lead.pipeline_stage == stage)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Lead.business_name.ilike(like), Lead.email.ilike(like)))
    leads = db.execute(stmt.order_by(Lead.updated_at.desc()).limit(500)).scalars().all()
    board: dict[str, list[dict]] = {stage_name: [] for stage_name in PipelineStage.ORDER}
    for lead in leads:
        entry = lead.to_dict()
        entry["replyCount"] = len(lead.replies)
        entry["lastReplyAt"] = (
            max((r.received_at for r in lead.replies if r.received_at), default=None)
        )
        if entry["lastReplyAt"]:
            entry["lastReplyAt"] = entry["lastReplyAt"].isoformat()
        board.setdefault(lead.pipeline_stage, []).append(entry)
    return {"board": board, "stageOrder": PipelineStage.ORDER}


@router.post("/leads/{lead_id}/stage")
def set_stage(lead_id: int, payload: StageUpdate, db: Session = Depends(get_db)) -> dict:
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if payload.pipeline_stage not in PipelineStage.ORDER:
        raise HTTPException(status_code=400, detail=f"Unknown stage '{payload.pipeline_stage}'")
    previous = lead.pipeline_stage
    lead.pipeline_stage = payload.pipeline_stage
    if payload.pipeline_stage in {PipelineStage.WON, PipelineStage.LOST}:
        lead.selected = False
    db.add(
        Activity(
            lead_id=lead.id,
            kind="stage_change",
            payload={"from": previous, "to": payload.pipeline_stage},
        )
    )
    db.commit()
    return {"ok": True, "lead": lead.to_dict()}


@router.get("/leads/{lead_id}")
def lead_detail(lead_id: int, db: Session = Depends(get_db)) -> dict:
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    messages = db.execute(
        select(OutboundMessage).where(OutboundMessage.lead_id == lead_id).order_by(OutboundMessage.id)
    ).scalars().all()
    activities = db.execute(
        select(Activity).where(Activity.lead_id == lead_id).order_by(Activity.id.desc()).limit(100)
    ).scalars().all()
    return {
        "lead": lead.to_dict(),
        "messages": [m.to_dict() for m in messages],
        "replies": [r.to_dict() for r in lead.replies],
        "activities": [a.to_dict() for a in activities],
    }


@router.post("/leads/{lead_id}/notes")
def add_note(lead_id: int, payload: NoteCreate, db: Session = Depends(get_db)) -> dict:
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    activity = Activity(lead_id=lead.id, kind=payload.kind, note=payload.note)
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return {"ok": True, "activity": activity.to_dict()}


@router.delete("/leads/{lead_id}")
def delete_lead(lead_id: int, db: Session = Depends(get_db)) -> dict:
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    db.delete(lead)
    db.commit()
    return {"ok": True}


# -------------------------------------------------------------- suppression
@router.get("/suppressions")
def list_suppressions(db: Session = Depends(get_db)) -> dict:
    rows = db.execute(select(Suppression).order_by(Suppression.id.desc())).scalars().all()
    return {"suppressions": [r.to_dict() for r in rows]}


@router.post("/suppressions", status_code=201)
def add_suppression(payload: SuppressionCreate, db: Session = Depends(get_db)) -> dict:
    address = payload.email.lower()
    exists = db.execute(select(Suppression).where(Suppression.email == address)).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="Already suppressed")
    row = Suppression(
        email=address, domain=address.split("@")[-1], reason=payload.reason
    )
    db.add(row)
    # keep existing leads consistent
    from sqlalchemy import update

    db.execute(
        update(Lead)
        .where(Lead.email == address)
        .values(is_suppressed=True, selected=False, status=LeadStatus.UNSUBSCRIBED)
    )
    db.commit()
    db.refresh(row)
    return {"ok": True, "suppression": row.to_dict()}


@router.delete("/suppressions/{suppression_id}")
def remove_suppression(suppression_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(Suppression, suppression_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(row)
    db.commit()
    return {"ok": True}
