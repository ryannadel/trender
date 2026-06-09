"""Reusable trend-analysis service used by both hosted agents and local commands."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .analyzer import Analyzer
from .config import Config, load_config
from .crawler import Crawler
from .discoverer import DiscoveryOptions, discover_sources
from .models import ScanRecord, utc_now_iso
from .renderer import render_report
from .storage import Storage
from .timeframes import make_window
from .trends import build_trend_map

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class TrendScanResult:
    topic: str
    window_start: str
    window_end: str
    source_count: int
    source_summary: str
    enriched_source_count: int
    trend_topic_count: int
    top_topics: list[dict]
    scan_path: str
    report_path: str

    def to_dict(self) -> dict:
        return asdict(self)


def summarize_sources(sources) -> str:
    counts = Counter(source.source_type for source in sources)
    if not counts:
        return "0 sources"
    return ", ".join(f"{count} {source_type}" for source_type, count in sorted(counts.items()))


def require_openai_key(config: Config) -> None:
    if not config.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is required. Trender uses GPT-5.4 for analysis and does not provide heuristic fallback."
        )


async def scan_trends(
    topic: str,
    *,
    days: int | None = None,
    start: str | None = None,
    end: str | None = None,
    max_results: int = 30,
    include_arxiv: bool = True,
    include_github: bool = True,
    include_web: bool = True,
    data_dir: Path | None = None,
    progress: ProgressCallback | None = None,
) -> TrendScanResult:
    """Discover sources, extract topics with GPT-5.4, calculate trends, and render a report."""
    config = load_config(data_dir)
    require_openai_key(config)
    storage = Storage(config.data_dir)
    storage.ensure()
    window = make_window(days, start, end)
    options = DiscoveryOptions(
        max_results=max_results,
        include_arxiv=include_arxiv,
        include_github=include_github,
        include_web=include_web,
    )
    emit = progress or (lambda message: None)
    enabled_sources = [
        name
        for name, enabled in (
            ("arXiv", options.include_arxiv),
            ("GitHub", options.include_github),
            ("OpenAI web search", options.include_web),
        )
        if enabled
    ]

    emit(f"Using GPT model {config.openai_model}; no heuristic fallback is enabled.")
    emit(f"Analyzing source dates from {window.start} to {window.end}.")
    emit(f"Discovery sources enabled: {', '.join(enabled_sources)}.")
    sources = await discover_sources(topic, window, config, options)
    emit(f"Discovery complete: {len(sources)} sources ({summarize_sources(sources)}).")

    emit("Fetching source bodies and repository READMEs where available.")
    sources = await Crawler(config).enrich(sources)
    enriched = sum(1 for source in sources if (source.content or "").strip() and source.content != source.summary)
    emit(f"Content enrichment complete: {enriched} sources gained additional text.")

    emit("Sending source batches to GPT-5.4 to extract themes, evidence, novelty, and relevance scores.")
    extracted_topics = Analyzer(config).analyze(topic, sources)
    emit(f"Analysis complete: extracted {len(extracted_topics)} candidate trend topics.")

    scan_record = ScanRecord(
        topic_query=topic,
        generated_at=utc_now_iso(),
        window=window,
        sources=sources,
        extracted_topics=extracted_topics,
    )
    emit("Persisting raw scan data for future trend comparisons.")
    scan_path = storage.save_scan(scan_record)

    emit("Loading historical scans and calculating bucketed trend metrics.")
    all_scans = storage.load_scans(topic)
    trend = build_trend_map(topic, all_scans, window)
    emit(f"Trend map ready: ranked {len(trend.topics)} topics from {len(trend.sources)} in-window sources.")

    emit("Rendering static HTML report with dynamic time-window controls and compare mode.")
    report_path = storage.save_report(topic, trend.generated_at, render_report(trend))

    top_topics = [
        {
            "name": trend_topic.name,
            "direction": trend_topic.direction,
            "momentum": trend_topic.velocity,
            "source_count": trend_topic.source_count,
            "source_diversity": trend_topic.source_diversity,
            "first_seen": trend_topic.first_seen,
            "last_seen": trend_topic.last_seen,
        }
        for trend_topic in trend.topics[:8]
    ]
    return TrendScanResult(
        topic=topic,
        window_start=window.start,
        window_end=window.end,
        source_count=len(sources),
        source_summary=summarize_sources(sources),
        enriched_source_count=enriched,
        trend_topic_count=len(trend.topics),
        top_topics=top_topics,
        scan_path=str(scan_path),
        report_path=str(report_path),
    )


def scan_trends_sync(**kwargs) -> TrendScanResult:
    return asyncio.run(scan_trends(**kwargs))

