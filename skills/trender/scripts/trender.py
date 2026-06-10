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
import webbrowser
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any

VERSION = "0.5.0"


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
    parser.add_argument("topic", nargs="*", help="Topic to analyze, or 'setup' to configure bundled last30days")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days. Default: 30.")
    parser.add_argument("--from", dest="start", help="Explicit start date YYYY-MM-DD.")
    parser.add_argument("--to", dest="end", help="Explicit end date YYYY-MM-DD.")
    parser.add_argument(
        "--compare",
        help="Compare two lookback windows, e.g. 7,30 or 30,90. Uses current N days vs current M days.",
    )
    parser.add_argument("--emit", choices=["md", "json", "html", "all"], default="html")
    parser.add_argument("--save-dir", default=os.getenv("TRENDER_OUTPUT_DIR", str(Path.home() / "Documents" / "Trender")))
    parser.add_argument("--last30days-dir", default=os.getenv("LAST30DAYS_SKILL_DIR"))
    parser.add_argument("--search", help="Comma-separated source list passed through to last30days.")
    parser.add_argument("--quick", action="store_true", help="Pass --quick through to last30days.")
    parser.add_argument("--deep", action="store_true", help="Pass --deep through to last30days.")
    parser.add_argument("--mock", action="store_true", help="Use last30days mock retrieval fixtures.")
    parser.add_argument("--web-research", choices=["off", "mock"], default="off", help="Deprecated. Use --agent-web-file. 'mock' is kept for tests.")
    parser.add_argument(
        "--agent-web-file",
        default=os.getenv("TRENDER_AGENT_WEB_FILE"),
        help=(
            "JSON file of web evidence gathered by the host coding agent. "
            "Schema: {items:[{title,url,published_at,summary,trend_theme,relevance_score}]} or a bare list."
        ),
    )
    parser.add_argument("--keep-raw", action="store_true", help="Save raw last30days JSON beside Trender outputs.")
    parser.add_argument("--diagnose", action="store_true", help="Show bundled last30days source/provider availability.")
    parser.add_argument("--no-open", action="store_true", help="Do not open generated HTML reports automatically.")
    parser.add_argument(
        "--skip-last30days-preflight",
        action="store_true",
        help="Bypass last30days preflight checks. Use only when you intentionally want to skip setup/gates.",
    )
    args = parser.parse_args()

    topic = " ".join(args.topic).strip()
    skill_dir = Path(__file__).resolve().parents[1]
    last30days_dir = resolve_last30days_dir(args.last30days_dir, skill_dir)

    if args.diagnose:
        return run_last30days_passthrough(last30days_dir, ["--diagnose"])
    if topic.lower() == "setup":
        return run_last30days_passthrough(last30days_dir, ["setup"])

    if not topic:
        raise SystemExit("topic is required, or run: trender.py setup / trender.py --diagnose")

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

    raw_report = run_last30days(
        last30days_dir=last30days_dir,
        topic=topic,
        days=required_lookback,
        search=args.search,
        quick=args.quick,
        deep=args.deep,
        mock=args.mock,
        skip_preflight=args.skip_last30days_preflight,
    )
    evidence = flatten_evidence(raw_report)
    clusters = raw_report.get("clusters", [])
    native_web_items = mock_web_research(topic, primary_window) if args.web_research == "mock" else []
    if native_web_items:
        evidence.extend(native_web_items)
        clusters.extend(clusters_from_native_web(native_web_items))
    agent_web_items = load_agent_web_research(
        path=args.agent_web_file,
        topic=topic,
        window=primary_window,
    )
    if agent_web_items:
        evidence.extend(agent_web_items)
        clusters.extend(clusters_from_native_web(agent_web_items))
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
        "native_web_research": {
            "mode": args.web_research,
            "items": len(native_web_items),
            "agent_items": len(agent_web_items),
            "agent_web_file": str(args.agent_web_file or ""),
            "available": {"agent_web_file": bool(args.agent_web_file)},
        },
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
        if not args.no_open:
            open_html_report(html_path)
        write_markdown(render_markdown(payload, html_path=html_path))
    else:
        write_markdown(render_markdown(payload))

    if args.emit == "all":
        json_path = save_dir / f"{slug}-trend-map.json"
        html_path = save_dir / f"{slug}-trend-map.html"
        json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        html_path.write_text(render_html(payload), encoding="utf-8")
        if not args.no_open:
            open_html_report(html_path)
        write_markdown(render_markdown(payload, html_path=html_path, json_path=json_path))

    return 0


