"""SMTP transport.

Builds RFC-5322 compliant messages with the headers that bulk-sender filters
expect (Message-ID, List-Unsubscribe, Precedence, Auto-Submitted) and sends them
over STARTTLS/SSL.  Failures are classified as permanent or transient so the
dispatcher can decide between retrying and suppressing.

A :class:`DryRunSender` implements the same interface without touching the
network — used by the preview mode and by the test suite.
"""

from __future__ import annotations

import logging
import smtplib
import ssl as ssl_module
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

log = logging.getLogger("leadgen.sender")

PERMANENT_CODES = tuple(range(500, 600))
TRANSIENT_CODES = tuple(range(400, 500))


@dataclass
class SendResult:
    ok: bool
    status: str = "sent"  # sent | failed | bounced | skipped
    error: str = ""
    permanent: bool = False
    message_id: str = ""
    duration_ms: int = 0
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "status": self.status,
            "error": self.error,
            "permanent": self.permanent,
            "messageId": self.message_id,
            "durationMs": self.duration_ms,
        }


@dataclass
class MessagePayload:
    to_email: str
    to_name: str
    subject: str
    body_text: str
    body_html: str = ""
    from_email: str = ""
    from_name: str = ""
    reply_to: str = ""
    message_id: str = ""
    list_unsubscribe_url: str = ""
    campaign_id: int | None = None
    lead_id: int | None = None
    thread_id: str = ""

    def ensure_message_id(self, domain: str = "leadgen.local") -> str:
        if not self.message_id:
            self.message_id = make_msgid(domain=domain or "leadgen.local")
        return self.message_id


def build_message(
    payload: MessagePayload,
    *,
    list_unsubscribe_url: str = "",
    precedence: str = "bulk",
) -> EmailMessage:
    msg = EmailMessage()
    from_email = payload.from_email
    domain = from_email.split("@")[-1] if "@" in from_email else "leadgen.local"
    message_id = payload.ensure_message_id(domain)

    msg["From"] = formataddr((payload.from_name or "", from_email)) if payload.from_name else from_email
    msg["To"] = (
        formataddr((payload.to_name, payload.to_email)) if payload.to_name else payload.to_email
    )
    if payload.reply_to:
        msg["Reply-To"] = payload.reply_to
    msg["Subject"] = payload.subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = message_id
    msg["MIME-Version"] = "1.0"
    msg["Precedence"] = precedence
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Mailer"] = "LeadGen Studio"
    if payload.campaign_id:
        msg["X-LeadGen-Campaign"] = str(payload.campaign_id)
    if payload.lead_id:
        msg["X-LeadGen-Lead"] = str(payload.lead_id)
    if payload.thread_id:
        msg["X-LeadGen-Thread"] = payload.thread_id

    unsub = list_unsubscribe_url or payload.list_unsubscribe_url
    if unsub:
        msg["List-Unsubscribe"] = f"<{unsub}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    msg.set_content(payload.body_text or "")
    if payload.body_html:
        msg.add_alternative(payload.body_html, subtype="html")
    return msg


