"""Dispatch engine.

Owns the sending loop for one campaign at a time:

1. materialises a queued :class:`OutboundMessage` per selected lead (copy is
   generated once, stored, and therefore auditable/reproducible);
2. plans an **unordered, randomly delayed** send sequence with
   :class:`~leadgen.services.delay.DelayPlanner`;
3. before every single send it re-checks the live quota (sent today / this hour),
   the minimum gap since the last send, quiet hours and the suppression list;
4. sleeps in small slices so pause/stop are responsive.

Only one engine instance runs per process.  Everything is persisted to SQLite,
so a restart resumes from where it stopped.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import (
    Activity,
    Campaign,
    CampaignStatus,
    EmailAccount,
    Lead,
    LeadStatus,
    MessageStatus,
    OutboundMessage,
    Suppression,
)
from ..security import get_vault
from .compliance import get_compliance_engine
from .copywriter import OfferConfig, get_copywriter
from .delay import DelayConfig, DelayPlanner, is_within_quiet_hours, utcnow
from .niche_advisor import get_niche_advisor
from .sender import DryRunSender, MessagePayload, SmtpSender, new_thread_id

log = logging.getLogger("leadgen.dispatch")


@dataclass
class EngineState:
    running: bool = False
    paused: bool = False
    campaign_id: int | None = None
    campaign_name: str = ""
    total: int = 0
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    remaining: int = 0
    sent_today: int = 0
    sent_this_hour: int = 0
    daily_cap: int = 0
    next_send_at: datetime | None = None
    last_send_at: datetime | None = None
    current_delay_seconds: int = 0
    message: str = "idle"
    dry_run: bool = False
    started_at: datetime | None = None
    errors: list[str] = field(default_factory=list)
    recent: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "running": self.running,
            "paused": self.paused,
            "campaignId": self.campaign_id,
            "campaignName": self.campaign_name,
            "total": self.total,
            "sent": self.sent,
            "failed": self.failed,
            "skipped": self.skipped,
            "remaining": self.remaining,
            "sentToday": self.sent_today,
            "sentThisHour": self.sent_this_hour,
            "dailyCap": self.daily_cap,
            "nextSendAt": self.next_send_at.isoformat() if self.next_send_at else None,
            "lastSendAt": self.last_send_at.isoformat() if self.last_send_at else None,
            "currentDelaySeconds": self.current_delay_seconds,
            "message": self.message,
            "dryRun": self.dry_run,
            "startedAt": self.started_at.isoformat() if self.started_at else None,
            "errors": self.errors[-20:],
            "recent": self.recent[-25:],
        }


class DispatchEngine:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.compliance = get_compliance_engine()
        self.copywriter = get_copywriter()
        self.advisor = get_niche_advisor()
        self.state = EngineState(daily_cap=self.settings.daily_recipient_cap)
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._sender_override = None  # tests inject DryRunSender here

    # ------------------------------------------------------------------ API
    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, campaign_id: int, dry_run: bool = False) -> tuple[bool, str]:
        with self._lock:
            if self.busy:
                return False, "A campaign is already running. Stop it first."
            self._stop.clear()
            self._pause.clear()
            self.state = EngineState(
                running=True,
                campaign_id=campaign_id,
                dry_run=dry_run,
                started_at=utcnow(),
                daily_cap=self.settings.daily_recipient_cap,
                message="Preparing queue…",
            )
            self._thread = threading.Thread(
                target=self._run, args=(campaign_id, dry_run), daemon=True
            )
            self._thread.start()
        return True, "Dispatch started"

    def pause(self) -> bool:
        if not self.busy:
            return False
        self._pause.set()
        self.state.paused = True
        self.state.message = "Paused by user"
        return True

    def resume(self) -> bool:
        if not self.busy:
            return False
        self._pause.clear()
        self.state.paused = False
        self.state.message = "Resumed"
        return True

    def stop(self) -> bool:
        if not self.busy:
            return False
        self._stop.set()
        self._pause.clear()
        self.state.message = "Stopping after the current message…"
        return True

    # -------------------------------------------------------------- helpers
    def _sleep_interruptible(self, seconds: float) -> bool:
        """Sleep in 0.5s slices.  Returns False if the engine was stopped."""
        end = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < end:
            if self._stop.is_set():
                return False
            if self._pause.is_set():
                self.state.message = "Paused"
                while self._pause.is_set() and not self._stop.is_set():
                    time.sleep(0.5)
                if self._stop.is_set():
                    return False
            time.sleep(min(0.5, max(0.0, end - time.monotonic())))
        return not self._stop.is_set()

    def _count_sent(self, account_id: int | None) -> tuple[int, int, datetime | None]:
        now = utcnow()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        hour_start = now.replace(minute=0, second=0, microsecond=0)
        with session_scope() as session:
            base = select(func.count(OutboundMessage.id)).where(
                OutboundMessage.sent_at.is_not(None)
            )
            if account_id:
                base = base.where(OutboundMessage.account_id == account_id)
            today = session.execute(
                base.where(OutboundMessage.sent_at >= day_start)
            ).scalar_one()
            this_hour = session.execute(
                base.where(OutboundMessage.sent_at >= hour_start)
            ).scalar_one()
            last = session.execute(
                select(func.max(OutboundMessage.sent_at)).where(
                    OutboundMessage.sent_at.is_not(None)
                )
            ).scalar_one_or_none()
        return int(today or 0), int(this_hour or 0), last

    def _suppressed_emails(self) -> set[str]:
        with session_scope() as session:
            rows = session.execute(select(Suppression.email, Suppression.domain)).all()
        emails = {e.lower() for e, _ in rows if e}
        domains = {d.lower() for _, d in rows if d}
        return emails | {f"@{d}" for d in domains}

    @staticmethod
    def _is_suppressed(email: str, suppressed: set[str]) -> bool:
        email = (email or "").lower()
        if email in suppressed:
            return True
        domain = email.split("@")[-1] if "@" in email else ""
        return f"@{domain}" in suppressed

    def _build_sender(self, account: EmailAccount):
        if self._sender_override is not None:
            return self._sender_override
        password = get_vault(self.settings).decrypt(account.credential_enc)
        return SmtpSender(
            host=account.smtp_host,
            port=account.smtp_port,
            username=account.email,
            password=password,
            security=account.smtp_security,
            timeout=self.settings.scrape_request_timeout,
        )

    # ------------------------------------------------------------- queueing
    def prepare_queue(
        self, campaign_id: int, limit: int = 500, prefer_llm: bool = False
    ) -> dict:
        """Generate copy + queued messages for every selected lead of a campaign."""
        created = 0
        skipped = 0
        suppressed = self._suppressed_emails()
        with session_scope() as session:
            campaign = session.get(Campaign, campaign_id)
            if not campaign:
                return {"created": 0, "skipped": 0, "error": "campaign not found"}
            account = (
                session.get(EmailAccount, campaign.sender_account_id)
                if campaign.sender_account_id
                else None
            )
            offers = OfferConfig.from_dict(campaign.offers)
            suggestion = self.advisor.suggest(
                campaign.service_offering or campaign.niche,
                campaign.geo_filter,
                use_llm=False,
                top_n=5,
            )
            hooks = suggestion["hooks"]
            campaign_dict = campaign.to_dict()
            if account:
                campaign_dict["sender_name"] = account.display_name or account.email.split("@")[0]

            leads = session.execute(
                select(Lead).where(
                    Lead.campaign_id == campaign_id,
                    Lead.selected.is_(True),
                    Lead.status.in_([LeadStatus.NEW, LeadStatus.QUEUED, LeadStatus.FAILED]),
                )
            ).scalars().all()

            existing = {
                m.lead_id
                for m in session.execute(
                    select(OutboundMessage).where(
                        OutboundMessage.campaign_id == campaign_id,
                        OutboundMessage.status.in_([MessageStatus.QUEUED, MessageStatus.SENT]),
                    )
                ).scalars().all()
            }

            for lead in leads[:limit]:
                if lead.id in existing:
                    skipped += 1
                    continue
                if not lead.email:
                    lead.status = LeadStatus.SKIPPED
                    lead.last_error = "no email address"
                    skipped += 1
                    continue
                if self._is_suppressed(lead.email, suppressed) or lead.is_suppressed:
                    lead.status = LeadStatus.UNSUBSCRIBED
                    lead.is_suppressed = True
                    lead.last_error = "suppressed"
                    skipped += 1
                    continue
                copy = self.copywriter.generate(
                    lead.to_dict(), campaign_dict, offers, hooks, prefer_llm=prefer_llm
                )
                message = OutboundMessage(
                    lead_id=lead.id,
                    campaign_id=campaign_id,
                    account_id=account.id if account else None,
                    subject=copy.subject,
                    body_text=copy.body_text,
                    body_html=copy.body_html if campaign.send_html else "",
                    status=MessageStatus.QUEUED,
                    thread_id=new_thread_id(),
                )
                session.add(message)
                lead.status = LeadStatus.QUEUED
                created += 1
            if campaign.status == CampaignStatus.DRAFT:
                campaign.status = CampaignStatus.READY
        return {"created": created, "skipped": skipped}

    # ------------------------------------------------------------------ run
    def _run(self, campaign_id: int, dry_run: bool) -> None:
        state = self.state
        try:
            with session_scope() as session:
                campaign = session.get(Campaign, campaign_id)
                if not campaign:
                    state.running = False
                    state.message = "Campaign not found"
                    return
                state.campaign_name = campaign.name
                account_id = campaign.sender_account_id
                delay_min, delay_max = campaign.delay_min, campaign.delay_max
                max_per_day = campaign.max_per_day
                campaign.status = CampaignStatus.RUNNING
                campaign.started_at = campaign.started_at or utcnow()

            if not dry_run and account_id is None:
                state.message = "No sending account attached to this campaign"
                state.running = False
                self._finish(campaign_id, CampaignStatus.STOPPED)
                return

            account = None
            if account_id and not dry_run:
                with session_scope() as session:
                    row = session.get(EmailAccount, account_id)
                    account = EmailAccount(
                        id=row.id, email=row.email, display_name=row.display_name,
                        smtp_host=row.smtp_host, smtp_port=row.smtp_port,
                        smtp_security=row.smtp_security, credential_enc=row.credential_enc,
                        daily_limit=row.daily_limit, hourly_limit=row.hourly_limit,
                    )
                    session.expunge(row)

            sender = (
                self._sender_override
                if self._sender_override is not None
                else (DryRunSender() if dry_run else self._build_sender(account))
            )
            if not dry_run and self._sender_override is None:
                ok, detail = sender.test_connection()
                if not ok:
                    state.message = f"SMTP check failed: {detail}"
                    state.errors.append(detail)
                    state.running = False
                    self._finish(campaign_id, CampaignStatus.STOPPED)
                    return

            cfg = DelayConfig(
                min_seconds=delay_min,
                max_seconds=delay_max,
                long_pause_every=self.settings.long_pause_every,
                long_pause_min_seconds=self.settings.long_pause_min_seconds,
                long_pause_max_seconds=self.settings.long_pause_max_seconds,
                daily_cap=min(self.settings.daily_recipient_cap, max_per_day or 10**6),
                hourly_cap=min(self.settings.hourly_recipient_cap, max_per_day or 10**6),
                enforce_quiet_hours=self.settings.enforce_quiet_hours,
                quiet_start_hour=self.settings.quiet_hours_start,
                quiet_end_hour=self.settings.quiet_hours_end,
            )
            problems = cfg.validate()
            if problems:
                state.errors.extend(problems)

            queued = self._load_queued(campaign_id)
            state.total = len(queued)
            state.remaining = len(queued)
            if not queued:
                state.message = "Nothing queued — run 'Prepare queue' first"
                state.running = False
                self._finish(campaign_id, CampaignStatus.COMPLETED)
                return

            planner = DelayPlanner(cfg)
            plan = planner.plan(
                [m["id"] for m in queued],
                already_sent_today=self._count_sent(account_id)[0],
            )
            order = {slot.lead_id: slot for slot in plan.slots}
            sequence = [m for m in queued if m["id"] in order]
            # plan.slots is already shuffled; keep that order
            by_id = {m["id"]: m for m in sequence}
            sequence = [by_id[s.lead_id] for s in plan.slots if s.lead_id in by_id]

            consecutive_failures = 0
            for index, message_row in enumerate(sequence):
                if not self._sleep_interruptible(0.1):
                    break
                if self._stop.is_set():
                    break
                if self._pause.is_set():
                    while self._pause.is_set() and not self._stop.is_set():
                        time.sleep(0.5)
                    if self._stop.is_set():
                        break

                slot = order[message_row["id"]]
                sent_today, sent_this_hour, last_send = self._count_sent(account_id)
                state.sent_today, state.sent_this_hour = sent_today, sent_this_hour

                suppressed = self._suppressed_emails()
                quiet = is_within_quiet_hours(utcnow(), cfg)
                report = self.compliance.full_check(
                    message_row["subject"],
                    message_row["body_text"],
                    message_row["body_html"],
                    now=utcnow(),
                    sent_today=sent_today,
                    sent_this_hour=sent_this_hour,
                    last_send_at=last_send,
                    suppressed=self._is_suppressed(message_row["email"], suppressed),
                    quiet_hours_active=quiet,
                    daily_cap=cfg.daily_cap,
                    hourly_cap=cfg.hourly_cap,
                    min_gap_seconds=cfg.min_seconds,
                    pending_gap_seconds=0 if index == 0 else slot.delay_seconds,
                )
                if report.blocked:
                    blocking = [i for i in report.issues if i.severity == "block"]
                    reason = blocking[0].message if blocking else "blocked by compliance"
                    code = blocking[0].code if blocking else "blocked"
                    if code in {"daily_cap", "hourly_cap", "quiet_hours", "burst"}:
                        wait = self._seconds_to_resume(code, cfg, utcnow())
                        state.message = f"Holding: {reason} (resuming in {wait}s)"
                        if not self._sleep_interruptible(min(wait, 3600)):
                            break
                        continue
                    self._mark_message(message_row["id"], MessageStatus.SKIPPED, reason)
                    self._mark_lead(message_row["lead_id"], LeadStatus.SKIPPED, reason)
                    state.skipped += 1
                    state.remaining -= 1
                    state.errors.append(f"skipped {message_row['email']}: {reason}")
                    continue

                state.current_delay_seconds = slot.delay_seconds
                state.next_send_at = utcnow() + timedelta(seconds=slot.delay_seconds)
                if index > 0 and slot.delay_seconds > 0:
                    state.message = (
                        f"Waiting {slot.delay_seconds}s before the next send "
                        f"({'long break' if slot.long_pause else 'humanised gap'})"
                    )
                    if not self._sleep_interruptible(slot.delay_seconds):
                        break

                state.message = f"Sending to {message_row['email']}"
                payload = MessagePayload(
                    to_email=message_row["email"],
                    to_name=message_row["contact_name"] or message_row["business_name"],
                    subject=message_row["subject"],
                    body_text=message_row["body_text"],
                    body_html=message_row["body_html"],
                    from_email=account.email if account else "preview@leadgen.local",
                    from_name=(account.display_name if account else "") or "",
                    reply_to=account.email if account else "",
                    list_unsubscribe_url=self.settings.unsubscribe_url,
                    campaign_id=campaign_id,
                    lead_id=message_row["lead_id"],
                    thread_id=message_row["thread_id"],
                )
                result = sender.send(payload)
                state.last_send_at = utcnow()
                if result.ok:
                    self._mark_message(
                        message_row["id"], MessageStatus.SENT, "",
                        rfc_message_id=result.message_id or payload.message_id,
                        delay_seconds=slot.delay_seconds,
                        compliance_score=report.score,
                    )
                    self._mark_lead(message_row["lead_id"], LeadStatus.SENT)
                    state.sent += 1
                    consecutive_failures = 0
                    state.recent.append(
                        {"email": message_row["email"], "status": "sent",
                         "at": utcnow().isoformat(), "delay": slot.delay_seconds}
                    )
                else:
                    status = MessageStatus.BOUNCED if result.status == "bounced" else MessageStatus.FAILED
                    self._mark_message(message_row["id"], status, result.error)
                    self._mark_lead(
                        message_row["lead_id"],
                        LeadStatus.FAILED if not result.permanent else LeadStatus.SKIPPED,
                        result.error,
                    )
                    if result.permanent:
                        self._suppress(message_row["email"], "permanent bounce")
                    state.failed += 1
                    consecutive_failures += 1
                    state.errors.append(f"{message_row['email']}: {result.error}")
                    state.recent.append(
                        {"email": message_row["email"], "status": "failed",
                         "error": result.error, "at": utcnow().isoformat()}
                    )
                state.remaining -= 1
                state.message = (
                    f"Sent {state.sent}, failed {state.failed}, "
                    f"remaining {state.remaining}"
                )
                if consecutive_failures >= self.settings.max_consecutive_failures:
                    state.message = (
                        f"Stopped: {consecutive_failures} consecutive failures "
                        "(circuit breaker — check credentials/deliverability)"
                    )
                    state.errors.append(state.message)
                    break

            self._finish(
                campaign_id,
                CampaignStatus.COMPLETED if state.remaining <= 0 else CampaignStatus.PAUSED,
            )
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("dispatch failed")
            state.message = f"Dispatch error: {exc}"
            state.errors.append(str(exc))
            self._finish(campaign_id, CampaignStatus.STOPPED)
        finally:
            state.running = False
            state.paused = False

    def _load_queued(self, campaign_id: int) -> list[dict]:
        with session_scope() as session:
            rows = session.execute(
                select(OutboundMessage, Lead)
                .join(Lead, Lead.id == OutboundMessage.lead_id)
                .where(
                    OutboundMessage.campaign_id == campaign_id,
                    OutboundMessage.status == MessageStatus.QUEUED,
                )
            ).all()
            return [
                {
                    "id": m.id,
                    "lead_id": lead.id,
                    "email": lead.email,
                    "contact_name": lead.contact_name,
                    "business_name": lead.business_name,
                    "subject": m.subject,
                    "body_text": m.body_text,
                    "body_html": m.body_html,
                    "thread_id": m.thread_id,
                }
                for m, lead in rows
            ]

    @staticmethod
    def _seconds_to_resume(code: str, cfg: DelayConfig, now: datetime) -> int:
        if code == "daily_cap":
            tomorrow = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
            return max(60, int((tomorrow - now).total_seconds()))
        if code == "hourly_cap":
            next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            return max(30, int((next_hour - now).total_seconds()))
        if code == "quiet_hours":
            end = now.replace(hour=cfg.quiet_end_hour, minute=0, second=0, microsecond=0)
            if end <= now:
                end += timedelta(days=1)
            return max(60, int((end - now).total_seconds()))
        return max(30, cfg.min_seconds)

    def _mark_message(
        self,
        message_id: int,
        status: str,
        error: str = "",
        *,
        rfc_message_id: str = "",
        delay_seconds: int | None = None,
        compliance_score: int | None = None,
    ) -> None:
        with session_scope() as session:
            message = session.get(OutboundMessage, message_id)
            if not message:
                return
            message.status = status
            message.attempts += 1
            if error:
                message.error = error[:1000]
            if rfc_message_id:
                message.rfc_message_id = rfc_message_id
            if delay_seconds is not None:
                message.delay_seconds = delay_seconds
            if compliance_score is not None:
                message.compliance_score = compliance_score
            if status in {MessageStatus.SENT, MessageStatus.BOUNCED}:
                message.sent_at = utcnow()

    def _mark_lead(self, lead_id: int, status: str, error: str = "") -> None:
        with session_scope() as session:
            lead = session.get(Lead, lead_id)
            if not lead:
                return
            lead.status = status
            if error:
                lead.last_error = error[:500]
            if status == LeadStatus.SENT:
                from ..models import PipelineStage

                lead.pipeline_stage = PipelineStage.CONTACTED
            session.add(
                Activity(lead_id=lead_id, kind="outbound", payload={"status": status, "error": error[:200]})
            )

    def _suppress(self, email: str, reason: str) -> None:
        with session_scope() as session:
            exists = session.execute(
                select(Suppression).where(Suppression.email == email.lower())
            ).scalar_one_or_none()
            if exists:
                return
            session.add(
                Suppression(
                    email=email.lower(),
                    domain=email.split("@")[-1].lower() if "@" in email else "",
                    reason=reason,
                )
            )

    @staticmethod
    def _finish(campaign_id: int, status: str) -> None:
        with session_scope() as session:
            campaign = session.get(Campaign, campaign_id)
            if campaign:
                campaign.status = status
                if status in {CampaignStatus.COMPLETED, CampaignStatus.STOPPED}:
                    campaign.finished_at = utcnow()

    def requeue(self, campaign_id: int) -> int:
        """Put failed messages back in the queue (transient errors only)."""
        count = 0
        with session_scope() as session:
            messages = session.execute(
                select(OutboundMessage).where(
                    OutboundMessage.campaign_id == campaign_id,
                    OutboundMessage.status == MessageStatus.FAILED,
                )
            ).scalars().all()
            for message in messages:
                message.status = MessageStatus.QUEUED
                message.error = ""
                count += 1
            leads = session.execute(
                select(Lead).where(
                    Lead.campaign_id == campaign_id, Lead.status == LeadStatus.FAILED
                )
            ).scalars().all()
            for lead in leads:
                lead.status = LeadStatus.QUEUED
        return count


_engine: DispatchEngine | None = None


def get_engine() -> DispatchEngine:
    global _engine
    if _engine is None:
        _engine = DispatchEngine()
    return _engine
