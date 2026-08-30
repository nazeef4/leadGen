"""System: settings, health, compliance posture, LLM status."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import EmailAccount, Lead, OutboundMessage, Reply, Suppression
from ..schemas import SettingsUpdate
from ..services.compliance import get_compliance_engine
from ..services.delay import hour_window, utcnow
from ..services.dispatcher import get_engine
from ..services.llm import get_llm
from ..services.scrapers.pipeline import SCRAPER_REGISTRY, get_pipeline

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    engine = get_engine()
    accounts = db.execute(select(EmailAccount)).scalars().all()
    verified = [a for a in accounts if a.is_verified]
    now = utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = db.execute(
        select(func.count(OutboundMessage.id)).where(
            OutboundMessage.sent_at.is_not(None), OutboundMessage.sent_at >= day_start
        )
    ).scalar_one()
    settings = get_settings()
    return {
        "ok": True,
        "app": {"name": settings.app_name, "version": settings.version},
        "database": str(db.bind.url),
        "accounts": {
            "total": len(accounts),
            "verified": len(verified),
            "active": sum(1 for a in accounts if a.is_active),
        },
        "quota": {
            "sentToday": int(sent_today or 0),
            "dailyCap": settings.daily_recipient_cap,
            "remaining": max(0, settings.daily_recipient_cap - int(sent_today or 0)),
        },
        "dispatch": engine.state.to_dict(),
        "llm": get_llm().info(),
        "scrapers": sorted(SCRAPER_REGISTRY.keys()),
        "stateDir": str(settings.state_dir),
    }


@router.get("/settings")
def read_settings() -> dict:
    return {"settings": get_settings().export_public()}


@router.patch("/settings")
def write_settings(payload: SettingsUpdate) -> dict:
    """Mutates the in-process settings (persisted values belong in .env)."""
    settings = get_settings()
    data = payload.model_dump(exclude_unset=True)
    api_key = data.pop("llm_api_key", None)
    for key, value in data.items():
        setattr(settings, key, value)
    if api_key:
        settings.llm_api_key = api_key
        if settings.llm_provider == "offline":
            settings.llm_provider = "openai"
    from ..services import llm as llm_module

    llm_module.reset_llm()
    return {"settings": settings.export_public(), "detail": "Applied for this session — add to .env to persist"}


@router.get("/compliance-posture")
def compliance_posture(db: Session = Depends(get_db)) -> dict:
    """A live view of every guardrail and whether it currently binds."""
    settings = get_settings()
    engine = get_engine()
    now = utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    sent_today = int(
        db.execute(
            select(func.count(OutboundMessage.id)).where(
                OutboundMessage.sent_at.is_not(None), OutboundMessage.sent_at >= day_start
            )
        ).scalar_one()
        or 0
    )
    sent_this_hour = int(
        db.execute(
            select(func.count(OutboundMessage.id)).where(
                OutboundMessage.sent_at.is_not(None), OutboundMessage.sent_at >= hour_start
            )
        ).scalar_one()
        or 0
    )
    suppressed = int(db.execute(select(func.count(Suppression.id))).scalar_one() or 0)
    accounts = db.execute(select(EmailAccount)).scalars().all()
    caps = {
        "daily": {
            "limit": settings.daily_recipient_cap,
            "used": sent_today,
            "remaining": max(0, settings.daily_recipient_cap - sent_today),
            "note": "Google free accounts: 500 recipients/day hard ceiling",
        },
        "hourly": {
            "limit": settings.hourly_recipient_cap,
            "used": sent_this_hour,
            "remaining": max(0, settings.hourly_recipient_cap - sent_this_hour),
            "note": "Prevents burst-sending fingerprints",
        },
        "minGapSeconds": settings.min_delay_seconds,
        "maxGapSeconds": settings.max_delay_seconds,
        "longPauseEvery": settings.long_pause_every,
        "quietHours": {
            "enforced": settings.enforce_quiet_hours,
            "start": settings.quiet_hours_start,
            "end": settings.quiet_hours_end,
        },
        "suppressionList": suppressed,
        "perAccountLimits": [
            {"email": a.email, "daily": a.daily_limit, "hourly": a.hourly_limit} for a in accounts
        ],
    }
    checks = [
        {"id": "daily_cap", "label": "Daily recipient cap", "active": True, "value": settings.daily_recipient_cap},
        {"id": "hourly_cap", "label": "Hourly recipient cap", "active": True, "value": settings.hourly_recipient_cap},
        {"id": "randomised_delay", "label": "Randomised, unordered delays", "active": True,
         "value": f"{settings.min_delay_seconds}-{settings.max_delay_seconds}s + long pauses"},
        {"id": "quiet_hours", "label": "Quiet hours", "active": settings.enforce_quiet_hours,
         "value": f"{settings.quiet_hours_start}:00-{settings.quiet_hours_end}:00"},
        {"id": "suppression", "label": "Global suppression list", "active": suppressed > 0, "value": suppressed},
        {"id": "content_scan", "label": "Spam-phrase & content scan", "active": True,
         "value": "blocks opt-out-less or address-less mail"},
        {"id": "circuit_breaker", "label": "Failure circuit breaker", "active": True,
         "value": f"stop after {settings.max_consecutive_failures} consecutive failures"},
    ]
    return {
        "caps": caps,
        "checks": checks,
        "engineRunning": engine.state.running,
        "lastSendAt": engine.state.last_send_at.isoformat() if engine.state.last_send_at else None,
    }


@router.get("/scrapers")
def scrapers() -> dict:
    pipeline = get_pipeline()
    out = []
    for name, cls in SCRAPER_REGISTRY.items():
        instance = pipeline.build_scraper(name)
        out.append(
            {
                "name": name,
                "requiresKey": bool(getattr(cls, "requires_key", False)),
                "available": instance is not None,
            }
        )
    return {"scrapers": out}
