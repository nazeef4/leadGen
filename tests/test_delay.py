"""The pacing engine: unordered order, random gaps, caps and quiet hours."""

from __future__ import annotations

import random
from datetime import datetime, timezone

from leadgen.services.delay import (
    DelayConfig,
    DelayPlanner,
    is_within_quiet_hours,
    next_business_moment,
    next_gap,
    shuffle_unordered,
)


def test_shuffle_breaks_input_order():
    ids = list(range(1, 51))
    out = shuffle_unordered(ids, random.Random(3))
    assert sorted(out) == ids
    assert out != ids


def test_gaps_stay_inside_the_window_and_vary():
    cfg = DelayConfig(min_seconds=45, max_seconds=240, seed=11)
    planner = DelayPlanner(cfg)
    plan = planner.plan(list(range(1, 41)))
    gaps = [s.delay_seconds for s in plan.slots]
    assert len(gaps) == 40
    assert all(g >= 45 for g in gaps)
    assert len(set(gaps)) > 15, "delays should look random, not fixed"
    assert plan.mean_gap_seconds > 45


def test_send_order_is_not_input_order():
    cfg = DelayConfig(min_seconds=30, max_seconds=120, seed=5)
    ids = list(range(1, 31))
    plan = DelayPlanner(cfg).plan(ids)
    scheduled = [s.lead_id for s in plan.slots]
    assert scheduled != ids
    assert sorted(scheduled) == ids


def test_timestamps_are_monotonic():
    cfg = DelayConfig(min_seconds=30, max_seconds=90, seed=2)
    plan = DelayPlanner(cfg).plan(list(range(1, 25)))
    times = [s.send_at for s in plan.slots]
    assert times == sorted(times)
    assert plan.span_minutes > 0


def test_long_pauses_are_inserted():
    cfg = DelayConfig(
        min_seconds=30, max_seconds=60, long_pause_every=5,
        long_pause_min_seconds=600, long_pause_max_seconds=900, seed=9,
    )
    plan = DelayPlanner(cfg).plan(list(range(1, 21)))
    pauses = [s for s in plan.slots if s.long_pause]
    assert pauses
    assert all(600 <= p.delay_seconds <= 900 for p in pauses)


def test_daily_cap_defers_the_rest():
    cfg = DelayConfig(min_seconds=10, max_seconds=20, daily_cap=5, hourly_cap=5, seed=1)
    plan = DelayPlanner(cfg).plan(list(range(1, 21)), already_sent_today=0)
    assert plan.scheduled == 5
    assert plan.deferred == 15
    # the order is shuffled, so *which* ids get deferred is arbitrary — but the
    # two sets must partition the input exactly.
    scheduled = [s.lead_id for s in plan.slots]
    assert sorted(scheduled + plan.deferred_lead_ids) == list(range(1, 21))
    assert not set(scheduled) & set(plan.deferred_lead_ids)


def test_already_sent_today_counts_against_the_cap():
    cfg = DelayConfig(min_seconds=10, max_seconds=20, daily_cap=10, hourly_cap=10, seed=1)
    plan = DelayPlanner(cfg).plan(list(range(1, 11)), already_sent_today=8)
    assert plan.scheduled == 2
    assert plan.deferred == 8


def test_hourly_cap_pushes_into_the_next_hour():
    cfg = DelayConfig(min_seconds=10, max_seconds=20, daily_cap=100, hourly_cap=3, seed=4)
    start = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
    plan = DelayPlanner(cfg).plan(list(range(1, 8)), start=start)
    assert plan.scheduled == 7
    hours = {s.send_at.replace(minute=0, second=0, microsecond=0) for s in plan.slots}
    assert len(hours) >= 3, "seven sends at 3/hour must span at least three hours"


def test_quiet_hours_are_skipped():
    cfg = DelayConfig(
        min_seconds=60, max_seconds=120, enforce_quiet_hours=True,
        quiet_start_hour=20, quiet_end_hour=8, seed=6,
    )
    start = datetime(2026, 3, 10, 19, 30, tzinfo=timezone.utc)
    plan = DelayPlanner(cfg).plan(list(range(1, 12)), start=start)
    for slot in plan.slots:
        assert not is_within_quiet_hours(slot.send_at, cfg), slot.send_at


def test_is_within_quiet_hours_wraps_midnight():
    cfg = DelayConfig(enforce_quiet_hours=True, quiet_start_hour=20, quiet_end_hour=8)
    assert is_within_quiet_hours(datetime(2026, 1, 1, 23, 0, tzinfo=timezone.utc), cfg)
    assert is_within_quiet_hours(datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc), cfg)
    assert not is_within_quiet_hours(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc), cfg)


def test_next_business_moment_moves_out_of_quiet_hours():
    cfg = DelayConfig(enforce_quiet_hours=True, quiet_start_hour=22, quiet_end_hour=7)
    moved = next_business_moment(datetime(2026, 1, 1, 23, 30, tzinfo=timezone.utc), cfg)
    assert moved.hour == 7


def test_config_validation_catches_bad_values():
    assert DelayConfig(min_seconds=1, max_seconds=2).validate()
    assert DelayConfig(min_seconds=60, max_seconds=30).validate()
    assert DelayConfig(min_seconds=60, max_seconds=120, hourly_cap=500, daily_cap=100).validate()
    assert DelayConfig(min_seconds=60, max_seconds=120).validate() == []


def test_next_gap_never_below_minimum():
    cfg = DelayConfig(min_seconds=120, max_seconds=125)
    for _ in range(50):
        assert next_gap(cfg, random.Random()) >= 120


def test_two_runs_differ_without_a_seed():
    a = DelayPlanner(DelayConfig(min_seconds=30, max_seconds=300)).plan(list(range(1, 20)))
    b = DelayPlanner(DelayConfig(min_seconds=30, max_seconds=300)).plan(list(range(1, 20)))
    assert [s.delay_seconds for s in a.slots] != [s.delay_seconds for s in b.slots]


def test_seeded_runs_are_reproducible():
    a = DelayPlanner(DelayConfig(min_seconds=30, max_seconds=300, seed=42)).plan(list(range(1, 20)))
    b = DelayPlanner(DelayConfig(min_seconds=30, max_seconds=300, seed=42)).plan(list(range(1, 20)))
    assert [s.delay_seconds for s in a.slots] == [s.delay_seconds for s in b.slots]
    assert [s.lead_id for s in a.slots] == [s.lead_id for s in b.slots]


def test_empty_queue_is_safe():
    plan = DelayPlanner(DelayConfig()).plan([])
    assert plan.scheduled == 0
    assert plan.slots == []
    assert plan.mean_gap_seconds == 0.0