class SmtpSender:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        security: str = "starttls",
        timeout: int = 30,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.security = (security or "starttls").lower()
        self.timeout = timeout

    def connect(self) -> smtplib.SMTP:
        context = ssl_module.create_default_context()
        if self.security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                self.host, self.port, timeout=self.timeout, context=context
            )
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            server.ehlo()
            if self.security == "starttls":
                server.starttls(context=context)
                server.ehlo()
        if self.username and self.password:
            server.login(self.username, self.password)
        return server

    def test_connection(self) -> tuple[bool, str]:
        try:
            server = self.connect()
        except smtplib.SMTPAuthenticationError as exc:
            return False, f"Authentication failed: {exc.smtp_error!r}"
        except (smtplib.SMTPException, OSError) as exc:
            return False, f"Could not connect to {self.host}:{self.port} — {exc}"
        try:
            server.noop()
            return True, "SMTP login OK"
        finally:
            try:
                server.quit()
            except Exception:  # pragma: no cover
                pass

    def send(self, payload: MessagePayload) -> SendResult:
        started = time.monotonic()
        msg = build_message(payload)
        try:
            server = self.connect()
        except smtplib.SMTPAuthenticationError as exc:
            return SendResult(
                ok=False, status="failed", permanent=True,
                error=f"SMTP auth failed: {exc.smtp_error!r}",
                message_id=payload.message_id,
                duration_ms=_ms(started),
            )
        except (smtplib.SMTPException, OSError) as exc:
            return SendResult(
                ok=False, status="failed", permanent=False,
                error=f"SMTP connection error: {exc}",
                message_id=payload.message_id, duration_ms=_ms(started),
            )
        try:
            refused = server.send_message(msg)
            if refused:
                detail = "; ".join(f"{addr}: {info}" for addr, info in refused.items())
                permanent = any(
                    isinstance(info, tuple) and info[0] in PERMANENT_CODES for info in refused.values()
                )
                return SendResult(
                    ok=False,
                    status="bounced" if permanent else "failed",
                    permanent=permanent,
                    error=f"Recipient refused: {detail}",
                    message_id=payload.message_id,
                    duration_ms=_ms(started),
                    detail={"refused": str(detail)},
                )
            return SendResult(
                ok=True, status="sent", message_id=payload.message_id, duration_ms=_ms(started)
            )
        except smtplib.SMTPRecipientsRefused as exc:
            first = next(iter(exc.recipients.values()), (550, b"refused"))
            code = first[0] if isinstance(first, tuple) else 550
            return SendResult(
                ok=False,
                status="bounced" if code in PERMANENT_CODES else "failed",
                permanent=code in PERMANENT_CODES,
                error=f"Recipient refused ({code})",
                message_id=payload.message_id,
                duration_ms=_ms(started),
            )
        except smtplib.SMTPSenderRefused as exc:
            return SendResult(
                ok=False, status="failed", permanent=True,
                error=f"Sender refused ({exc.smtp_code}): {exc.smtp_error!r}",
                message_id=payload.message_id, duration_ms=_ms(started),
            )
        except (smtplib.SMTPException, OSError) as exc:
            return SendResult(
                ok=False, status="failed", permanent=False, error=f"SMTP error: {exc}",
                message_id=payload.message_id, duration_ms=_ms(started),
            )
        finally:
            try:
                server.quit()
            except Exception:  # pragma: no cover
                pass


class DryRunSender:
    """Records what would have been sent.  Never touches the network."""

    def __init__(self, fail_every: int = 0):
        self.sent: list[MessagePayload] = []
        self.fail_every = fail_every

    def test_connection(self) -> tuple[bool, str]:
        return True, "Dry run (no network)"

    def send(self, payload: MessagePayload) -> SendResult:
        started = time.monotonic()
        # Render for real so a dry run exercises the same MIME building path and
        # allocates the Message-ID that reply threading depends on.
        build_message(payload)
        self.sent.append(payload)
        if self.fail_every and len(self.sent) % self.fail_every == 0:
            return SendResult(
                ok=False, status="failed", permanent=False,
                error="simulated transient failure", message_id=payload.message_id,
                duration_ms=_ms(started),
            )
        return SendResult(ok=True, status="sent", message_id=payload.message_id, duration_ms=_ms(started))


PROVIDER_PRESETS = {
    "gmail": {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "imap_security": "ssl",
        "daily_limit": 400,
        "hourly_limit": 60,
        "note": "Use an App Password (2FA must be on). Consumer cap is 500/day.",
    },
    "google_workspace": {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "imap_security": "ssl",
        "daily_limit": 1500,
        "hourly_limit": 120,
        "note": "Workspace trial accounts are capped at 500/day; paid at 2000/day.",
    },
    "outlook": {
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "imap_security": "ssl",
        "daily_limit": 300,
        "hourly_limit": 60,
        "note": "Microsoft 365: 30 recipients/message, 10000 recipients/day.",
    },
    "zoho": {
        "smtp_host": "smtp.zoho.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "imap_host": "imap.zoho.com",
        "imap_port": 993,
        "imap_security": "ssl",
        "daily_limit": 300,
        "hourly_limit": 60,
        "note": "Zoho Mail free tier: 250/day.",
    },
    "fastmail": {
        "smtp_host": "smtp.fastmail.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "imap_host": "imap.fastmail.com",
        "imap_port": 993,
        "imap_security": "ssl",
        "daily_limit": 400,
        "hourly_limit": 80,
        "note": "Use an app-specific password.",
    },
    "custom": {
        "smtp_host": "",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "imap_host": "",
        "imap_port": 993,
        "imap_security": "ssl",
        "daily_limit": 400,
        "hourly_limit": 60,
        "note": "Enter your own SMTP/IMAP endpoints.",
    },
}


def guess_provider(email: str) -> str:
    domain = (email or "").split("@")[-1].lower()
    if domain in {"gmail.com", "googlemail.com"}:
        return "gmail"
    if domain in {"outlook.com", "hotmail.com", "live.com", "msn.com"}:
        return "outlook"
    if domain.endswith("zoho.com"):
        return "zoho"
    if domain.endswith("fastmail.com"):
        return "fastmail"
    return "custom"


def new_thread_id() -> str:
    return uuid.uuid4().hex[:16]


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
