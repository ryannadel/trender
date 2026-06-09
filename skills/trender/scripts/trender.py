#!/usr/bin/env python3
"""Trender skill engine.

This script layers trend analysis on top of the last30days research engine.
It intentionally does not narrow the source set. Retrieval is delegated to
last30days, and Trender analyzes the returned evidence across flexible windows.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any

VERSION = "0.1.3"


@dataclass(frozen=True)
class Window:
    label: str
    start: date
    end: date


@dataclass
class EvidenceItem:
    id: str
    source: str
    title: str
    body: str
    url: str
    published_at: date | None
    score: float
    raw: dict[str, Any]


@dataclass
class TrendTheme:
    title: str
    direction: str
    momentum: float
    current_count: int
    baseline_count: int
    source_diversity: int
    score: float
    windows: dict[str, int]
    sources: list[str]
    evidence: list[EvidenceItem]


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Map topic trends using last30days evidence.")
    parser.add_argument("topic", nargs="+", help="Topic to analyze")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days. Default: 30.")
    parser.add_argument("--from", dest="start", help="Explicit start date YYYY-MM-DD.")
    parser.add_argument("--to", dest="end", help="Explicit end date YYYY-MM-DD.")
    parser.add_argument(
        "--compare",
        help="Compare two lookback windows, e.g. 7,30 or 30,90. Uses current N days vs current M days.",
    )
    parser.add_argument("--emit", choices=["md", "json", "html", "all"], default="md")
    parser.add_argument("--save-dir", default=os.getenv("TRENDER_OUTPUT_DIR", str(Path.home() / "Documents" / "Trender")))
    parser.add_argument("--last30days-dir", default=os.getenv("LAST30DAYS_SKILL_DIR"))
    parser.add_argument("--search", help="Comma-separated source list passed through to last30days.")
    parser.add_argument("--quick", action="store_true", help="Pass --quick through to last30days.")
    parser.add_argument("--deep", action="store_true", help="Pass --deep through to last30days.")
    parser.add_argument("--mock", action="store_true", help="Use last30days mock retrieval fixtures.")
    parser.add_argument("--keep-raw", action="store_true", help="Save raw last30days JSON beside Trender outputs.")
    args = parser.parse_args()

    topic = " ".join(args.topic).strip()
    if not topic:
        raise SystemExit("topic is required")

    analysis_end = parse_date(args.end) if args.end else date.today()
    if args.start:
        analysis_start = parse_date(args.start)
        if analysis_start > analysis_end:
            raise SystemExit("--from must be before --to")
        lookback_days = max(1, (date.today() - analysis_start).days + 1)
    else:
        lookback_days = max(1, args.days)
        analysis_start = analysis_end - timedelta(days=lookback_days - 1)

    compare_windows = parse_compare(args.compare, analysis_end)
    primary_window = Window("selected", analysis_start, analysis_end)
    required_lookback = max(
        [lookback_days]
        + [max(1, (date.today() - window.start).days + 1) for window in compare_windows]
    )

    skill_dir = Path(__file__).resolve().parents[1]
    last30days_dir = resolve_last30days_dir(args.last30days_dir, skill_dir)
    raw_report = run_last30days(
        last30days_dir=last30days_dir,
        topic=topic,
        days=required_lookback,
        search=args.search,
        quick=args.quick,
        deep=args.deep,
        mock=args.mock,
    )
    evidence = flatten_evidence(raw_report)
    clusters = raw_report.get("clusters", [])
    themes = analyze_trends(
        clusters=clusters,
        evidence=evidence,
        topic=topic,
        primary_window=primary_window,
        compare_windows=compare_windows,
    )
    payload = {
        "topic": topic,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": serialize_window(primary_window),
        "compare_windows": [serialize_window(window) for window in compare_windows],
        "retrieval_days": required_lookback,
        "source_counts": dict(Counter(item.source for item in evidence)),
        "coverage_notes": coverage_notes(evidence),
        "themes": [serialize_theme(theme) for theme in themes],
        "source_count": len(evidence),
        "last30days_dir": str(last30days_dir),
    }

    save_dir = Path(args.save_dir).expanduser()
    save_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(topic)
    paths: dict[str, str] = {}
    if args.keep_raw:
        raw_path = save_dir / f"{slug}-last30days-raw.json"
        raw_path.write_text(json.dumps(raw_report, indent=2, sort_keys=True), encoding="utf-8")
        paths["raw"] = str(raw_path)

    if args.emit == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif args.emit == "html":
        html_path = save_dir / f"{slug}-trend-map.html"
        html_path.write_text(render_html(payload), encoding="utf-8")
        print(render_markdown(payload, html_path=html_path))
    else:
        print(render_markdown(payload))

    if args.emit == "all":
        json_path = save_dir / f"{slug}-trend-map.json"
        html_path = save_dir / f"{slug}-trend-map.html"
        json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        html_path.write_text(render_html(payload), encoding="utf-8")
        print(render_markdown(payload, html_path=html_path, json_path=json_path))

    return 0


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def resolve_last30days_dir(configured: str | None, skill_dir: Path) -> Path:
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend(
        [
            skill_dir / "vendor" / "last30days",
            skill_dir.parent / "last30days",
            Path.home() / ".claude" / "skills" / "last30days",
            Path.home() / ".codex" / "skills" / "last30days",
            Path.home() / ".agents" / "skills" / "last30days",
            Path.home() / ".openclaw" / "skills" / "last30days",
        ]
    )
    for candidate in candidates:
        if (candidate / "scripts" / "last30days.py").exists():
            return candidate
    checked = "\n".join(f"  - {path}" for path in candidates)
    raise SystemExit(
        "Could not find last30days skill engine. The packaged vendor copy may be missing; "
        "rebuild/reinstall Trender or set LAST30DAYS_SKILL_DIR.\n"
        f"Checked:\n{checked}"
    )


def run_last30days(
    *,
    last30days_dir: Path,
    topic: str,
    days: int,
    search: str | None,
    quick: bool,
    deep: bool,
    mock: bool,
) -> dict[str, Any]:
    plan_path = write_trender_plan(topic)
    cmd = [
        resolve_python_for_last30days(),
        str(last30days_dir / "scripts" / "last30days.py"),
        topic,
        "--emit=json",
        f"--days={days}",
        "--plan",
        str(plan_path),
    ]
    if search:
        cmd.extend(["--search", search])
    if quick:
        cmd.append("--quick")
    if deep:
        cmd.append("--deep")
    if mock:
        cmd.append("--mock")

    env = os.environ.copy()
    env.setdefault("LAST30DAYS_SKIP_PREFLIGHT", "1")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(last30days_dir),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0:
            raise SystemExit(
                "last30days retrieval failed.\n"
                f"Command: {' '.join(cmd)}\n"
                f"STDERR:\n{proc.stderr}\nSTDOUT:\n{proc.stdout[-4000:]}"
            )
        return parse_json_from_mixed_output(proc.stdout)
    finally:
        try:
            plan_path.unlink()
        except OSError:
            pass


def write_trender_plan(topic: str) -> Path:
    plan = build_trender_plan(topic)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix="-trender-plan.json", delete=False)
    with handle:
        json.dump(plan, handle, indent=2)
    return Path(handle.name)


def build_trender_plan(topic: str) -> dict[str, Any]:
    core = topic.strip()
    quoted = core
    sources = [
        "reddit",
        "x",
        "youtube",
        "tiktok",
        "instagram",
        "hackernews",
        "bluesky",
        "truthsocial",
        "grounding",
        "github",
        "perplexity",
        "threads",
        "pinterest",
        "xquik",
        "digg",
        "polymarket",
        "xiaohongshu",
    ]
    source_weights = {source: 1.0 for source in sources}
    source_weights.update(
        {
            "hackernews": 1.35,
            "github": 1.3,
            "reddit": 1.25,
            "x": 1.2,
            "grounding": 1.2,
            "youtube": 1.1,
            "digg": 1.1,
        }
    )
    subqueries = [
        {
            "label": "primary",
            "search_query": quoted,
            "ranking_query": f"What recent evidence shows {core} changing, accelerating, or becoming important?",
            "sources": sources,
            "weight": 1.2,
        },
        {
            "label": "implementations",
            "search_query": f"{core} implementation open source github release",
            "ranking_query": f"Which concrete projects, repos, releases, or implementations show momentum around {core}?",
            "sources": sources,
            "weight": 1.0,
        },
        {
            "label": "research-and-claims",
            "search_query": f"{core} research paper benchmark evaluation case study",
            "ranking_query": f"What papers, benchmarks, evaluations, or case studies provide evidence for {core}?",
            "sources": sources,
            "weight": 1.0,
        },
        {
            "label": "community-friction",
            "search_query": f"{core} problems limitations adoption workflows examples",
            "ranking_query": f"What are practitioners saying about use cases, limitations, adoption, and workflow friction for {core}?",
            "sources": sources,
            "weight": 0.95,
        },
        {
            "label": "adjacent-phrasing",
            "search_query": f"{core} self improving autonomous agents learning from feedback memory reflection",
            "ranking_query": f"What adjacent terminology or related phrases point to the same trend as {core}?",
            "sources": sources,
            "weight": 0.85,
        },
    ]
    return {
        "intent": "concept",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "story",
        "source_weights": source_weights,
        "subqueries": subqueries,
        "notes": [
            "Generated by Trender to improve recall for trend analysis.",
            "Prefer concrete time-stamped evidence, implementations, practitioner discussion, and cross-source corroboration.",
        ],
    }


def resolve_python_for_last30days() -> str:
    configured = os.getenv("TRENDER_LAST30DAYS_PYTHON")
    candidates = [configured] if configured else []
    candidates.extend(
        [
            sys.executable,
            shutil.which("python3.13"),
            shutil.which("python3.12"),
            shutil.which("python3"),
            shutil.which("python"),
        ]
    )
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if python_version_at_least(candidate, 3, 12):
            return candidate
    raise SystemExit(
        "last30days requires Python 3.12+. Set TRENDER_LAST30DAYS_PYTHON to a Python 3.12+ executable."
    )


def python_version_at_least(executable: str, major: int, minor: int) -> bool:
    try:
        proc = subprocess.run(
            [
                executable,
                "-c",
                "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return False
    if proc.returncode != 0:
        return False
    try:
        found_major, found_minor = (int(part) for part in proc.stdout.strip().split(".", 1))
    except ValueError:
        return False
    return (found_major, found_minor) >= (major, minor)


def parse_json_from_mixed_output(output: str) -> dict[str, Any]:
    start = output.find("{")
    end = output.rfind("}")
    if start < 0 or end < start:
        raise SystemExit("last30days did not return JSON output")
    return json.loads(output[start : end + 1])


def flatten_evidence(report: dict[str, Any]) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for source, source_items in report.get("items_by_source", {}).items():
        if not isinstance(source_items, list):
            continue
        for raw in source_items:
            if not isinstance(raw, dict):
                continue
            url = first_text(raw, ["url", "link", "permalink", "x_url", "html_url"]) or raw.get("item_id") or ""
            title = first_text(raw, ["title", "headline", "name"]) or str(url or source)
            body = first_text(raw, ["body", "text", "summary", "description", "snippet"]) or ""
            published = parse_item_date(raw)
            score = evidence_score(raw)
            item_id = str(url or raw.get("item_id") or f"{source}:{len(items)}")
            items.append(
                EvidenceItem(
                    id=item_id,
                    source=source,
                    title=str(title),
                    body=str(body),
                    url=str(url),
                    published_at=published,
                    score=score,
                    raw=raw,
                )
            )
    return items


def analyze_trends(
    *,
    clusters: list[dict[str, Any]],
    evidence: list[EvidenceItem],
    topic: str,
    primary_window: Window,
    compare_windows: list[Window],
) -> list[TrendTheme]:
    evidence_by_id = {item.id: item for item in evidence}
    all_windows = compare_windows or split_window(primary_window)

    themes: list[TrendTheme] = []
    used_ids: set[str] = set()
    for cluster in clusters:
        ids = [str(value) for value in cluster.get("candidate_ids", []) + cluster.get("representative_ids", [])]
        cluster_items = []
        for candidate_id in ids:
            match = evidence_by_id.get(candidate_id) or fuzzy_find_item(candidate_id, evidence)
            if match and match.id not in {item.id for item in cluster_items}:
                cluster_items.append(match)
                used_ids.add(match.id)
        if not cluster_items:
            continue
        themes.append(
            theme_from_items(
                str(cluster.get("title") or "Untitled theme"),
                cluster_items,
                all_windows,
                topic=topic,
                compare_mode=bool(compare_windows),
            )
        )

    if not themes:
        grouped: dict[str, list[EvidenceItem]] = defaultdict(list)
        for item in evidence:
            grouped[item.source].append(item)
        for source, items in grouped.items():
            themes.append(
                theme_from_items(
                    f"{source} signal",
                    items,
                    all_windows,
                    topic=topic,
                    compare_mode=bool(compare_windows),
                )
            )

    themes = [theme for theme in themes if theme.score > 0]
    themes.sort(key=lambda theme: (direction_rank(theme.direction), theme.momentum, theme.score), reverse=True)
    return themes[:12]


def theme_from_items(
    title: str,
    items: list[EvidenceItem],
    windows: list[Window],
    *,
    topic: str,
    compare_mode: bool,
) -> TrendTheme:
    relevance = theme_relevance(topic, title, items)
    if relevance < 0.34:
        return TrendTheme(
            title=title,
            direction="filtered",
            momentum=0.0,
            current_count=0,
            baseline_count=0,
            source_diversity=0,
            score=0.0,
            windows={window.label: 0 for window in windows},
            sources=[],
            evidence=[],
        )
    counts = {window.label: count_items(items, window) for window in windows}
    baseline, current, baseline_rate, current_rate, current_window = compute_window_stats(counts, windows, compare_mode)
    momentum = compute_momentum(baseline_rate, current_rate)
    direction = classify_direction(baseline, current, momentum, items, current_window)
    sources = sorted({item.source for item in items})
    score = (sum(item.score for item in items) * relevance) + (len(sources) * 5) + (momentum * 10)
    return TrendTheme(
        title=title,
        direction=direction,
        momentum=round(momentum, 3),
        current_count=current,
        baseline_count=baseline,
        source_diversity=len(sources),
        score=round(score, 3),
        windows=counts,
        sources=sources,
        evidence=sorted(items, key=lambda item: item.score, reverse=True)[:5],
    )


def compute_window_stats(
    counts: dict[str, int],
    windows: list[Window],
    compare_mode: bool,
) -> tuple[int, int, float, float, Window | None]:
    if not windows:
        return 0, 0, 0.0, 0.0, None

    if compare_mode:
        baseline_window = windows[0]
        current_window = windows[-1]
        baseline = counts[baseline_window.label]
        current = counts[current_window.label]
        return (
            baseline,
            current,
            baseline / window_days(baseline_window),
            current / window_days(current_window),
            current_window,
        )

    if len(windows) == 1:
        current = counts[windows[0].label]
        return 0, current, 0.0, current / window_days(windows[0]), windows[0]

    midpoint = max(1, len(windows) // 2)
    baseline_windows = windows[:midpoint]
    current_windows = windows[midpoint:]
    baseline = sum(counts[window.label] for window in baseline_windows)
    current = sum(counts[window.label] for window in current_windows)
    baseline_days = sum(window_days(window) for window in baseline_windows)
    current_days = sum(window_days(window) for window in current_windows)
    current_window = Window("recent half", current_windows[0].start, current_windows[-1].end)
    return (
        baseline,
        current,
        baseline / max(1, baseline_days),
        current / max(1, current_days),
        current_window,
    )


def split_window(window: Window) -> list[Window]:
    days = max(1, (window.end - window.start).days + 1)
    bucket_days = 1 if days <= 14 else 3 if days <= 45 else 7 if days <= 180 else 30
    buckets = []
    current = window.start
    while current <= window.end:
        bucket_end = min(window.end, current + timedelta(days=bucket_days - 1))
        label = current.isoformat() if bucket_days == 1 else f"{current.isoformat()}..{bucket_end.isoformat()}"
        buckets.append(Window(label, current, bucket_end))
        current = bucket_end + timedelta(days=1)
    return buckets


def parse_compare(raw: str | None, end: date) -> list[Window]:
    if not raw:
        return []
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise SystemExit("--compare must be two day counts, e.g. --compare=7,30")
    short_days, long_days = sorted([int(parts[0]), int(parts[1])])
    if short_days <= 0 or long_days <= 0:
        raise SystemExit("--compare day counts must be positive")
    return [
        Window(f"last {long_days}d baseline", end - timedelta(days=long_days - 1), end),
        Window(f"last {short_days}d current", end - timedelta(days=short_days - 1), end),
    ]


def count_items(items: list[EvidenceItem], window: Window) -> int:
    return sum(1 for item in items if item.published_at and window.start <= item.published_at <= window.end)


def compute_momentum(baseline_rate: float, current_rate: float) -> float:
    return (current_rate - baseline_rate) / max(0.05, baseline_rate)


def window_days(window: Window) -> int:
    return max(1, (window.end - window.start).days + 1)


def classify_direction(
    baseline: int,
    current: int,
    momentum: float,
    items: list[EvidenceItem],
    current_window: Window | None,
) -> str:
    if current > 0 and baseline == 0:
        return "emerging"
    if momentum >= 1.0:
        return "rising"
    if momentum <= -1.0:
        return "fading"
    if current_window:
        first_dates = [item.published_at for item in items if item.published_at]
        if first_dates and min(first_dates) >= current_window.start:
            return "emerging"
    return "stable"


def direction_rank(direction: str) -> int:
    return {"emerging": 4, "rising": 3, "stable": 2, "fading": 1}.get(direction, 0)


def theme_relevance(topic: str, title: str, items: list[EvidenceItem]) -> float:
    text = " ".join([title, *[item.title for item in items], *[item.body[:500] for item in items]]).lower()
    normalized_topic = topic.lower().strip()
    tokens = topic_tokens(topic)
    if not tokens:
        return 1.0
    matched = sum(1 for token in tokens if token in text)
    score = matched / len(tokens)
    if normalized_topic and normalized_topic in text:
        score += 0.6
    if "self" in tokens and any(term in text for term in ["self improving", "self-improving", "self learning", "self-learning"]):
        score += 0.4
    if any(token in tokens for token in ["agent", "agents", "agentic"]) and any(
        term in text for term in ["agent", "agents", "agentic", "autonomous"]
    ):
        score += 0.2
    if any(term in text for term in ["feedback", "reflection", "memory", "learning", "improving", "self-upgrading"]):
        score += 0.15
    if is_self_improvement_topic(tokens) and not has_self_improvement_signal(text):
        score = min(score, 0.25)
    return min(score, 1.5)


def topic_tokens(topic: str) -> list[str]:
    stop = {"the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "with", "ai"}
    raw = re.findall(r"[a-z0-9][a-z0-9-]{1,}", topic.lower())
    tokens = []
    for token in raw:
        token = token.rstrip("s")
        if token and token not in stop and token not in tokens:
            tokens.append(token)
    return tokens


def is_self_improvement_topic(tokens: list[str]) -> bool:
    return "self" in tokens and any(token in tokens for token in ["improving", "improv", "learning", "learn"])


def has_self_improvement_signal(text: str) -> bool:
    return any(
        term in text
        for term in [
            "self improving",
            "self-improving",
            "self learning",
            "self-learning",
            "self-upgrading",
            "self upgrading",
            "improving",
            "improvement",
            "learns",
            "learning",
            "feedback",
            "reflection",
            "reflective",
            "adaptation",
            "adapting",
            "memory",
        ]
    )


def evidence_score(raw: dict[str, Any]) -> float:
    engagement = raw.get("engagement")
    score = 0.0
    for key in ("score", "engagement_score", "freshness", "local_rank_score", "local_relevance"):
        value = raw.get(key)
        if isinstance(value, (int, float)):
            score += float(value)
    if isinstance(engagement, dict):
        for key in ("score", "likes", "upvotes", "comments", "views", "rank_score", "postCount"):
            value = engagement.get(key)
            if isinstance(value, (int, float)):
                score += min(float(value), 1000.0) / 20.0
    return score or 1.0


def parse_item_date(raw: dict[str, Any]) -> date | None:
    candidates = [
        raw.get("published_at"),
        raw.get("created_at"),
        raw.get("date"),
        raw.get("posted_at"),
        raw.get("timestamp"),
    ]
    metadata = raw.get("metadata")
    if isinstance(metadata, dict):
        candidates.extend([metadata.get("published_at"), metadata.get("posted_at"), metadata.get("date")])
    for candidate in candidates:
        parsed = try_parse_date(candidate)
        if parsed:
            return parsed
    return None


def try_parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text[:10]):
        return date.fromisoformat(text[:10])
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise SystemExit(f"Invalid date {value!r}; expected YYYY-MM-DD") from exc


def first_text(raw: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def fuzzy_find_item(candidate_id: str, evidence: list[EvidenceItem]) -> EvidenceItem | None:
    for item in evidence:
        if candidate_id and (candidate_id == item.url or candidate_id == item.raw.get("item_id")):
            return item
    for item in evidence:
        if candidate_id and item.url and (candidate_id in item.url or item.url in candidate_id):
            return item
    return None


def serialize_window(window: Window) -> dict[str, str]:
    return {"label": window.label, "start": window.start.isoformat(), "end": window.end.isoformat()}


def serialize_theme(theme: TrendTheme) -> dict[str, Any]:
    return {
        "title": theme.title,
        "direction": theme.direction,
        "momentum": theme.momentum,
        "current_count": theme.current_count,
        "baseline_count": theme.baseline_count,
        "source_diversity": theme.source_diversity,
        "score": theme.score,
        "windows": theme.windows,
        "sources": theme.sources,
        "evidence": [
            {
                "source": item.source,
                "title": item.title,
                "url": item.url,
                "published_at": item.published_at.isoformat() if item.published_at else "",
                "score": round(item.score, 3),
                "snippet": item.body[:300],
            }
            for item in theme.evidence
        ],
    }


def render_markdown(payload: dict[str, Any], html_path: Path | None = None, json_path: Path | None = None) -> str:
    lines = [
        f"📈 trender v{VERSION} · analyzed {payload['generated_at'][:10]}",
        "",
        f"What moved for **{payload['topic']}**:",
        "",
        (
            f"Window: {payload['window']['start']} to {payload['window']['end']} · "
            f"retrieval lookback: {payload['retrieval_days']} days · "
            f"sources: {payload['source_count']}"
        ),
        "",
    ]
    if payload["compare_windows"]:
        labels = " vs ".join(window["label"] for window in payload["compare_windows"])
        lines.extend([f"Comparison: {labels}", ""])

    if not payload["themes"]:
        lines.append("No dateable trend themes were found in the retrieved evidence.")
    else:
        for index, theme in enumerate(payload["themes"][:8], start=1):
            lines.append(
                f"{index}. **{theme['title']}** - {theme['direction']} "
                f"(momentum {theme['momentum']}, diversity {theme['source_diversity']})"
            )
            evidence = theme["evidence"][:2]
            for item in evidence:
                link = f" - {item['url']}" if item["url"] else ""
                date_part = f"{item['published_at']} · " if item["published_at"] else ""
                lines.append(f"   - {date_part}{item['source']}: {item['title']}{link}")
            lines.append("")

    source_counts = payload.get("source_counts", {})
    if source_counts:
        lines.append("Source coverage: " + ", ".join(f"{source}={count}" for source, count in sorted(source_counts.items())))
    for note in payload.get("coverage_notes", []):
        lines.append(f"Coverage note: {note}")
    if html_path:
        lines.append(f"HTML trend map: {html_path}")
    if json_path:
        lines.append(f"JSON data: {json_path}")
    return "\n".join(lines).rstrip() + "\n"


def render_html(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False)
    cards = []
    for theme in payload["themes"]:
        evidence = "".join(
            f"<li><strong>{escape(item['source'])}</strong> {escape(item['published_at'])}: "
            f"<a href=\"{escape(item['url'])}\">{escape(item['title'])}</a></li>"
            for item in theme["evidence"]
        )
        cards.append(
            f"<article class=\"card {escape(theme['direction'])}\">"
            f"<span>{escape(theme['direction'])}</span>"
            f"<h2>{escape(theme['title'])}</h2>"
            f"<p>Momentum {theme['momentum']} · diversity {theme['source_diversity']} · "
            f"current {theme['current_count']} · baseline {theme['baseline_count']}</p>"
            f"<pre>{escape(json.dumps(theme['windows'], indent=2))}</pre>"
            f"<ul>{evidence}</ul>"
            "</article>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trender - {escape(payload['topic'])}</title>
<style>
body {{ margin: 0; background: #0b1020; color: #eef2ff; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
main {{ max-width: 1120px; margin: 0 auto; padding: 32px; }}
.hero, .card {{ background: rgba(255,255,255,.07); border: 1px solid rgba(255,255,255,.12); border-radius: 18px; padding: 22px; margin: 16px 0; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 16px; }}
.card span {{ display: inline-block; padding: 4px 10px; border-radius: 999px; background: #1d4ed8; }}
.emerging span {{ background: #15803d; }} .rising span {{ background: #1d4ed8; }} .fading span {{ background: #991b1b; }} .stable span {{ background: #52525b; }}
a {{ color: #93c5fd; }} pre {{ white-space: pre-wrap; color: #cbd5e1; }}
</style>
</head>
<body>
<main>
<section class="hero">
<h1>Trend map: {escape(payload['topic'])}</h1>
<p>Generated {escape(payload['generated_at'])}</p>
<p>Window {escape(payload['window']['start'])} to {escape(payload['window']['end'])}; retrieval lookback {payload['retrieval_days']} days.</p>
</section>
<section class="grid">
{''.join(cards)}
</section>
</main>
<script type="application/json" id="trender-data">{escape(data)}</script>
</body>
</html>"""


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return slug or "trend"


def coverage_notes(evidence: list[EvidenceItem]) -> list[str]:
    active = sorted({item.source for item in evidence})
    notes: list[str] = []
    if len(active) <= 3:
        notes.append(
            "Only "
            + ", ".join(active or ["no sources"])
            + " returned evidence. Treat source-diversity conclusions as provisional."
        )
    missing_high_signal = [
        source
        for source in ["x", "youtube", "tiktok", "grounding", "perplexity", "digg"]
        if source not in active
    ]
    if missing_high_signal:
        notes.append(
            "High-signal sources not represented in this run: "
            + ", ".join(missing_high_signal)
            + ". Configure the corresponding last30days credentials/backends for broader coverage."
        )
    return notes


if __name__ == "__main__":
    raise SystemExit(main())

