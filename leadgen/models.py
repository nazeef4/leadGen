"""ORM models — the whole local CRM lives in these tables."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LeadStatus:
    NEW = "new"
    QUEUED = "queued"
    SENT = "sent"
    REPLIED = "replied"
    FAILED = "failed"
    SKIPPED = "skipped"
    EXCLUDED = "excluded"
    UNSUBSCRIBED = "unsubscribed"
    ALL = [NEW, QUEUED, SENT, REPLIED, FAILED, SKIPPED, EXCLUDED, UNSUBSCRIBED]


class PipelineStage:
    NEW = "new"
    CONTACTED = "contacted"
    REPLIED = "replied"
    ENGAGED = "engaged"
    MEETING = "meeting"
    PROPOSAL = "proposal"
    WON = "won"
    LOST = "lost"
    ORDER = [NEW, CONTACTED, REPLIED, ENGAGED, MEETING, PROPOSAL, WON, LOST]


class MessageStatus:
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    BOUNCED = "bounced"
    SKIPPED = "skipped"


class CampaignStatus:
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED = "stopped"


class Intent:
    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    OUT_OF_OFFICE = "out_of_office"
    AUTO_REPLY = "auto_reply"
    SPAM = "spam"
    QUESTION = "question"
    UNKNOWN = "unknown"


class EmailAccount(Base):
    __tablename__ = "email_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    provider: Mapped[str] = mapped_column(String(32), default="gmail")
    smtp_host: Mapped[str] = mapped_column(String(255), default="")
    smtp_port: Mapped[int] = mapped_column(Integer, default=587)
    smtp_security: Mapped[str] = mapped_column(String(16), default="starttls")  # starttls|ssl|none
    imap_host: Mapped[str] = mapped_column(String(255), default="")
    imap_port: Mapped[int] = mapped_column(Integer, default=993)
    imap_security: Mapped[str] = mapped_column(String(16), default="ssl")  # ssl|none
    credential_enc: Mapped[str] = mapped_column(Text, default="")  # encrypted app password
    auth_mode: Mapped[str] = mapped_column(String(16), default="password")  # password|oauth
    oauth_refresh_token_enc: Mapped[str] = mapped_column(Text, default="")
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_limit: Mapped[int] = mapped_column(Integer, default=400)
    hourly_limit: Mapped[int] = mapped_column(Integer, default=60)
    signature_html: Mapped[str] = mapped_column(Text, default="")
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, default="")
    warmup_day: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    messages: Mapped[list["OutboundMessage"]] = relationship(back_populates="account")

    def to_dict(self, include_secret_state: bool = False) -> dict:
        data = {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "provider": self.provider,
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port,
            "smtp_security": self.smtp_security,
            "imap_host": self.imap_host,
            "imap_port": self.imap_port,
            "imap_security": self.imap_security,
            "auth_mode": self.auth_mode,
            "has_credential": bool(self.credential_enc or self.oauth_refresh_token_enc),
            "is_verified": self.is_verified,
            "is_active": self.is_active,
            "daily_limit": self.daily_limit,
            "hourly_limit": self.hourly_limit,
            "signature_html": self.signature_html,
            "warmup_day": self.warmup_day,
            "last_verified_at": self.last_verified_at.isoformat() if self.last_verified_at else None,
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        return data


class Campaign(Base):
    """A targeting + offer + copy configuration bound to a set of leads."""

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    niche: Mapped[str] = mapped_column(String(255), default="")
    service_offering: Mapped[str] = mapped_column(Text, default="")
    geo_filter: Mapped[dict] = mapped_column(JSON, default=dict)
    offers: Mapped[dict] = mapped_column(JSON, default=dict)
    tone: Mapped[str] = mapped_column(String(32), default="professional")
    template_key: Mapped[str] = mapped_column(String(64), default="consultative")
    sender_account_id: Mapped[int | None] = mapped_column(ForeignKey("email_accounts.id"))
    status: Mapped[str] = mapped_column(String(32), default=CampaignStatus.DRAFT, index=True)
    max_per_day: Mapped[int] = mapped_column(Integer, default=50)
    delay_min: Mapped[int] = mapped_column(Integer, default=45)
    delay_max: Mapped[int] = mapped_column(Integer, default=240)
    track_replies: Mapped[bool] = mapped_column(Boolean, default=True)
    send_html: Mapped[bool] = mapped_column(Boolean, default=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    leads: Mapped[list["Lead"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    messages: Mapped[list["OutboundMessage"]] = relationship(back_populates="campaign")

    def to_dict(self, counts: dict | None = None) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "niche": self.niche,
            "service_offering": self.service_offering,
            "geo_filter": self.geo_filter or {},
            "offers": self.offers or {},
            "tone": self.tone,
            "template_key": self.template_key,
            "sender_account_id": self.sender_account_id,
            "status": self.status,
            "max_per_day": self.max_per_day,
            "delay_min": self.delay_min,
            "delay_max": self.delay_max,
            "track_replies": self.track_replies,
            "send_html": self.send_html,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "counts": counts or {},
        }


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (UniqueConstraint("email", "campaign_id", name="uq_lead_email_campaign"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    business_name: Mapped[str] = mapped_column(String(255), default="")
    contact_name: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(255), index=True)
    phone: Mapped[str] = mapped_column(String(64), default="")
    website: Mapped[str] = mapped_column(String(512), default="")
    address: Mapped[str] = mapped_column(String(512), default="")
    city: Mapped[str] = mapped_column(String(128), default="", index=True)
    state: Mapped[str] = mapped_column(String(128), default="", index=True)
    country: Mapped[str] = mapped_column(String(128), default="", index=True)
    category: Mapped[str] = mapped_column(String(255), default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(64), default="manual")
    source_url: Mapped[str] = mapped_column(String(1024), default="")
    rating: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[int] = mapped_column(Integer, default=50, index=True)
    signals: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default=LeadStatus.NEW, index=True)
    pipeline_stage: Mapped[str] = mapped_column(String(32), default=PipelineStage.NEW, index=True)
    selected: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    campaign: Mapped[Campaign | None] = relationship(back_populates="leads")
    messages: Mapped[list["OutboundMessage"]] = relationship(back_populates="lead")
    replies: Mapped[list["Reply"]] = relationship(back_populates="lead")
    activities: Mapped[list["Activity"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )

    @staticmethod
    def dedupe_key(email: str, campaign_id: int | None) -> str:
        return hashlib.sha1(f"{email.lower().strip()}|{campaign_id}".encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "campaign_id": self.campaign_id,
            "business_name": self.business_name,
            "contact_name": self.contact_name,
            "email": self.email,
            "phone": self.phone,
            "website": self.website,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "country": self.country,
            "category": self.category,
            "snippet": self.snippet,
            "source": self.source,
            "source_url": self.source_url,
            "rating": self.rating,
            "review_count": self.review_count,
            "score": self.score,
            "signals": self.signals or {},
            "status": self.status,
            "pipeline_stage": self.pipeline_stage,
            "selected": self.selected,
            "is_suppressed": self.is_suppressed,
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class OutboundMessage(Base):
    __tablename__ = "outbound_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"), index=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("email_accounts.id"), index=True)
    rfc_message_id: Mapped[str] = mapped_column(String(255), index=True, default="")
    thread_id: Mapped[str] = mapped_column(String(128), index=True, default="")
    subject: Mapped[str] = mapped_column(String(512), default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default=MessageStatus.QUEUED, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    delay_seconds: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    compliance_score: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    lead: Mapped[Lead] = relationship(back_populates="messages")
    campaign: Mapped[Campaign | None] = relationship(back_populates="messages")
    account: Mapped[EmailAccount | None] = relationship(back_populates="messages")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "lead_id": self.lead_id,
            "campaign_id": self.campaign_id,
            "account_id": self.account_id,
            "rfc_message_id": self.rfc_message_id,
            "thread_id": self.thread_id,
            "subject": self.subject,
            "body_text": self.body_text,
            "status": self.status,
            "attempts": self.attempts,
            "delay_seconds": self.delay_seconds,
            "scheduled_for": self.scheduled_for.isoformat() if self.scheduled_for else None,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "error": self.error,
            "compliance_score": self.compliance_score,
        }


class Reply(Base):
    __tablename__ = "replies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), index=True)
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("outbound_messages.id", ondelete="SET NULL"), index=True
    )
    account_id: Mapped[int | None] = mapped_column(ForeignKey("email_accounts.id"), index=True)
    from_email: Mapped[str] = mapped_column(String(255), index=True)
    from_name: Mapped[str] = mapped_column(String(255), default="")
    subject: Mapped[str] = mapped_column(String(512), default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    intent: Mapped[str] = mapped_column(String(32), default=Intent.UNKNOWN, index=True)
    sentiment: Mapped[str] = mapped_column(String(16), default="neutral")
    matched_by: Mapped[str] = mapped_column(String(32), default="address")  # reference|subject|address
    imap_uid: Mapped[str] = mapped_column(String(64), index=True, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    lead: Mapped[Lead | None] = relationship(back_populates="replies")
    message: Mapped[OutboundMessage | None] = relationship()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "lead_id": self.lead_id,
            "message_id": self.message_id,
            "account_id": self.account_id,
            "from_email": self.from_email,
            "from_name": self.from_name,
            "subject": self.subject,
            "snippet": self.snippet,
            "body": self.body,
            "intent": self.intent,
            "sentiment": self.sentiment,
            "matched_by": self.matched_by,
            "imap_uid": self.imap_uid,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "is_read": self.is_read,
        }


class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="note")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    lead: Mapped[Lead | None] = relationship(back_populates="activities")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "lead_id": self.lead_id,
            "kind": self.kind,
            "payload": self.payload or {},
            "note": self.note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Suppression(Base):
    """Global do-not-contact list — honoured by every send path."""

    __tablename__ = "suppressions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    domain: Mapped[str] = mapped_column(String(255), default="", index=True)
    reason: Mapped[str] = mapped_column(String(64), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "domain": self.domain,
            "reason": self.reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class SyncState(Base):
    """Watermarks for the IMAP poller and scheduler bookkeeping."""

    __tablename__ = "sync_state"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
