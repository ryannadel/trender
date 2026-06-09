"""Time-frame parsing helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import TimeWindow


def parse_date(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"Expected ISO date like 2026-01-31, got {value!r}") from exc


def make_window(days: int | None, start: str | None, end: str | None) -> TimeWindow:
    if days is not None and (start or end):
        raise ValueError("Use either --days or --from/--to, not both.")

    now = datetime.now(timezone.utc)
    if days is not None:
        if days <= 0:
            raise ValueError("--days must be greater than 0.")
        start_dt = now - timedelta(days=days)
        return TimeWindow(start=start_dt.date().isoformat(), end=now.date().isoformat())

    if start or end:
        if not (start and end):
            raise ValueError("--from and --to must be provided together.")
        start_dt = parse_date(start)
        end_dt = parse_date(end)
        if start_dt > end_dt:
            raise ValueError("--from must be before --to.")
        return TimeWindow(start=start_dt.date().isoformat(), end=end_dt.date().isoformat())

    start_dt = now - timedelta(days=90)
    return TimeWindow(start=start_dt.date().isoformat(), end=now.date().isoformat())


def in_window(date_value: str, window: TimeWindow) -> bool:
    if not date_value:
        return False
    date = date_value[:10]
    return window.start <= date <= window.end

