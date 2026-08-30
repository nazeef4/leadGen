"""Sending-account management: credentials, connection tests, quota presets."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import EmailAccount, OutboundMessage
from ..schemas import AccountCreate, AccountUpdate, ConnectionTest, GenericResponse
from ..security import get_vault
from ..services.inbox_sync import ImapInbox
from ..services.sender import PROVIDER_PRESETS, SmtpSender, guess_provider
from ..services.delay import hour_window, utcnow

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("")
def list_accounts(db: Session = Depends(get_db)) -> dict:
    accounts = db.execute(select(EmailAccount).order_by(EmailAccount.id)).scalars().all()
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for account in accounts:
        data = account.to_dict()
        from sqlalchemy import func

        sent_today = db.execute(
            select(func.count(OutboundMessage.id)).where(
                OutboundMessage.account_id == account.id,
                OutboundMessage.sent_at.is_not(None),
                OutboundMessage.sent_at >= today,
            )
        ).scalar_one()
        data["sentToday"] = int(sent_today or 0)
        data["quotaRemaining"] = max(0, account.daily_limit - data["sentToday"])
        out.append(data)
    return {"accounts": out, "presets": PROVIDER_PRESETS}


@router.post("", status_code=201)
def create_account(payload: AccountCreate, db: Session = Depends(get_db)) -> dict:
    exists = db.execute(
        select(EmailAccount).where(EmailAccount.email == payload.email.lower())
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="That sending address is already configured")

    provider = payload.provider or guess_provider(payload.email)
    preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["custom"])
    account = EmailAccount(
        email=payload.email.lower(),
        display_name=payload.display_name or payload.email.split("@")[0].replace(".", " ").title(),
        provider=provider,
        smtp_host=payload.smtp_host or preset["smtp_host"],
        smtp_port=payload.smtp_port or preset["smtp_port"],
        smtp_security=payload.smtp_security or preset["smtp_security"],
        imap_host=payload.imap_host or preset["imap_host"],
        imap_port=payload.imap_port or preset["imap_port"],
        imap_security=payload.imap_security or preset["imap_security"],
        auth_mode=payload.auth_mode,
        daily_limit=payload.daily_limit or preset["daily_limit"],
        hourly_limit=payload.hourly_limit or preset["hourly_limit"],
        signature_html=payload.signature_html,
    )
    if payload.password:
        account.credential_enc = get_vault().encrypt(payload.password)
    db.add(account)
    db.commit()
    db.refresh(account)
    return {"account": account.to_dict()}


@router.patch("/{account_id}")
def update_account(
    account_id: int, payload: AccountUpdate, db: Session = Depends(get_db)
) -> dict:
    account = db.get(EmailAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    data = payload.model_dump(exclude_unset=True)
    password = data.pop("password", None)
    for key, value in data.items():
        setattr(account, key, value)
    if password:
        account.credential_enc = get_vault().encrypt(password)
        account.is_verified = False
    db.commit()
    db.refresh(account)
    return {"account": account.to_dict()}


@router.delete("/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)) -> GenericResponse:
    account = db.get(EmailAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    db.delete(account)
    db.commit()
    return GenericResponse(detail="Account removed")


@router.post("/{account_id}/test")
def test_account(
    account_id: int, payload: ConnectionTest, db: Session = Depends(get_db)
) -> dict:
    account = db.get(EmailAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    password = get_vault().decrypt(account.credential_enc)
    if not password:
        raise HTTPException(
            status_code=400,
            detail="No stored app password for this account — add one before testing",
        )
    result: dict = {"smtp": None, "imap": None}
    if payload.smtp:
        sender = SmtpSender(
            account.smtp_host, account.smtp_port, account.email, password, account.smtp_security
        )
        ok, detail = sender.test_connection()
        result["smtp"] = {"ok": ok, "detail": detail}
    if payload.imap and account.imap_host:
        inbox = ImapInbox(
            account.imap_host, account.imap_port, account.email, password, account.imap_security
        )
        ok, detail = inbox.test_connection()
        result["imap"] = {"ok": ok, "detail": detail}
    verified = all(v["ok"] for v in result.values() if v)
    account.is_verified = verified
    account.last_verified_at = utcnow()
    account.last_error = "" if verified else "; ".join(
        str(v.get("detail")) for v in result.values() if v and not v["ok"]
    )
    db.commit()
    return {"verified": verified, "results": result, "account": account.to_dict()}


@router.get("/presets")
def presets() -> dict:
    return {"presets": PROVIDER_PRESETS}


@router.get("/guess-provider")
def guess(email: str) -> dict:
    provider = guess_provider(email)
    return {"email": email, "provider": provider, "preset": PROVIDER_PRESETS[provider]}
