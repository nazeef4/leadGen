"""Randomised, unordered dispatch scheduling (anti-spam pacing).

Google's sending limits are not the only thing that gets an account flagged — the
*shape* of the traffic matters.  A cron job that fires one email every 60 seconds
is trivially fingerprinted.  This module produces:

* an **unordered** send sequence (Fisher-Yates shuffle, never input order),
* a **randomised gap** before each send drawn from a triangular distribution
  (most gaps near the low end, occasional long ones — like a human at a desk),
* **long breaks** inserted after every N sends (lunch / meeting simulation),
* **quiet-hours awareness** so nothing goes out at 3am,
* **daily / hourly budget enforcement** so the queue never exceeds the cap.

Everything is seedable, which keeps the unit tests deterministic.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DelayConfig:
    min_seconds: int = 45
    max_seconds: int = 240
    long_pause_every: int = 12
    long_pause_min_seconds: int = 600
    long_pause_max_seconds: int = 1500
    daily_cap: int = 400
    hourly_cap: int = 60
    enforce_quiet_hours: bool = False
    quiet_start_hour: int = 20
    quiet_end_hour: int = 8
    seed: int | None = None

    def validate(self) -> list[str]:
        problems = []
        if self.min_seconds < 5:
            problems.append("Minimum delay under 5s looks like burst sending.")
        if self.max_seconds < self.min_seconds:
            problems.append("Maximum delay must be >= minimum delay.")
        if self.long_pause_every < 2:
            problems.append("Long pauses should trigger after at least 2 sends.")
        if self.daily_cap <= 0:
            problems.append("Daily cap must be positive.")
        if self.hourly_cap > self.daily_cap:
            problems.append("Hourly cap cannot exceed the daily cap.")
        return problems


@dataclass
class SendSlot:
    lead_id: int
    delay_seconds: int
    send_at: datetime
    long_pause: bool = False
    budget_limited: bool = False

    def to_dict(self) -> dict:
        return {
            "leadId": self.lead_id,
            "delaySeconds": self.delay_seconds,
            "sendAt": self.send_at.isoformat(),
            "longPause": self.long_pause,
            "budgetLimited": self.budget_limited,
        }


@dataclass
class PlanSummary:
    total: int
    scheduled: int
    deferred: int
    first_send_at: datetime | None
    last_send_at: datetime | None
    span_minutes: float
    mean_gap_seconds: float
    slots: list[SendSlot] = field(default_factory=list)
    deferred_lead_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "scheduled": self.scheduled,
            "deferred": self.deferred,
            "firstSendAt": self.first_send_at.isoformat() if self.first_send_at else None,
            "lastSendAt": self.last_send_at.isoformat() if self.last_send_at else None,
            "spanMinutes": round(self.span_minutes, 1),
            "meanGapSeconds": round(self.mean_gap_seconds, 1),
            "slots": [s.to_dict() for s in self.slots],
            "deferredLeadIds": self.deferred_lead_ids,
        }


def shuffle_unordered(lead_ids: list[int], rng: random.Random | None = None) -> list[int]:
    """Return a shuffled copy — the send order must never mirror the list order."""
    rng = rng or random.Random()
    out = list(lead_ids)
    rng.shuffle(out)
    return out


def next_gap(cfg: DelayConfig, rng: random.Random | None = None) -> int:
    """One human-ish gap: triangular distribution biased towards the lower bound."""
    rng = rng or random.Random()
    low, high = float(cfg.min_seconds), float(cfg.max_seconds)
    if high <= low:
        return int(low)
    mode = low + (high - low) * 0.3
    value = rng.triangular(low, high, mode)
    if rng.random() < 0.12:  # occasional "got distracted" tail
        value = rng.uniform(high * 0.9, high * 1.6)
    return max(cfg.min_seconds, int(round(value)))


def long_pause(cfg: DelayConfig, rng: random.Random | None = None) -> int:
    rng = rng or random.Random()
    return rng.randint(cfg.long_pause_min_seconds, cfg.long_pause_max_seconds)


def next_business_moment(moment: datetime, cfg: DelayConfig) -> datetime:
    """Push a timestamp past quiet hours (local wall clock of the machine)."""
    if not cfg.enforce_quiet_hours:
        return moment
    start, end = cfg.quiet_start_hour % 24, cfg.quiet_end_hour % 24
    local = moment
    for _ in range(8):
        hour = local.hour
        if start <= end:
            blocked = start <= hour < end
        else:  # wraps midnight, e.g. 20:00 -> 08:00
            blocked = hour >= start or hour < end
        if not blocked:
            return local
        # jump to the next quiet-hours end
        nxt = local.replace(hour=end, minute=0, second=0, microsecond=0)
        if nxt <= local:
            nxt = nxt + timedelta(days=1)
        local = nxt
    return moment


class DelayPlanner:
    def __init__(self, cfg: DelayConfig, rng: random.Random | None = None):
        self.cfg = cfg
        self.rng = rng or random.Random(cfg.seed)

    def plan(
        self,
        lead_ids: list[int],
        start: datetime | None = None,
        already_sent_today: int = 0,
        already_sent_this_hour: int = 0,
    ) -> PlanSummary:
        cfg = self.cfg
        cursor = next_business_moment(start or utcnow(), cfg)
        ordered = shuffle_unordered(lead_ids, self.rng)

        remaining_today = max(0, cfg.daily_cap - already_sent_today)
        remaining_hour = max(0, cfg.hourly_cap - already_sent_this_hour)

        slots: list[SendSlot] = []
        deferred: list[int] = []
        hour_start = cursor.replace(minute=0, second=0, microsecond=0)
        sent_this_hour = 0

        for index, lead_id in enumerate(ordered):
            if len(slots) >= remaining_today:
                deferred.append(lead_id)
                continue
            is_long_pause = (
                cfg.long_pause_every > 0
                and index > 0
                and index % cfg.long_pause_every == 0
            )
            gap = long_pause(cfg, self.rng) if is_long_pause else next_gap(cfg, self.rng)
            cursor = cursor + timedelta(seconds=gap)

            # roll the hourly window forward when we cross into a new hour
            while cursor - hour_start >= timedelta(hours=1):
                hour_start += timedelta(hours=1)
                sent_this_hour = 0

            if sent_this_hour >= remaining_hour:
                # wait until the next hour instead of bursting
                cursor = hour_start + timedelta(hours=1)
                hour_start = cursor.replace(minute=0, second=0, microsecond=0)
                sent_this_hour = 0

            cursor = next_business_moment(cursor, cfg)
            slots.append(
                SendSlot(
                    lead_id=lead_id,
                    delay_seconds=gap,
                    send_at=cursor,
                    long_pause=is_long_pause,
                )
            )
            sent_this_hour += 1

        first = slots[0].send_at if slots else None
        last = slots[-1].send_at if slots else None
        span = (last - first).total_seconds() / 60 if first and last else 0.0
        gaps = [s.delay_seconds for s in slots]
        return PlanSummary(
            total=len(lead_ids),
            scheduled=len(slots),
            deferred=len(deferred),
            first_send_at=first,
            last_send_at=last,
            span_minutes=span,
            mean_gap_seconds=(sum(gaps) / len(gaps)) if gaps else 0.0,
            slots=slots,
            deferred_lead_ids=deferred,
        )


def is_within_quiet_hours(moment: datetime, cfg: DelayConfig) -> bool:
    if not cfg.enforce_quiet_hours:
        return False
    start, end = cfg.quiet_start_hour % 24, cfg.quiet_end_hour % 24
    hour = moment.hour
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def seconds_until(hour: int, minute: int = 0, now: datetime | None = None) -> int:
    """Seconds until the next occurrence of a wall-clock time (used for daily rollover)."""
    now = now or utcnow()
    target = now.replace(hour=hour % 24, minute=minute % 60, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return int((target - now).total_seconds())


def daily_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or utcnow()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def hour_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or utcnow()
    start = now.replace(minute=0, second=0, microsecond=0)
    return start, start + timedelta(hours=1)


def describe_time(value: time | None) -> str:
    return value.strftime("%H:%M") if value else ""
