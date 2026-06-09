"""Trend computation over one or more scans."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from .models import ScanRecord, Source, TimeWindow, TrendMap, TrendPoint, TrendTopic, utc_now_iso
from .timeframes import in_window


@dataclass(frozen=True)
class Bucket:
    label: str
    start: date
    end: date


def build_trend_map(topic_query: str, scans: list[ScanRecord], window: TimeWindow) -> TrendMap:
    relevant_scans = [scan for scan in scans if scan.window.end >= window.start and scan.window.start <= window.end]
    buckets = make_buckets(window)
    source_by_id = collect_sources(relevant_scans, window)
    topic_groups = collect_topic_groups(relevant_scans, source_by_id)

    trend_topics: list[TrendTopic] = []
    for normalized_name, entries in topic_groups.items():
        all_source_ids = sorted({source_id for _, ids in entries for source_id in ids})
        dates = sorted(source_by_id[source_id].published_at[:10] for source_id in all_source_ids if source_id in source_by_id)
        if not dates:
            continue

        source_types = {source_by_id[source_id].source_type for source_id in all_source_ids}
        bucket_counts = count_sources_by_bucket(all_source_ids, source_by_id, buckets)
        time_series = [TrendPoint(date=bucket.label, count=bucket_counts[bucket.label]) for bucket in buckets]
        early_rate, recent_rate, velocity = compute_momentum(time_series)
        first_seen = dates[0]
        direction = classify_direction(first_seen, window, velocity, early_rate, recent_rate)
        representative = max(entries, key=lambda entry: entry[0].relevance_score)[0]
        key_findings = unique_findings(entries)

        trend_topics.append(
            TrendTopic(
                name=representative.name or normalized_name.title(),
                description=representative.description,
                source_count=len(all_source_ids),
                source_diversity=len(source_types),
                relevance_score=avg([entry[0].relevance_score for entry in entries]),
                novelty_score=avg([entry[0].novelty_score for entry in entries]),
                velocity=round(velocity, 3),
                direction=direction,
                first_seen=first_seen,
                last_seen=dates[-1],
                time_series=time_series,
                source_ids=all_source_ids,
                key_findings=key_findings[:6],
            )
        )

    trend_topics.sort(key=trend_rank, reverse=True)
    return TrendMap(
        topic_query=topic_query,
        generated_at=utc_now_iso(),
        window=window,
        topics=trend_topics,
        sources=list(source_by_id.values()),
        scans=relevant_scans,
    )


def collect_sources(scans: list[ScanRecord], window: TimeWindow) -> dict[str, Source]:
    source_by_id: dict[str, Source] = {}
    for scan in scans:
        for source in scan.sources:
            if in_window(source.published_at, window):
                source_by_id[source.id] = source
    return source_by_id


def collect_topic_groups(scans: list[ScanRecord], source_by_id: dict[str, Source]) -> dict[str, list]:
    topic_groups: dict[str, list] = defaultdict(list)
    for scan in scans:
        for topic in scan.extracted_topics:
            valid_source_ids = [source_id for source_id in topic.source_ids if source_id in source_by_id]
            if valid_source_ids:
                topic_groups[normalize_topic(topic.name)].append((topic, valid_source_ids))
    return topic_groups


def make_buckets(window: TimeWindow) -> list[Bucket]:
    start = date.fromisoformat(window.start[:10])
    end = date.fromisoformat(window.end[:10])
    bucket_days = bucket_size_days(start, end)
    buckets: list[Bucket] = []
    current = start
    while current <= end:
        bucket_end = min(current + timedelta(days=bucket_days - 1), end)
        label = current.isoformat() if bucket_days == 1 else f"{current.isoformat()}..{bucket_end.isoformat()}"
        buckets.append(Bucket(label=label, start=current, end=bucket_end))
        current = bucket_end + timedelta(days=1)
    return buckets


def bucket_size_days(start: date, end: date) -> int:
    span_days = max(1, (end - start).days + 1)
    if span_days <= 14:
        return 1
    if span_days <= 45:
        return 3
    if span_days <= 180:
        return 7
    if span_days <= 540:
        return 14
    return 30


def count_sources_by_bucket(
    source_ids: list[str],
    source_by_id: dict[str, Source],
    buckets: list[Bucket],
) -> dict[str, int]:
    counts = {bucket.label: 0 for bucket in buckets}
    for source_id in source_ids:
        source_date = date.fromisoformat(source_by_id[source_id].published_at[:10])
        for bucket in buckets:
            if bucket.start <= source_date <= bucket.end:
                counts[bucket.label] += 1
                break
    return counts


def compute_momentum(time_series: list[TrendPoint]) -> tuple[float, float, float]:
    if not time_series:
        return 0.0, 0.0, 0.0
    midpoint = max(1, len(time_series) // 2)
    early_points = time_series[:midpoint]
    recent_points = time_series[midpoint:] or time_series[-1:]
    early_rate = sum(point.count for point in early_points) / len(early_points)
    recent_rate = sum(point.count for point in recent_points) / len(recent_points)
    baseline = max(early_rate, 0.5)
    velocity = (recent_rate - early_rate) / baseline
    return early_rate, recent_rate, velocity


def classify_direction(
    first_seen: str,
    window: TimeWindow,
    velocity: float,
    early_rate: float,
    recent_rate: float,
) -> str:
    window_days = max(1, date_to_ordinal(window.end) - date_to_ordinal(window.start) + 1)
    age_days = date_to_ordinal(window.end) - date_to_ordinal(first_seen) + 1
    if recent_rate > 0 and early_rate == 0 and age_days <= max(7, window_days // 4):
        return "emerging"
    if velocity >= 0.35:
        return "rising"
    if velocity <= -0.35:
        return "fading"
    return "stable"


def trend_rank(topic: TrendTopic) -> tuple:
    direction_score = {"emerging": 3, "rising": 2, "stable": 1, "fading": 0}.get(topic.direction, 0)
    confidence = topic.source_count * topic.source_diversity * max(topic.relevance_score, 0.01)
    return (
        direction_score,
        topic.velocity,
        confidence,
        topic.novelty_score,
    )


def unique_findings(entries: list) -> list[str]:
    key_findings: list[str] = []
    for topic, _ in entries:
        for finding in topic.key_findings:
            if finding not in key_findings:
                key_findings.append(finding)
    return key_findings


def normalize_topic(name: str) -> str:
    return " ".join(name.lower().strip().split())


def avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def date_to_ordinal(value: str) -> int:
    return date.fromisoformat(value[:10]).toordinal()

