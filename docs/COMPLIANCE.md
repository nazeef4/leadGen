# Compliance & responsible use

Cold outreach is a legitimate B2B channel **and** one of the most regulated ones.
This document describes what the software enforces and what remains your
responsibility. It is not legal advice.

## What the software enforces

### Content (blocking — the message will not send)

| Rule | Why |
| --- | --- |
| A working opt-out must be present | CAN-SPAM §5(a)(4), CASL, PECR |
| A valid physical postal address must be present | CAN-SPAM §5(a)(3) |
| 4+ spam-trigger phrases in subject/body | Deliverability and anti-spam filters |
| Empty subject | Looks like bulk mail |
| Recipient on the suppression list | Honouring opt-outs |

### Content (warning — lowers the compliance score)

Long subjects (>70 chars), bodies under 40 words or over 220, ALL-CAPS words,
multiple exclamation marks, more than three links, image-heavy HTML, and
attachment-like references.

### Sending behaviour (blocking)

| Guardrail | Default | Why |
| --- | --- | --- |
| Daily recipient cap | 400 | Google free accounts hard-stop at 500/day; Workspace trials also 500 |
| Hourly recipient cap | 60 | Avoids burst patterns |
| Minimum gap since last send | 45s | A fixed cron cadence is trivially fingerprinted |
| Quiet hours | off by default | Sending at 3am hurts reply rates and looks automated |
| Suppression list | always on | Opt-outs are permanent and global |

### Sending behaviour (protective)

* Randomised, **unordered** send sequence with a triangular gap distribution and
  a 10–25 minute break every 12 sends.
* Circuit breaker after 5 consecutive failures — a broken credential or a
  blacklisted domain stops the run instead of burning the domain's reputation.
* Per-domain concentration warning when several recipients share a domain.
* Headers expected of a legitimate bulk sender: `Message-ID`,
  `List-Unsubscribe` with one-click POST, `Precedence: bulk`, `Auto-Submitted`.

## What remains your responsibility

**Only email businesses, and only addresses they published for business
contact.** Consumer addresses are covered by much stricter rules (GDPR, PECR,
CASL) that generally require prior consent.

**GDPR (EU/UK).** Processing personal data of EU/UK contacts needs a lawful
basis. "Legitimate interests" can apply to B2B prospecting but requires a
documented balancing test, a privacy notice, and honouring erasure and objection
requests immediately. The suppression list is the technical mechanism for
objection; keep records of where each address came from (the `source` and
`source_url` fields exist for this).

**CASL (Canada).** Requires express or implied consent. Implied consent from a
published address only applies where the message is relevant to the recipient's
business role, and it expires.

**CAN-SPAM (US).** Permits unsolicited B2B email but requires accurate headers,
a non-deceptive subject, identification as an advertisement where applicable, a
valid postal address, and a functioning opt-out honoured within 10 business days
(this app honours it instantly).

**Sender reputation.** Authenticate your domain with SPF, DKIM and DMARC before
sending. Warm up a new domain gradually — the per-campaign `max_per_day` and the
global caps are the tools for that. Never send from a domain you also use for
normal business mail without warming it up first.

**Scraping.** This app honours `robots.txt`, sends a real User-Agent, and delays
between requests. That does not override a site's terms of service, and
republishing or reselling scraped data may. Keep volumes modest.

**Honesty.** Do not disguise the sender, fake a relationship you do not have
("following up on our conversation"), or claim a referral that does not exist.
Beyond the deliverability cost, several of these are independently illegal.

## If someone opts out

The system handles it automatically: an inbound message matching an unsubscribe
phrase adds the address to `suppressions`, marks the lead unsubscribed, and every
future send path blocks it. Do not remove entries from that list.

## A note on volume

The temptation is always to raise the caps. The caps are not there to slow you
down arbitrarily: a free Google account that exceeds 500 recipients/day gets
sending disabled, and a domain that spikes from 0 to 400 cold emails a day gets
blacklisted by the receiving providers. Slow and steady is not just compliance —
it is the only version of this that keeps working.
