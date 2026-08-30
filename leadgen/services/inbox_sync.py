"""Inbox syncing and reply detection.

The transport (IMAP) and the logic (matching a reply to the message that
triggered it) are deliberately separate so the matching rules can be unit tested
without a mail server.

Matching order, strongest first:

1. ``In-Reply-To`` / ``References`` contains the RFC Message-ID we stored when
   we sent — exact, unambiguous.
2. Subject matches ``Re: <our subject>``.
3. Sender address matches a known lead in the account's campaigns.

Each match updates the lead to ``replied`` and moves it to the corresponding
pipeline stage; an explicit unsubscribe adds the address to the global
suppression list so no campaign can contact it again.
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import logging
import re
import ssl as ssl_module
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import Message as EmailMessageType

from sqlalchemy import select

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import (
    Activity,
    Campaign,
    CampaignStatus,
    EmailAccount,
    Lead,
    LeadStatus,
    OutboundMessage,
    PipelineStage,
    Reply,
    Suppression,
    SyncState,
)
from ..security import get_vault
from .classifier import classify_reply, summarise, wants_unsubscribe

log = logging.getLogger("leadgen.inbox")

SUBJECT_NORMALISE_RE = re.compile(r"^(re|fwd|fw|aw|antwoord|sv|vs)\s*:\s*", re.IGNORECASE)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class FetchedMessage:
    uid: str
    from_email: str
    from_name: str
    subject: str
    body: str
    date: datetime
    in_reply_to: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    raw_headers: dict = field(default_factory=dict)


@dataclass
class MatchResult:
    matched: bool
    message_id: int | None = None
    lead_id: int | None = None
    matched_by: str = ""
    reason: str = ""


def normalise_subject(subject: str) -> str:
    value = subject or ""
    previous = None
    while previous != value:
        previous = value
        value = SUBJECT_NORMALISE_RE.sub("", value).strip()
    return value.lower().strip()


def extract_addresses(value: str) -> list[str]:
    return re.findall(r"[\w.+-]+@[\w.-]+\.\w+", value or "")


def match_reply(
    fetched: FetchedMessage,
    *,
    message_ids: dict[str, tuple[int, int]],
    subjects: dict[str, tuple[int, int]],
    lead_emails: dict[str, int],
) -> MatchResult:
    """Resolve an inbound message to the outbound message / lead that caused it."""
    for header in [*fetched.in_reply_to, *fetched.references]:
        hit = message_ids.get(header.strip().lower())
        if hit:
            message_id, lead_id = hit
            return MatchResult(True, message_id, lead_id, "reference")
    key = normalise_subject(fetched.subject)
    if key and key in subjects:
        message_id, lead_id = subjects[key]
        return MatchResult(True, message_id, lead_id, "subject")
    lead_id = lead_emails.get((fetched.from_email or "").lower())
    if lead_id:
        return MatchResult(True, None, lead_id, "address")
    return MatchResult(False, reason="no matching outbound message or lead")


def parse_message(uid: str, raw: bytes) -> FetchedMessage | None:
    try:
        parsed: EmailMessageType = email.message_from_bytes(raw, policy=email.policy.default)
    except Exception as exc:  # pragma: no cover - malformed MIME
        log.warning("could not parse message %s: %s", uid, exc)
        return None
    from_header = str(parsed.get("From", ""))
    addresses = extract_addresses(from_header)
    from_email = addresses[0].lower() if addresses else ""
    name_match = re.match(r'"?([^"<@]+?)"?\s*<', from_header)
    from_name = name_match.group(1).strip() if name_match else ""
    date = parsed.get("Date")
    received = utcnow()
    if isinstance(date, datetime):
        received = date if date.tzinfo else date.replace(tzinfo=timezone.utc)
    body = _best_body(parsed)
    return FetchedMessage(
        uid=uid,
        from_email=from_email,
        from_name=from_name,
        subject=str(parsed.get("Subject", ""))[:500],
        body=body,
        date=received,
        in_reply_to=extract_addresses_of_ids(str(parsed.get("In-Reply-To", "")))
        or [v.strip() for v in str(parsed.get("In-Reply-To", "")).split() if "@" in v],        references=[v.strip() for v in str(parsed.get("References", "")).split() if "@" in v],
        raw_headers={k: str(v)[:200] for k, v in parsed.items() if k.lower() in
                     {"from", "to", "subject", "date", "in-reply-to", "references", "list-unsubscribe"}},
    )


def extract_addresses_of_ids(value: str) -> list[str]:
    """Pull message-ids out of a header, keeping the angle brackets.

    We store ``rfc_message_id`` in its canonical ``<local@domain>`` form, so the
    comparison key must keep the brackets too.
    """
    return [f"<{v.strip()}>" for v in re.findall(r"<([^>]+)>", value or "") if "@" in v]


def _best_body(parsed: EmailMessageType, limit: int = 20000) -> str:
    if parsed.is_multipart():
        plain, html = "", ""
        for part in parsed.walk():
            content_type = part.get_content_type()
            if part.get_content_disposition() == "attachment":
                continue
            try:
                payload = part.get_content()
            except Exception:  # pragma: no cover
                continue
            if content_type == "text/plain" and not plain:
                plain = payload if isinstance(payload, str) else str(payload)
            elif content_type == "text/html" and not html:
                html = payload if isinstance(payload, str) else str(payload)
        text = plain or _html_to_text(html)
    else:
        try:
            payload = parsed.get_content()
        except Exception:  # pragma: no cover
            payload = parsed.get_payload(decode=True) or b""
        if isinstance(payload, bytes):
            text = payload.decode("utf-8", errors="replace")
        else:
            text = payload if parsed.get_content_type() == "text/plain" else _html_to_text(str(payload))
    return (text or "")[:limit]


def _html_to_text(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html or "", flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return " ".join(text.split())


class ImapInbox:
    """Thin IMAP transport.  Read-only: nothing is deleted or flagged on the server."""

    def __init__(self, host: str, port: int, username: str, password: str, security: str = "ssl"):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.security = (security or "ssl").lower()

    def connect(self) -> imaplib.IMAP4:
        if self.security == "ssl":
            context = ssl_module.create_default_context()
            client: imaplib.IMAP4 = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=context)
        else:
            client = imaplib.IMAP4(self.host, self.port)
        client.login(self.username, self.password)
        client.select("INBOX", readonly=True)
        return client

    def test_connection(self) -> tuple[bool, str]:
        try:
            client = self.connect()
        except imaplib.IMAP4.error as exc:
            return False, f"IMAP login failed: {exc}"
        except (OSError, ssl_module.SSLError) as exc:
            return False, f"Could not reach {self.host}:{self.port} — {exc}"
        try:
            status, data = client.status("INBOX", "(MESSAGES)")
            return True, f"IMAP OK ({status}: {data})"
        finally:
            try:
                client.logout()
            except Exception:  # pragma: no cover
                pass

    def fetch_since(self, since: datetime, limit: int = 100) -> list[FetchedMessage]:
        client = self.connect()
        out: list[FetchedMessage] = []
        try:
            criteria = f'(SINCE "{since.strftime("%d-%b-%Y")}")'
            status, data = client.uid("SEARCH", None, criteria)
            if status != "OK" or not data or not data[0]:
                return out
            uids = data[0].split()[-limit:]
            for uid in uids:
                status, payload = client.uid("FETCH", uid, "(RFC822)")
                if status != "OK" or not payload or not payload[0]:
                    continue
                raw = payload[0][1] if isinstance(payload[0], tuple) else payload[0]
                parsed = parse_message(uid.decode() if isinstance(uid, bytes) else str(uid), raw)
                if parsed:
                    out.append(parsed)
        finally:
            try:
                client.logout()
            except Exception:  # pragma: no cover
                pass
        return out


class InboxSyncService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ API
    def sync_account(self, account_id: int, limit: int = 100, days_back: int = 14) -> dict:
        with session_scope() as session:
            row = session.get(EmailAccount, account_id)
            if not row:
                return {"ok": False, "error": "account not found"}
            account = EmailAccount(
                id=row.id, email=row.email, imap_host=row.imap_host, imap_port=row.imap_port,
                imap_security=row.imap_security, credential_enc=row.credential_enc,
            )
            session.expunge(row)
        password = get_vault(self.settings).decrypt(account.credential_enc)
        inbox = ImapInbox(
            account.imap_host, account.imap_port, account.email, password, account.imap_security
        )
        ok, detail = inbox.test_connection()
        if not ok:
            return {"ok": False, "error": detail}
        since = utcnow() - timedelta(days=days_back)
        try:
            messages = inbox.fetch_since(since, limit=limit)
        except (imaplib.IMAP4.error, OSError) as exc:  # pragma: no cover - network
            return {"ok": False, "error": f"IMAP fetch failed: {exc}"}
        result = self.ingest(account_id, messages, account_email=account.email)
        result["ok"] = True
        result["fetched"] = len(messages)
        return result

    def sync_all(self, limit: int = 100) -> dict:
        with session_scope() as session:
            ids = session.execute(
                select(EmailAccount.id).where(EmailAccount.is_active.is_(True))
            ).scalars().all()
        summary = {"accounts": len(ids), "results": []}
        for account_id in ids:
            summary["results"].append({"accountId": account_id, **self.sync_account(account_id, limit)})
        return summary

    def ingest(
        self, account_id: int, messages: list[FetchedMessage], account_email: str = ""
    ) -> dict:
        """Pure DB logic — injectable messages make this unit-testable."""
        stats = {
            "processed": len(messages),
            "replies": 0,
            "interested": 0,
            "notInterested": 0,
            "unsubscribes": 0,
            "unmatched": 0,
            "duplicates": 0,
        }
        with session_scope() as session:
            message_ids: dict[str, tuple[int, int]] = {}
            for row in session.execute(
                select(OutboundMessage.id, OutboundMessage.lead_id, OutboundMessage.rfc_message_id)
                .where(OutboundMessage.rfc_message_id != "")
            ).all():
                message_ids[row.rfc_message_id.lower()] = (row.id, row.lead_id)

            subjects: dict[str, tuple[int, int]] = {}
            for row in session.execute(
                select(OutboundMessage.id, OutboundMessage.lead_id, OutboundMessage.subject)
                .where(OutboundMessage.status.in_(["sent", "bounced"]))
            ).all():
                subjects.setdefault(normalise_subject(row.subject), (row.id, row.lead_id))

            lead_emails: dict[str, int] = {}
            for row in session.execute(select(Lead.id, Lead.email)).all():
                if row.email:
                    lead_emails.setdefault(row.email.lower(), row.id)

            for fetched in messages:
                if not fetched.from_email:
                    continue
                if account_email and fetched.from_email == account_email.lower():
                    continue  # our own outbound copy
                existing = session.execute(
                    select(Reply).where(
                        Reply.imap_uid == fetched.uid, Reply.account_id == account_id
                    )
                ).scalar_one_or_none()
                if existing:
                    stats["duplicates"] += 1
                    continue

                match = match_reply(
                    fetched,
                    message_ids=message_ids,
                    subjects=subjects,
                    lead_emails=lead_emails,
                )
                classification = classify_reply(fetched.subject, fetched.body)

                reply = Reply(
                    lead_id=match.lead_id,
                    message_id=match.message_id,
                    account_id=account_id,
                    from_email=fetched.from_email,
                    from_name=fetched.from_name,
                    subject=fetched.subject,
                    snippet=summarise(fetched.body),
                    body=fetched.body[:8000],
                    intent=classification.intent,
                    sentiment=classification.sentiment,
                    matched_by=match.matched_by or "none",
                    imap_uid=fetched.uid,
                    received_at=fetched.date,
                )
                session.add(reply)

                if not match.matched:
                    stats["unmatched"] += 1
                else:
                    stats["replies"] += 1
                    lead = session.get(Lead, match.lead_id) if match.lead_id else None
                    if lead:
                        if lead.status != LeadStatus.UNSUBSCRIBED:
                            lead.status = LeadStatus.REPLIED
                            lead.pipeline_stage = PipelineStage.REPLIED
                        lead.last_error = ""
                        session.add(
                            Activity(
                                lead_id=lead.id,
                                kind="reply",
                                note=reply.snippet,
                                payload={
                                    "intent": classification.intent,
                                    "sentiment": classification.sentiment,
                                    "from": fetched.from_email,
                                    "subject": fetched.subject,
                                    "matchedBy": match.matched_by,
                                },
                            )
                        )
                    if classification.intent == "interested":
                        stats["interested"] += 1
                    elif classification.intent == "not_interested":
                        stats["notInterested"] += 1

                if wants_unsubscribe(fetched.subject, fetched.body):
                    self._add_suppression(session, fetched.from_email, "unsubscribe reply")
                    stats["unsubscribes"] += 1
                    if match.lead_id:
                        lead = session.get(Lead, match.lead_id)
                        if lead:
                            lead.status = LeadStatus.UNSUBSCRIBED
                            lead.is_suppressed = True
            key = f"last_sync:{account_id}"
            stamp = utcnow().isoformat()
            row = session.execute(
                select(SyncState).where(SyncState.key == key)
            ).scalar_one_or_none()
            if row:
                row.value = stamp
            else:
                session.add(SyncState(key=key, value=stamp))
        return stats

    @staticmethod
    def _add_suppression(session, address: str, reason: str) -> None:
        address = (address or "").lower()
        if not address:
            return
        exists = session.execute(
            select(Suppression).where(Suppression.email == address)
        ).scalar_one_or_none()
        if exists:
            return
        session.add(
            Suppression(email=address, domain=address.split("@")[-1], reason=reason)
        )

    # ------------------------------------------------------------- helpers
    def last_sync(self, account_id: int) -> str | None:
        with session_scope() as session:
            row = session.execute(
                select(SyncState).where(SyncState.key == f"last_sync:{account_id}")
            ).scalar_one_or_none()
            return row.value if row else None

    def record_sync(self, account_id: int) -> None:
        with session_scope() as session:
            row = session.execute(
                select(SyncState).where(SyncState.key == f"last_sync:{account_id}")
            ).scalar_one_or_none()
            if row:
                row.value = utcnow().isoformat()
            else:
                session.add(SyncState(key=f"last_sync:{account_id}", value=utcnow().isoformat()))

    def campaign_overview(self) -> dict:
        """Counts used by the CRM dashboard."""
        with session_scope() as session:
            campaigns = session.execute(select(Campaign)).scalars().all()
            out = []
            for campaign in campaigns:
                leads = session.execute(
                    select(Lead).where(Lead.campaign_id == campaign.id)
                ).scalars().all()
                replies = session.execute(
                    select(Reply)
                    .join(Lead, Lead.id == Reply.lead_id)
                    .where(Lead.campaign_id == campaign.id)
                ).scalars().all()
                stages: dict[str, int] = {stage: 0 for stage in PipelineStage.ORDER}
                for lead in leads:
                    stages[lead.pipeline_stage] = stages.get(lead.pipeline_stage, 0) + 1
                sent = sum(1 for lead in leads if lead.status in {LeadStatus.SENT, LeadStatus.REPLIED})
                out.append(
                    {
                        "campaignId": campaign.id,
                        "campaignName": campaign.name,
                        "status": campaign.status,
                        "leads": len(leads),
                        "sent": sent,
                        "replies": len(replies),
                        "interested": sum(1 for r in replies if r.intent == "interested"),
                        "replyRate": round(len(replies) / sent, 4) if sent else 0.0,
                        "stages": stages,
                    }
                )
            return {"campaigns": out}


def start_campaign_replies_flag(campaign_id: int) -> int:
    """Flag every replied lead of a campaign (used after a manual import)."""
    count = 0
    with session_scope() as session:
        leads = session.execute(
            select(Lead).where(Lead.campaign_id == campaign_id, Lead.status == LeadStatus.REPLIED)
        ).scalars().all()
        for lead in leads:
            if lead.pipeline_stage == PipelineStage.NEW:
                lead.pipeline_stage = PipelineStage.REPLIED
                count += 1
    return count


_service: InboxSyncService | None = None


def get_inbox_service() -> InboxSyncService:
    global _service
    if _service is None:
        _service = InboxSyncService()
    return _service