def load_agent_web_research(
    *,
    path: str | None,
    topic: str,
    window: Window,
) -> list[EvidenceItem]:
    if not path:
        return []
    source_path = Path(path).expanduser()
    if not source_path.exists():
        raise SystemExit(f"--agent-web-file does not exist: {source_path}")
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    raw_items = payload.get("items", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        raise SystemExit("--agent-web-file must be a JSON list or an object with an 'items' list")
    return web_items_from_payload(
        raw_items,
        source="trender-agent-web",
        topic=topic,
        window=window,
    )


def mock_web_research(topic: str, window: Window) -> list[EvidenceItem]:
    samples = [
        ("Primary research and analysis", f"{topic} research analysis shows new adoption patterns"),
        ("Implementation releases", f"New open implementation demonstrates {topic} in practice"),
        ("Market and adoption signals", f"Teams report production experiments around {topic}"),
    ]
    items = []
    for index, (theme, title) in enumerate(samples):
        published = window.end - timedelta(days=index * 7)
        items.append(
            EvidenceItem(
                id=f"mock-web-{index}",
                source="trender-web-mock",
                title=title,
                body=f"Mock web evidence for {topic}.",
                url=f"https://example.com/{slugify(topic)}/{index}",
                published_at=published,
                score=40.0 - index,
                raw={"trend_theme": theme},
            )
        )
    return items


def clusters_from_native_web(items: list[EvidenceItem]) -> list[dict[str, Any]]:
    grouped: dict[str, list[EvidenceItem]] = defaultdict(list)
    for item in items:
        grouped[str(item.raw.get("trend_theme") or classify_web_result_theme("", item.title, item.body))].append(item)
    return [
        {
            "cluster_id": f"trender-web-{slugify(theme)}",
            "title": theme,
            "candidate_ids": [item.id for item in grouped_items],
            "representative_ids": [grouped_items[0].id],
            "score": sum(item.score for item in grouped_items),
            "sources": sorted({item.source for item in grouped_items}),
        }
        for theme, grouped_items in grouped.items()
        if grouped_items
    ]


def web_items_from_payload(raw_items: list[Any], *, source: str, topic: str, window: Window) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue
        url = str(raw.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        published = try_parse_date(raw.get("published_at")) or window.end
        if not (window.start <= published <= window.end):
            continue
        title = str(raw.get("title") or url)
        summary = str(raw.get("summary") or "")
        theme = str(raw.get("trend_theme") or classify_web_result_theme(topic, title, summary))
        try:
            relevance = float(raw.get("relevance_score", 0.75))
        except (TypeError, ValueError):
            relevance = 0.75
        items.append(
            EvidenceItem(
                id=url,
                source=source,
                title=title,
                body=summary,
                url=url,
                published_at=published,
                score=max(1.0, relevance * 100.0),
                raw={**raw, "trend_theme": theme},
            )
        )
        if len(items) >= 50:
            break
    return items


def classify_web_result_theme(topic: str, title: str, body: str) -> str:
    text = f"{title} {body}".lower()
    topic_label = md_text(topic).strip() or "Web evidence"
    if any(term in text for term in ["paper", "research", "benchmark", "evaluation", "study", "analysis"]):
        return f"{topic_label}: research, benchmarks, and evaluation"
    if any(term in text for term in ["release", "launch", "github", "open source", "implementation", "demo", "tool"]):
        return f"{topic_label}: implementations, releases, and demos"
    if any(term in text for term in ["adoption", "production", "workflow", "use case", "enterprise", "customer"]):
        return f"{topic_label}: workflows, use cases, and adoption"
    if any(term in text for term in ["funding", "market", "acquisition", "report", "announces", "news"]):
        return f"{topic_label}: news, market, and external signals"
    if any(term in text for term in ["problem", "risk", "limit", "issue", "bug", "cost", "pricing"]):
        return f"{topic_label}: problems, limits, and adoption friction"
    return f"{topic_label}: web evidence"


def write_markdown(markdown: str) -> None:
    output = markdown.replace("\n", os.linesep)
    if not markdown.endswith("\n"):
        output += os.linesep
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(output.encode("utf-8", errors="replace"))
        buffer.flush()
    else:
        sys.stdout.write(output)


def open_html_report(path: Path) -> None:
    try:
        webbrowser.open(path.resolve().as_uri())
    except Exception:
        if os.name == "nt":
            os.startfile(str(path.resolve()))  # type: ignore[attr-defined]
        else:
            raise


def run_last30days_passthrough(last30days_dir: Path, args: list[str]) -> int:
    cmd = [
        resolve_python_for_last30days(),
        str(last30days_dir / "scripts" / "last30days.py"),
        *args,
    ]
    proc = subprocess.run(cmd, cwd=str(last30days_dir), env=os.environ.copy(), check=False)
    return proc.returncode


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
    skip_preflight: bool,
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
    if skip_preflight:
        env["LAST30DAYS_SKIP_PREFLIGHT"] = "1"
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
            "search_query": f"{core} trends emerging patterns adoption examples",
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
    themes = consolidate_themes(topic, themes, all_windows, bool(compare_windows))
    themes.sort(key=lambda theme: (direction_rank(theme.direction), theme.momentum, theme.score), reverse=True)
    return themes[:12]


def consolidate_themes(
    topic: str,
    themes: list[TrendTheme],
    windows: list[Window],
    compare_mode: bool,
) -> list[TrendTheme]:
    grouped: dict[str, list[EvidenceItem]] = defaultdict(list)
    passthrough: list[TrendTheme] = []
    seen_by_group: dict[str, set[str]] = defaultdict(set)

    for theme in themes:
        group = classify_theme_group(topic, theme)
        if not group:
            passthrough.append(theme)
            continue
        for item in theme.evidence:
            if item.id in seen_by_group[group]:
                continue
            grouped[group].append(item)
            seen_by_group[group].add(item.id)

    consolidated = [
        theme_from_items(group, items, windows, topic=topic, compare_mode=compare_mode)
        for group, items in grouped.items()
        if items
    ]
    return [theme for theme in consolidated if theme.score > 0] + passthrough


def classify_theme_group(topic: str, theme: TrendTheme) -> str:
    text = " ".join([theme.title, *[item.title for item in theme.evidence], *[item.body[:500] for item in theme.evidence]]).lower()
    topic_label = md_text(topic).strip()
    if any(term in text for term in ["bug", "issue", "limit", "limitation", "regression", "broken", "fail", "error", "complaint", "pricing", "quota", "cost"]):
        return f"{topic_label}: problems, limits, and adoption friction"
    if any(term in text for term in ["paper", "arxiv", "research", "benchmark", "evaluation", "eval", "study", "analysis", "case study"]):
        return f"{topic_label}: research, benchmarks, and evaluation"
    if any(term in text for term in ["github", "repo", "release", "open source", "open-source", "implementation", "framework", "tool", "library", "show hn", "demo", "launch"]):
        return f"{topic_label}: implementations, releases, and demos"
    if any(term in text for term in ["workflow", "workflows", "use case", "use cases", "adoption", "production", "teams", "users", "customer", "enterprise"]):
        return f"{topic_label}: workflows, use cases, and adoption"
    if any(term in text for term in ["news", "report", "funding", "market", "polymarket", "odds", "acquisition", "announces"]):
        return f"{topic_label}: news, market, and external signals"
    compact = re.sub(r"\s+", " ", md_text(theme.title)).strip()
    return compact[:96] or f"{topic_label}: uncategorized evidence"


def theme_from_items(
    title: str,
    items: list[EvidenceItem],
    windows: list[Window],
    *,
    topic: str,
    compare_mode: bool,
) -> TrendTheme:
    relevance = theme_relevance(topic, title, items)
    min_relevance = 0.5 if len(topic_tokens(topic)) >= 2 else 0.34
    if relevance < min_relevance:
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
    item_text = " ".join([*[item.title for item in items], *[item.body[:500] for item in items]]).lower()
    text = " ".join([title.lower(), item_text])
    normalized_topic = topic.lower().strip()
    tokens = topic_tokens(topic)
    if not tokens:
        return 1.0
    text_tokens = set(topic_tokens(text))
    matched = sum(1 for token in tokens if token in text_tokens)
    score = matched / len(tokens)
    if normalized_topic and normalized_topic in text:
        score += 0.6
    if topic_phrase_match(tokens, item_text):
        score += 0.35
    return min(score, 1.5)


def topic_tokens(topic: str) -> list[str]:
    stop = {"the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "with", "ai"}
    raw = re.findall(r"[a-z0-9][a-z0-9-]{1,}", topic.lower())
    tokens = []
    for token in raw:
        token = normalize_token(token)
        if token and token not in stop and token not in tokens:
            tokens.append(token)
    return tokens


def normalize_token(token: str) -> str:
    token = token.lower().strip("-_").rstrip("s")
    replacements = {
        "improving": "improv",
        "improvement": "improv",
        "improve": "improv",
        "improved": "improv",
        "learning": "learn",
        "learned": "learn",
        "agents": "agent",
        "agentic": "agent",
        "tools": "tool",
        "servers": "server",
    }
    return replacements.get(token, token)


def topic_phrase_match(tokens: list[str], text: str) -> bool:
    if len(tokens) < 2:
        return False
    text_tokens = topic_tokens(text)
    text_bigrams = set(zip(text_tokens, text_tokens[1:]))
    query_bigrams = set(zip(tokens, tokens[1:]))
    return bool(text_bigrams & query_bigrams)


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
        "evidence_count": len(theme.evidence),
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
        f"trender v{VERSION} - analyzed {payload['generated_at'][:10]}",
        "",
        f"What moved for **{md_text(payload['topic'])}**:",
        "",
        (
            f"Window: {payload['window']['start']} to {payload['window']['end']} - "
            f"retrieval lookback: {payload['retrieval_days']} days - "
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
                f"{index}. **{md_text(theme['title'])}** - {theme['direction']} "
                f"(momentum {theme['momentum']}, evidence {theme['evidence_count']}, diversity {theme['source_diversity']})"
            )
            if theme["current_count"] or theme["baseline_count"]:
                lines.append(
                    f"   - Signal moved from {theme['baseline_count']} baseline item(s) to "
                    f"{theme['current_count']} recent/current item(s)."
                )
            evidence = theme["evidence"][:2]
            for item in evidence:
                link = f" - {item['url']}" if item["url"] else ""
                date_part = f"{item['published_at']} · " if item["published_at"] else ""
                date_part = f"{item['published_at']} - " if item["published_at"] else ""
                lines.append(f"   - {date_part}{item['source']}: {md_text(item['title'])}{link}")
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


def md_text(value: Any) -> str:
    return (
        str(value)
        .replace("—", "-")
        .replace("–", "-")
        .replace("·", "-")
        .replace("“", '"')
        .replace("”", '"')
        .replace("’", "'")
        .replace("‘", "'")
        .replace("☀️", "")
        .replace("☀", "")
    )


def render_html(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False)
    themes = payload.get("themes", [])
    source_counts = payload.get("source_counts", {})
    direction_counts = Counter(theme.get("direction", "unknown") for theme in themes)
    total_evidence = sum(int(theme.get("evidence_count", 0)) for theme in themes)
    max_source_count = max([1, *[int(value) for value in source_counts.values()]])
    max_window_count = max(
        [1, *[int(value) for theme in themes for value in theme.get("windows", {}).values()]]
    )

    source_bars = "".join(
        f"""
        <div class="bar-row">
          <span>{escape(source)}</span>
          <div class="bar-track"><div class="bar-fill" style="width:{(count / max_source_count) * 100:.1f}%"></div></div>
          <strong>{count}</strong>
        </div>
        """
        for source, count in sorted(source_counts.items(), key=lambda item: item[1], reverse=True)
    )

    direction_chips = "".join(
        f"<span class=\"chip {escape(direction)}\">{escape(direction)} {count}</span>"
        for direction, count in sorted(direction_counts.items())
    )

    cards = []
    for index, theme in enumerate(themes, start=1):
        windows = theme.get("windows", {})
        timeline = "".join(
            f"""
            <div class="tick" title="{escape(label)}: {count}">
              <div class="tick-bar" style="height:{max(8, (int(count) / max_window_count) * 72):.1f}px"></div>
            </div>
            """
            for label, count in windows.items()
        )
        evidence = "".join(
            f"""
            <li>
              <div class="evidence-meta">{escape(item.get('published_at') or 'unknown date')} · {escape(item.get('source') or 'source')}</div>
              <a href="{escape(item.get('url') or '#')}" target="_blank" rel="noreferrer">{escape(md_text(item.get('title') or 'Untitled evidence'))}</a>
              <p>{escape(md_text(item.get('snippet') or ''))}</p>
            </li>
            """
            for item in theme.get("evidence", [])
        )
        cards.append(
            f"""
            <article class="theme-card {escape(theme.get('direction', 'stable'))}">
              <div class="theme-rank">{index}</div>
              <div class="theme-body">
                <div class="theme-topline">
                  <span class="chip {escape(theme.get('direction', 'stable'))}">{escape(theme.get('direction', 'stable'))}</span>
                  <span class="muted">momentum {theme.get('momentum', 0)} · evidence {theme.get('evidence_count', 0)} · diversity {theme.get('source_diversity', 0)}</span>
                </div>
                <h2>{escape(md_text(theme.get('title') or 'Untitled trend'))}</h2>
                <div class="movement">
                  <div><strong>{theme.get('baseline_count', 0)}</strong><span>baseline</span></div>
                  <div class="arrow">→</div>
                  <div><strong>{theme.get('current_count', 0)}</strong><span>recent/current</span></div>
                </div>
                <div class="timeline" aria-label="timeline">{timeline}</div>
                <details>
                  <summary>Original evidence</summary>
                  <ul class="evidence-list">{evidence}</ul>
                </details>
              </div>
            </article>
            """
        )

    coverage_notes = "".join(
        f"<li>{escape(note)}</li>" for note in payload.get("coverage_notes", [])
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trender - {escape(payload['topic'])}</title>
<style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: radial-gradient(circle at top left, #172554, #08111f 45%, #050816); color: #eef2ff; font-family: Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif; }}
main {{ max-width: 1180px; margin: 0 auto; padding: 32px; }}
.hero {{ border: 1px solid rgba(148,163,184,.25); background: linear-gradient(135deg, rgba(59,130,246,.22), rgba(15,23,42,.86)); border-radius: 28px; padding: 30px; box-shadow: 0 24px 80px rgba(0,0,0,.35); }}
.eyebrow {{ color: #67e8f9; font-size: 12px; font-weight: 800; letter-spacing: .22em; text-transform: uppercase; }}
h1 {{ margin: 8px 0 10px; font-size: clamp(34px, 6vw, 64px); line-height: .95; }}
.subtitle, .muted {{ color: #a8b3cf; }}
.metrics {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin-top: 24px; }}
.metric {{ background: rgba(15,23,42,.72); border: 1px solid rgba(148,163,184,.2); border-radius: 18px; padding: 16px; }}
.metric strong {{ display: block; font-size: 30px; }}
.metric span {{ color: #a8b3cf; font-size: 13px; }}
.layout {{ display: grid; grid-template-columns: 340px 1fr; gap: 18px; margin-top: 18px; align-items: start; }}
.panel, .theme-card {{ background: rgba(15,23,42,.78); border: 1px solid rgba(148,163,184,.18); border-radius: 22px; box-shadow: 0 18px 60px rgba(0,0,0,.24); }}
.panel {{ padding: 18px; }}
.panel h2 {{ margin: 0 0 14px; font-size: 16px; }}
.bar-row {{ display: grid; grid-template-columns: 86px 1fr 32px; gap: 10px; align-items: center; margin: 10px 0; font-size: 13px; }}
.bar-track {{ height: 10px; border-radius: 999px; background: rgba(148,163,184,.18); overflow: hidden; }}
.bar-fill {{ height: 100%; border-radius: inherit; background: linear-gradient(90deg, #22d3ee, #a78bfa); }}
.chips {{ display: flex; flex-wrap: wrap; gap: 8px; }}
.chip {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 5px 10px; font-size: 12px; font-weight: 800; background: #334155; color: #e2e8f0; text-transform: uppercase; letter-spacing: .04em; }}
.chip.emerging {{ background: #14532d; color: #bbf7d0; }} .chip.rising {{ background: #1e3a8a; color: #bfdbfe; }} .chip.fading {{ background: #7f1d1d; color: #fecaca; }} .chip.stable {{ background: #3f3f46; color: #e4e4e7; }}
.notes {{ color: #fef3c7; padding-left: 20px; }}
.theme-list {{ display: grid; gap: 14px; }}
.theme-card {{ display: grid; grid-template-columns: 54px 1fr; padding: 0; overflow: hidden; }}
.theme-rank {{ display: grid; place-items: start center; padding-top: 20px; font-size: 24px; font-weight: 900; color: rgba(255,255,255,.28); background: rgba(255,255,255,.04); }}
.theme-body {{ padding: 18px; }}
.theme-topline {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
.theme-card h2 {{ margin: 12px 0; font-size: 22px; line-height: 1.18; }}
.movement {{ display: inline-grid; grid-template-columns: auto 28px auto; align-items: center; gap: 10px; margin: 4px 0 14px; }}
.movement div:not(.arrow) {{ background: rgba(255,255,255,.06); border-radius: 14px; padding: 9px 12px; }}
.movement strong {{ display: block; font-size: 22px; }}
.movement span {{ color: #a8b3cf; font-size: 12px; }}
.arrow {{ color: #67e8f9; font-weight: 900; }}
.timeline {{ display: flex; align-items: end; gap: 3px; min-height: 86px; padding: 10px; border-radius: 16px; background: rgba(2,6,23,.55); border: 1px solid rgba(148,163,184,.14); overflow-x: auto; }}
.tick {{ min-width: 10px; display: flex; align-items: end; justify-content: center; }}
.tick-bar {{ width: 8px; min-height: 8px; border-radius: 999px 999px 2px 2px; background: linear-gradient(180deg, #67e8f9, #2563eb); }}
details {{ margin-top: 12px; }}
summary {{ cursor: pointer; color: #93c5fd; font-weight: 700; }}
.evidence-list {{ padding-left: 18px; }}
.evidence-list li {{ margin: 12px 0; }}
.evidence-meta {{ color: #94a3b8; font-size: 12px; margin-bottom: 3px; }}
.evidence-list p {{ color: #a8b3cf; margin: 4px 0 0; }}
a {{ color: #93c5fd; }}
@media (max-width: 900px) {{ main {{ padding: 18px; }} .metrics, .layout {{ grid-template-columns: 1fr; }} .theme-card {{ grid-template-columns: 42px 1fr; }} }}
</style>
</head>
<body>
<main>
<section class="hero">
<div class="eyebrow">Trender v{VERSION}</div>
<h1>{escape(md_text(payload['topic']))}</h1>
<p class="subtitle">Generated {escape(payload['generated_at'])}. Window {escape(payload['window']['start'])} to {escape(payload['window']['end'])}; retrieval lookback {payload['retrieval_days']} days.</p>
<div class="metrics">
  <div class="metric"><strong>{payload['source_count']}</strong><span>source items</span></div>
  <div class="metric"><strong>{len(themes)}</strong><span>trend themes</span></div>
  <div class="metric"><strong>{total_evidence}</strong><span>theme evidence links</span></div>
  <div class="metric"><strong>{len(source_counts)}</strong><span>active sources</span></div>
</div>
</section>
<section class="layout">
  <aside class="panel">
    <h2>Source coverage</h2>
    {source_bars or '<p class="muted">No source evidence returned.</p>'}
    <h2 style="margin-top:22px">Directions</h2>
    <div class="chips">{direction_chips or '<span class="chip">none</span>'}</div>
    <h2 style="margin-top:22px">Coverage notes</h2>
    <ul class="notes">{coverage_notes or '<li>No coverage warnings.</li>'}</ul>
  </aside>
  <section class="theme-list">
    {''.join(cards) or '<article class="theme-card"><div class="theme-rank">0</div><div class="theme-body"><h2>No trend themes found</h2><p class="muted">Try a broader query or configure more last30days providers.</p></div></article>'}
  </section>
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
    if "trender-agent-web" not in active:
        notes.append(
            "No host-agent web evidence was provided. For broader web coverage, have the coding agent write a JSON evidence file and pass --agent-web-file."
        )
    return notes


if __name__ == "__main__":
    raise SystemExit(main())

