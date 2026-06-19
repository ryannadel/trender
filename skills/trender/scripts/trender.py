#!/usr/bin/env python3
"""Trender skill engine (rewrite).

Trender maps how a topic is evolving across time. The host coding agent is the
primary researcher: it gathers bucketed web evidence (research, implementations,
adoption, criticism, forecasts) and writes it to JSON. last30days adds
community color on top. This script analyzes the combined evidence for:

  - Emerging / accelerating / fading / stable themes (TF-IDF clusters)
  - Linear-regression slope across time buckets
  - Emerging entities (capitalized n-grams new to the current window)
  - Vocabulary drift (terms with biggest baseline -> current frequency lift)
  - Inflection moments (biggest week-over-week jumps)
  - Then/now quote pairs per theme
  - Forward signals (predictions, roadmaps, forecasts, betting markets)

It then renders a self-contained HTML trend map and a Markdown synthesis.
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
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any

VERSION = "0.6.0"


@dataclass(frozen=True)
class Window:
    label: str
    start: date
    end: date

    @property
    def days(self) -> int:
        return max(1, (self.end - self.start).days + 1)


@dataclass
class EvidenceItem:
    id: str
    source: str
    title: str
    body: str
    url: str
    published_at: date | None
    score: float
    bucket: str
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.body}".strip()


@dataclass
class TrendTheme:
    title: str
    direction: str
    slope: float
    momentum: float
    current_count: int
    baseline_count: int
    source_diversity: int
    bucket_mix: dict[str, int]
    score: float
    bucket_counts: dict[str, int]
    sources: list[str]
    evidence: list[EvidenceItem]
    then_quote: EvidenceItem | None
    now_quote: EvidenceItem | None
    terms: list[str]


@dataclass
class InflectionMoment:
    bucket_label: str
    bucket_start: str
    bucket_end: str
    prior_count: int
    bucket_count: int
    lift: float
    headline: EvidenceItem | None


BUCKETS = ("research", "implementations", "adoption", "criticism", "forecasts", "community")


def main() -> int:
    configure_stdio()
    parser = build_parser()
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
    explicit_window = bool(args.start or args.compare or args.days_was_set)
    if args.compare:
        compare_windows = parse_compare(args.compare, analysis_end)
    elif not explicit_window:
        compare_windows = parse_compare("30,180", analysis_end)
    else:
        compare_windows = []

    if args.start:
        analysis_start = parse_date(args.start)
        if analysis_start > analysis_end:
            raise SystemExit("--from must be before --to")
    elif compare_windows:
        analysis_start = compare_windows[0].start
    else:
        analysis_start = analysis_end - timedelta(days=max(1, args.days) - 1)

    primary_window = Window("selected", analysis_start, analysis_end)
    required_lookback = max(
        [(analysis_end - analysis_start).days + 1]
        + [(date.today() - w.start).days + 1 for w in compare_windows]
    )

    evidence: list[EvidenceItem] = []
    if args.mock:
        evidence.extend(mock_evidence(topic, primary_window, compare_windows))
    else:
        raw_report = run_last30days(
            last30days_dir=last30days_dir,
            topic=topic,
            days=max(1, required_lookback),
            search=args.search,
            quick=args.quick,
            deep=args.deep,
            skip_preflight=args.skip_last30days_preflight,
        )
        evidence.extend(flatten_last30days_evidence(raw_report))

    agent_items = load_agent_web_research(args.agent_web_file)
    evidence.extend(agent_items)
    evidence = dedupe_evidence(evidence)

    overall_start = min([w.start for w in compare_windows] + [primary_window.start])
    overall_end = primary_window.end
    in_scope = [
        e for e in evidence
        if e.published_at is None or overall_start <= e.published_at <= overall_end
    ]

    buckets = make_time_buckets(overall_start, overall_end)
    themes = cluster_and_analyze(
        in_scope,
        topic=topic,
        buckets=buckets,
        compare_windows=compare_windows or [primary_window],
    )
    emerging_entities = compute_emerging_entities(in_scope, compare_windows or [primary_window])
    vocab_drift = compute_vocabulary_drift(in_scope, compare_windows or [primary_window])
    inflections = compute_inflection_moments(in_scope, buckets)
    forward = collect_forward_signals(in_scope)

    payload = {
        "topic": topic,
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": serialize_window(primary_window),
        "compare_windows": [serialize_window(w) for w in compare_windows],
        "buckets": [serialize_window(b) for b in buckets],
        "retrieval_days": required_lookback,
        "source_count": len(in_scope),
        "source_counts": dict(Counter(e.source for e in in_scope)),
        "bucket_counts": dict(Counter(e.bucket for e in in_scope)),
        "themes": [serialize_theme(t) for t in themes],
        "emerging_entities": emerging_entities,
        "vocabulary_drift": vocab_drift,
        "inflection_moments": [serialize_inflection(m) for m in inflections],
        "forward_signals": [serialize_evidence(e) for e in forward[:12]],
        "coverage_notes": coverage_notes(in_scope, bool(args.agent_web_file)),
        "last30days_dir": str(last30days_dir),
    }

    save_dir = Path(args.save_dir).expanduser()
    save_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(topic)
    html_path = save_dir / f"{slug}-trend-map.html"
    json_path = save_dir / f"{slug}-trend-map.json"

    if args.emit in ("html", "all"):
        html_path.write_text(render_html(payload), encoding="utf-8")
    if args.emit in ("json", "all"):
        json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if args.emit == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    md = render_markdown(
        payload,
        html_path=html_path if args.emit in ("html", "all") else None,
        json_path=json_path if args.emit == "all" else None,
    )
    write_stdout(md)
    if args.emit in ("html", "all") and not args.no_open:
        open_html_report(html_path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Map topic trends across time.")
    parser.add_argument("topic", nargs="*")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--from", dest="start")
    parser.add_argument("--to", dest="end")
    parser.add_argument("--compare", help="Two lookbacks, e.g. 30,180. Default when no window specified.")
    parser.add_argument("--emit", choices=["md", "json", "html", "all"], default="html")
    parser.add_argument(
        "--save-dir",
        default=os.getenv("TRENDER_OUTPUT_DIR", str(Path.home() / "Documents" / "Trender")),
    )
    parser.add_argument("--last30days-dir", default=os.getenv("LAST30DAYS_SKILL_DIR"))
    parser.add_argument("--search")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--deep", action="store_true")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--agent-web-file", default=os.getenv("TRENDER_AGENT_WEB_FILE"))
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--skip-last30days-preflight", action="store_true")
    args_seen = sys.argv[1:]
    parser.set_defaults(days_was_set=any(a == "--days" or a.startswith("--days=") for a in args_seen))
    return parser


def load_agent_web_research(path: str | None) -> list[EvidenceItem]:
    if not path:
        return []
    p = Path(path).expanduser()
    if not p.exists():
        raise SystemExit(f"--agent-web-file does not exist: {p}")
    payload = json.loads(p.read_text(encoding="utf-8"))
    items: list[EvidenceItem] = []
    if isinstance(payload, dict) and isinstance(payload.get("buckets"), dict):
        for bucket_name, bucket_items in payload["buckets"].items():
            if not isinstance(bucket_items, list):
                continue
            for raw in bucket_items:
                item = build_evidence_from_agent(raw, default_bucket=bucket_name)
                if item:
                    items.append(item)
    else:
        legacy_items = payload.get("items", payload) if isinstance(payload, dict) else payload
        if not isinstance(legacy_items, list):
            raise SystemExit("--agent-web-file must be a JSON object with 'buckets' or 'items'.")
        for raw in legacy_items:
            item = build_evidence_from_agent(raw, default_bucket=None)
            if item:
                items.append(item)
    return items


def build_evidence_from_agent(raw: Any, *, default_bucket: str | None) -> EvidenceItem | None:
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("url") or "").strip()
    if not url:
        return None
    title = str(raw.get("title") or url).strip()
    body = str(raw.get("summary") or raw.get("body") or "").strip()
    published = try_parse_date(raw.get("published_at"))
    bucket = (default_bucket or infer_bucket(raw, title, body) or "research").lower()
    if bucket not in BUCKETS:
        bucket = "research"
    try:
        relevance = float(raw.get("relevance_score", 0.75))
    except (TypeError, ValueError):
        relevance = 0.75
    return EvidenceItem(
        id=url,
        source=str(raw.get("source") or "agent-web"),
        title=title,
        body=body,
        url=url,
        published_at=published,
        score=max(1.0, relevance * 100.0),
        bucket=bucket,
        raw=raw,
    )


def infer_bucket(raw: dict[str, Any], title: str, body: str) -> str | None:
    explicit = str(raw.get("bucket") or raw.get("trend_theme") or "").lower()
    if explicit:
        for b in BUCKETS:
            if b in explicit:
                return b
    text = f"{title} {body}".lower()
    if any(k in text for k in ("arxiv", "paper", "benchmark", "evaluation", "study")):
        return "research"
    if any(k in text for k in ("release", "open source", "github", "implementation", "demo", "launch")):
        return "implementations"
    if any(k in text for k in ("adoption", "production", "enterprise", "workflow", "customer", "deployed")):
        return "adoption"
    if any(k in text for k in ("limit", "problem", "broken", "bug", "criticism", "concern", "risk")):
        return "criticism"
    if any(k in text for k in ("forecast", "predict", "by 202", "roadmap", "rfc", "expects", "polymarket")):
        return "forecasts"
    return None


def run_last30days_passthrough(last30days_dir: Path, args: list[str]) -> int:
    cmd = [resolve_python_for_last30days(), str(last30days_dir / "scripts" / "last30days.py"), *args]
    proc = subprocess.run(cmd, cwd=str(last30days_dir), env=os.environ.copy(), check=False)
    return proc.returncode


def run_last30days(*, last30days_dir: Path, topic: str, days: int, search: str | None,
                    quick: bool, deep: bool, skip_preflight: bool) -> dict[str, Any]:
    plan_path = write_trender_plan(topic)
    cmd = [
        resolve_python_for_last30days(),
        str(last30days_dir / "scripts" / "last30days.py"),
        topic, "--emit=json", f"--days={days}",
        "--plan", str(plan_path),
    ]
    if search:
        cmd.extend(["--search", search])
    if quick:
        cmd.append("--quick")
    if deep:
        cmd.append("--deep")
    env = os.environ.copy()
    if skip_preflight:
        env["LAST30DAYS_SKIP_PREFLIGHT"] = "1"
    try:
        proc = subprocess.run(
            cmd, cwd=str(last30days_dir), env=env,
            text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if proc.returncode != 0:
            raise SystemExit(
                "last30days retrieval failed.\n"
                f"Command: {' '.join(cmd)}\nSTDERR:\n{proc.stderr}\nSTDOUT:\n{proc.stdout[-4000:]}"
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
    primary = ["reddit", "hackernews", "x", "github", "grounding"]
    research = ["hackernews", "grounding", "perplexity", "github"]
    implementations = ["github", "hackernews", "x"]
    criticism = ["reddit", "x", "hackernews", "bluesky"]
    forecasts = ["polymarket", "grounding", "x"]
    broader = ["youtube", "tiktok", "instagram", "threads", "pinterest", "digg", "xiaohongshu", "truthsocial"]
    all_sources = sorted({s for grp in [primary, research, implementations, criticism, forecasts, broader] for s in grp})
    source_weights = {s: 1.0 for s in all_sources}
    source_weights.update({
        "hackernews": 1.35, "github": 1.3, "grounding": 1.25, "perplexity": 1.2,
        "reddit": 1.2, "x": 1.1, "polymarket": 1.1,
    })
    subqueries = [
        {"label": "primary", "search_query": core,
         "ranking_query": f"What recent evidence shows {core} changing, accelerating, or becoming important?",
         "sources": primary, "weight": 1.2},
        {"label": "research-and-claims", "search_query": f"{core} research paper benchmark evaluation",
         "ranking_query": f"What new papers, benchmarks, or evaluations characterize {core}?",
         "sources": research, "weight": 1.0},
        {"label": "implementations", "search_query": f"{core} open source release implementation",
         "ranking_query": f"Which new releases, repos, or implementations show momentum around {core}?",
         "sources": implementations, "weight": 1.0},
        {"label": "community-friction", "search_query": f"{core} problems limitations adoption workflows",
         "ranking_query": f"What are practitioners saying about limits, friction, or adoption of {core}?",
         "sources": criticism, "weight": 0.95},
        {"label": "forecasts", "search_query": f"{core} prediction forecast roadmap will",
         "ranking_query": f"What predictions, forecasts, or roadmaps point at where {core} is going?",
         "sources": forecasts, "weight": 0.85},
        {"label": "broader-social", "search_query": core,
         "ranking_query": f"Mainstream/social discussion volume around {core}.",
         "sources": broader, "weight": 0.6},
    ]
    return {
        "intent": "concept", "freshness_mode": "balanced_recent", "cluster_mode": "story",
        "source_weights": source_weights, "subqueries": subqueries,
        "notes": [
            "Generated by Trender. Subqueries route to source subsets matching their intent.",
            "Trender prefers concrete time-stamped evidence and cross-source corroboration.",
        ],
    }


def flatten_last30days_evidence(report: dict[str, Any]) -> list[EvidenceItem]:
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
            score = evidence_score_raw(raw)
            item_id = str(url or raw.get("item_id") or f"{source}:{len(items)}")
            bucket = source_to_bucket(source) or infer_bucket(raw, title, body) or "community"
            items.append(EvidenceItem(
                id=item_id, source=source, title=str(title), body=str(body), url=str(url),
                published_at=published, score=score, bucket=bucket, raw=raw,
            ))
    return items


def source_to_bucket(source: str) -> str | None:
    src = source.lower()
    if src == "github":
        return "implementations"
    if src == "polymarket":
        return "forecasts"
    if src in ("perplexity", "grounding"):
        return "research"
    if src in ("reddit", "x", "bluesky", "threads", "tiktok", "instagram", "youtube", "truthsocial",
               "hackernews", "digg", "pinterest", "xiaohongshu"):
        return "community"
    return None


def dedupe_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    by_key: dict[str, EvidenceItem] = {}
    for item in items:
        key = (item.url or item.id).lower()
        if not key:
            continue
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = item
        elif existing.published_at is None and item.published_at is not None:
            by_key[key] = item
    return list(by_key.values())


def evidence_score_raw(raw: dict[str, Any]) -> float:
    score = 0.0
    engagement = raw.get("engagement")
    for key in ("score", "engagement_score", "freshness", "local_rank_score", "local_relevance"):
        v = raw.get(key)
        if isinstance(v, (int, float)):
            score += float(v)
    if isinstance(engagement, dict):
        for key in ("score", "likes", "upvotes", "comments", "views", "rank_score", "postCount"):
            v = engagement.get(key)
            if isinstance(v, (int, float)):
                score += min(float(v), 1000.0) / 20.0
    return score or 1.0


STOPWORDS = frozenset(
    """a an and or but if then else of for to in on at by with from into over under
    is are was were be been being have has had do does did this that these those it its
    as so not no yes can will would should could may might must about which what when where
    who whom why how than such only also more most less least new now today yesterday
    just very really i you he she they them we us our your their there here""".split()
)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]{1,}")


def tokenize(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text) if w.lower() not in STOPWORDS and len(w) > 2]


def tfidf_vectors(items: list[EvidenceItem]) -> tuple[list[dict[str, float]], dict[str, float]]:
    docs = [tokenize(item.text) for item in items]
    df: Counter[str] = Counter()
    for doc in docs:
        for term in set(doc):
            df[term] += 1
    n = max(1, len(docs))
    idf = {term: math.log((1 + n) / (1 + count)) + 1.0 for term, count in df.items()}
    vectors: list[dict[str, float]] = []
    for doc in docs:
        tf = Counter(doc)
        if not tf:
            vectors.append({})
            continue
        max_tf = max(tf.values())
        vec = {term: (count / max_tf) * idf.get(term, 0.0) for term, count in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vectors.append({k: v / norm for k, v in vec.items()})
    return vectors, idf


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    return sum(weight * b.get(term, 0.0) for term, weight in a.items())


def cluster_items(items: list[EvidenceItem], vectors: list[dict[str, float]],
                   *, threshold: float = 0.32) -> list[list[int]]:
    clusters: list[dict[str, Any]] = []
    for idx, vec in enumerate(vectors):
        if not vec:
            clusters.append({"indices": [idx], "centroid": dict(vec), "size": 1})
            continue
        best_i = -1
        best_sim = 0.0
        for ci, cluster in enumerate(clusters):
            sim = cosine(vec, cluster["centroid"])
            if sim > best_sim:
                best_sim = sim
                best_i = ci
        if best_i >= 0 and best_sim >= threshold:
            cluster = clusters[best_i]
            cluster["indices"].append(idx)
            new_size = cluster["size"] + 1
            centroid = cluster["centroid"]
            for term, weight in vec.items():
                centroid[term] = centroid.get(term, 0.0) + (weight - centroid.get(term, 0.0)) / new_size
            cluster["size"] = new_size
        else:
            clusters.append({"indices": [idx], "centroid": dict(vec), "size": 1})
    return [c["indices"] for c in clusters]


def label_cluster(cluster_items_list: list[EvidenceItem], vectors: list[dict[str, float]],
                   indices: list[int], *, topic: str) -> tuple[str, list[str]]:
    agg: Counter[str] = Counter()
    for i in indices:
        for term, weight in vectors[i].items():
            agg[term] += weight
    topic_set = set(tokenize(topic))
    ranked = [t for t, _ in agg.most_common(20) if t not in topic_set]
    top_terms = ranked[:3] if ranked else [t for t, _ in agg.most_common(3)]
    title = ", ".join(top_terms) if top_terms else cluster_items_list[0].title[:80]
    return title, top_terms


def make_time_buckets(start: date, end: date) -> list[Window]:
    days = max(1, (end - start).days + 1)
    if days <= 14:
        bucket_days = 1
    elif days <= 45:
        bucket_days = 3
    elif days <= 180:
        bucket_days = 7
    else:
        bucket_days = 14
    buckets: list[Window] = []
    cur = start
    while cur <= end:
        bend = min(end, cur + timedelta(days=bucket_days - 1))
        label = cur.isoformat() if bucket_days == 1 else f"{cur.isoformat()}..{bend.isoformat()}"
        buckets.append(Window(label, cur, bend))
        cur = bend + timedelta(days=1)
    return buckets


def count_in_window(items: list[EvidenceItem], window: Window) -> int:
    return sum(1 for e in items if e.published_at and window.start <= e.published_at <= window.end)


def linear_slope(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(values) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, values))
    den = sum((x - mx) ** 2 for x in xs) or 1.0
    return num / den


def cluster_and_analyze(items: list[EvidenceItem], *, topic: str, buckets: list[Window],
                         compare_windows: list[Window]) -> list[TrendTheme]:
    if not items:
        return []
    vectors, _idf = tfidf_vectors(items)
    cluster_indices = cluster_items(items, vectors)
    themes: list[TrendTheme] = []
    for indices in cluster_indices:
        cluster_items_list = [items[i] for i in indices]
        title, terms = label_cluster(cluster_items_list, vectors, indices, topic=topic)
        themes.append(build_theme(title, terms, cluster_items_list, buckets, compare_windows))
    themes = [t for t in themes if t.current_count + t.baseline_count > 0 or len(t.evidence) > 0]
    themes.sort(key=lambda t: (direction_rank(t.direction), t.score), reverse=True)
    return themes


def build_theme(title: str, terms: list[str], cluster_items_list: list[EvidenceItem],
                 buckets: list[Window], compare_windows: list[Window]) -> TrendTheme:
    bucket_counts = {b.label: count_in_window(cluster_items_list, b) for b in buckets}
    values = [float(bucket_counts[b.label]) for b in buckets]
    slope = linear_slope(values)
    if len(compare_windows) >= 2:
        baseline_w, current_w = compare_windows[0], compare_windows[-1]
    else:
        single = compare_windows[0]
        mid = single.start + timedelta(days=single.days // 2)
        baseline_w = Window("baseline", single.start, mid - timedelta(days=1))
        current_w = Window("current", mid, single.end)
    baseline = count_in_window(cluster_items_list, baseline_w)
    current = count_in_window(cluster_items_list, current_w)
    baseline_rate = baseline / max(1, baseline_w.days)
    current_rate = current / max(1, current_w.days)
    momentum = (current_rate - baseline_rate) / max(0.05, baseline_rate)
    direction = classify_direction(baseline, current, slope, momentum)
    sources = sorted({e.source for e in cluster_items_list})
    bucket_mix = dict(Counter(e.bucket for e in cluster_items_list))
    diversity = len(sources)
    score = (sum(e.score for e in cluster_items_list) + diversity * 5
             + max(0.0, momentum) * 10 + max(0.0, slope) * 8)
    then_quote, now_quote = pick_then_now(cluster_items_list, baseline_w, current_w)
    top_evidence = sorted(cluster_items_list,
                          key=lambda e: (e.published_at or date.min, e.score),
                          reverse=True)[:6]
    return TrendTheme(
        title=title, direction=direction, slope=round(slope, 3),
        momentum=round(momentum, 3), current_count=current, baseline_count=baseline,
        source_diversity=diversity, bucket_mix=bucket_mix, score=round(score, 3),
        bucket_counts=bucket_counts, sources=sources, evidence=top_evidence,
        then_quote=then_quote, now_quote=now_quote, terms=terms,
    )


def classify_direction(baseline: int, current: int, slope: float, momentum: float) -> str:
    if baseline == 0 and current >= 3:
        return "emerging"
    if slope > 0 and momentum > 0.5 and current >= 3:
        return "rising"
    if slope < 0 and momentum < -0.4 and baseline >= 3:
        return "fading"
    return "stable"


def direction_rank(d: str) -> int:
    return {"emerging": 4, "rising": 3, "stable": 2, "fading": 1}.get(d, 0)


def pick_then_now(items: list[EvidenceItem], baseline_w: Window, current_w: Window
                   ) -> tuple[EvidenceItem | None, EvidenceItem | None]:
    base_items = [e for e in items if e.published_at and baseline_w.start <= e.published_at <= baseline_w.end]
    cur_items = [e for e in items if e.published_at and current_w.start <= e.published_at <= current_w.end]
    then = max(base_items, key=lambda e: e.score, default=None)
    now = max(cur_items, key=lambda e: e.score, default=None)
    return then, now


_ENTITY_RE = re.compile(r"\b([A-Z][A-Za-z0-9]{1,}(?:\s+[A-Z][A-Za-z0-9]{1,}){0,2})\b")


def extract_entities(text: str) -> list[str]:
    out: list[str] = []
    for match in _ENTITY_RE.findall(text or ""):
        cleaned = re.sub(r"\s+", " ", match.strip())
        if cleaned.lower() in STOPWORDS or len(cleaned) < 3:
            continue
        out.append(cleaned)
    return out


def compute_emerging_entities(items: list[EvidenceItem], compare_windows: list[Window]) -> list[dict[str, Any]]:
    if len(compare_windows) < 2:
        return []
    baseline_w, current_w = compare_windows[0], compare_windows[-1]
    base_counts: Counter[str] = Counter()
    cur_counts: Counter[str] = Counter()
    cur_examples: dict[str, EvidenceItem] = {}
    for e in items:
        if not e.published_at:
            continue
        ents = extract_entities(f"{e.title} {e.body}")
        if baseline_w.start <= e.published_at <= baseline_w.end:
            base_counts.update(ents)
        if current_w.start <= e.published_at <= current_w.end:
            cur_counts.update(ents)
            for ent in ents:
                cur_examples.setdefault(ent, e)
    out = []
    for ent, count in cur_counts.most_common():
        if count < 2 or base_counts[ent] > 0:
            continue
        ex = cur_examples.get(ent)
        out.append({"entity": ent, "current_count": count,
                    "example": serialize_evidence(ex) if ex else None})
        if len(out) >= 15:
            break
    return out


def compute_vocabulary_drift(items: list[EvidenceItem], compare_windows: list[Window]) -> list[dict[str, Any]]:
    if len(compare_windows) < 2:
        return []
    baseline_w, current_w = compare_windows[0], compare_windows[-1]

    def normalize(counter: Counter[str]) -> dict[str, float]:
        total = sum(counter.values()) or 1
        return {k: v / total for k, v in counter.items()}

    base_terms: Counter[str] = Counter()
    cur_terms: Counter[str] = Counter()
    cur_examples: dict[str, EvidenceItem] = {}
    for e in items:
        if not e.published_at:
            continue
        toks = tokenize(e.text)
        bigrams = [" ".join(toks[i:i + 2]) for i in range(len(toks) - 1)]
        all_terms = toks + bigrams
        if baseline_w.start <= e.published_at <= baseline_w.end:
            base_terms.update(all_terms)
        if current_w.start <= e.published_at <= current_w.end:
            cur_terms.update(all_terms)
            for term in all_terms:
                cur_examples.setdefault(term, e)
    base_norm = normalize(base_terms)
    cur_norm = normalize(cur_terms)
    drift: list[tuple[str, float, int, int]] = []
    for term, cur_freq in cur_norm.items():
        if cur_terms[term] < 3:
            continue
        base_freq = base_norm.get(term, 0.0)
        lift = math.log((cur_freq + 1e-6) / (base_freq + 1e-6))
        if lift <= 0.5:
            continue
        drift.append((term, lift, cur_terms[term], base_terms.get(term, 0)))
    drift.sort(key=lambda t: t[1], reverse=True)
    out = []
    for term, lift, cur_count, base_count in drift[:20]:
        ex = cur_examples.get(term)
        out.append({
            "term": term, "lift": round(lift, 3),
            "current_count": cur_count, "baseline_count": base_count,
            "example": serialize_evidence(ex) if ex else None,
        })
    return out


def compute_inflection_moments(items: list[EvidenceItem], buckets: list[Window]) -> list[InflectionMoment]:
    if len(buckets) < 3:
        return []
    counts = [count_in_window(items, b) for b in buckets]
    moments: list[InflectionMoment] = []
    for i in range(1, len(buckets)):
        prior = counts[i - 1]
        cur = counts[i]
        if cur < 3:
            continue
        lift = (cur - prior) / max(1.0, prior)
        if lift < 0.5:
            continue
        bucket_items = [
            e for e in items
            if e.published_at and buckets[i].start <= e.published_at <= buckets[i].end
        ]
        headline = max(bucket_items, key=lambda e: e.score, default=None)
        moments.append(InflectionMoment(
            bucket_label=buckets[i].label, bucket_start=buckets[i].start.isoformat(),
            bucket_end=buckets[i].end.isoformat(), prior_count=prior,
            bucket_count=cur, lift=round(lift, 3), headline=headline,
        ))
    moments.sort(key=lambda m: m.lift, reverse=True)
    return moments[:3]


_FORWARD_MARKERS = re.compile(
    r"\b(will|by 20\d{2}|expects?|predicts?|forecast(?:s|ed)?|roadmap|rfc|projection|likely to|set to|plans? to)\b",
    re.IGNORECASE,
)


def collect_forward_signals(items: list[EvidenceItem]) -> list[EvidenceItem]:
    out: list[EvidenceItem] = []
    for e in items:
        if e.bucket == "forecasts" or e.source.lower() == "polymarket":
            out.append(e)
            continue
        if _FORWARD_MARKERS.search(e.text):
            out.append(e)
    out.sort(key=lambda e: (e.published_at or date.min, e.score), reverse=True)
    return out


def serialize_window(w: Window) -> dict[str, str]:
    return {"label": w.label, "start": w.start.isoformat(), "end": w.end.isoformat()}


def serialize_evidence(item: EvidenceItem | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "id": item.id, "source": item.source, "bucket": item.bucket,
        "title": item.title, "url": item.url,
        "published_at": item.published_at.isoformat() if item.published_at else "",
        "score": round(item.score, 3), "snippet": item.body[:320],
    }


def serialize_theme(theme: TrendTheme) -> dict[str, Any]:
    return {
        "title": theme.title, "direction": theme.direction,
        "slope": theme.slope, "momentum": theme.momentum,
        "current_count": theme.current_count, "baseline_count": theme.baseline_count,
        "source_diversity": theme.source_diversity, "bucket_mix": theme.bucket_mix,
        "evidence_count": len(theme.evidence), "score": theme.score,
        "bucket_counts": theme.bucket_counts, "sources": theme.sources,
        "terms": theme.terms,
        "then": serialize_evidence(theme.then_quote),
        "now": serialize_evidence(theme.now_quote),
        "evidence": [serialize_evidence(e) for e in theme.evidence],
    }


def serialize_inflection(m: InflectionMoment) -> dict[str, Any]:
    return {
        "bucket_label": m.bucket_label, "start": m.bucket_start, "end": m.bucket_end,
        "prior_count": m.prior_count, "count": m.bucket_count,
        "lift": m.lift, "headline": serialize_evidence(m.headline),
    }


def coverage_notes(items: list[EvidenceItem], had_agent_web: bool) -> list[str]:
    notes: list[str] = []
    active_sources = sorted({e.source for e in items})
    active_buckets = sorted({e.bucket for e in items})
    if not items:
        notes.append("No evidence returned. Check last30days credentials or supply --agent-web-file.")
        return notes
    if len(active_sources) <= 2:
        notes.append("Only " + ", ".join(active_sources) + " returned evidence. Source diversity is low.")
    missing_buckets = [b for b in ("research", "implementations", "adoption", "criticism", "forecasts")
                       if b not in active_buckets]
    if missing_buckets:
        notes.append(
            "Evidence buckets missing: " + ", ".join(missing_buckets)
            + ". Have the host agent populate these via --agent-web-file."
        )
    if not had_agent_web:
        notes.append("No agent-web evidence file was provided. Trender is running on community signal only.")
    return notes


def render_markdown(payload: dict[str, Any], *, html_path: Path | None = None,
                     json_path: Path | None = None) -> str:
    lines: list[str] = []
    lines.append(f"trender v{payload['version']} - analyzed {payload['generated_at'][:10]}")
    lines.append("")
    lines.append(f"# Trend report: {payload['topic']}")
    lines.append("")
    w = payload["window"]
    lines.append(
        f"Window: {w['start']} to {w['end']} - retrieval lookback {payload['retrieval_days']}d - "
        f"{payload['source_count']} evidence items"
    )
    if payload["compare_windows"]:
        lines.append("Comparison: " + " vs ".join(c["label"] for c in payload["compare_windows"]))
    lines.append("")

    by_dir: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in payload["themes"]:
        by_dir[t["direction"]].append(t)

    if payload.get("inflection_moments"):
        lines.append("## Inflection moments")
        for m in payload["inflection_moments"]:
            head = m.get("headline") or {}
            lines.append(
                f"- **{m['start']} to {m['end']}** - {m['prior_count']} -> {m['count']} items "
                f"(+{int(m['lift'] * 100)}%)"
            )
            if head.get("url"):
                lines.append(f"  - {head['source']}: [{head['title']}]({head['url']})")
        lines.append("")

    for section, label in (("emerging", "Emerging"), ("rising", "Accelerating"), ("fading", "Fading")):
        themes_in = by_dir.get(section, [])
        if not themes_in:
            continue
        lines.append(f"## {label} themes")
        for theme in themes_in:
            lines.extend(render_theme_md(theme))
        lines.append("")

    if by_dir.get("stable"):
        lines.append("## Stable themes")
        for theme in by_dir["stable"][:5]:
            lines.extend(render_theme_md(theme, compact=True))
        lines.append("")

    if payload.get("emerging_entities"):
        lines.append("## New names in the conversation")
        for ent in payload["emerging_entities"][:10]:
            ex = ent.get("example") or {}
            tail = f" - example: [{ex.get('title', '')}]({ex.get('url', '')})" if ex.get("url") else ""
            lines.append(f"- **{ent['entity']}** ({ent['current_count']}x){tail}")
        lines.append("")

    if payload.get("vocabulary_drift"):
        lines.append("## Vocabulary drift")
        for term in payload["vocabulary_drift"][:12]:
            lines.append(
                f"- **{term['term']}** - lift {term['lift']} "
                f"(baseline {term['baseline_count']} -> current {term['current_count']})"
            )
        lines.append("")

    if payload.get("forward_signals"):
        lines.append("## Forward signals")
        for sig in payload["forward_signals"][:8]:
            if sig and sig.get("url"):
                lines.append(f"- {sig.get('published_at', '')} - {sig['source']}: [{sig['title']}]({sig['url']})")
        lines.append("")

    counts = payload.get("source_counts", {})
    if counts:
        lines.append("## Source coverage")
        lines.append(", ".join(f"{s}={c}" for s, c in sorted(counts.items(), key=lambda x: x[1], reverse=True)))
        lines.append("")
    bcounts = payload.get("bucket_counts", {})
    if bcounts:
        lines.append("Bucket mix: " + ", ".join(
            f"{s}={c}" for s, c in sorted(bcounts.items(), key=lambda x: x[1], reverse=True)
        ))
        lines.append("")

    for note in payload.get("coverage_notes", []):
        lines.append(f"> Coverage: {note}")
    if html_path:
        lines.append("")
        lines.append(f"HTML trend map: {html_path}")
    if json_path:
        lines.append(f"JSON data: {json_path}")
    return "\n".join(lines).rstrip() + "\n"


def render_theme_md(theme: dict[str, Any], *, compact: bool = False) -> list[str]:
    lines = [
        f"### {theme['title']} - {theme['direction']}",
        f"slope {theme['slope']} - momentum {theme['momentum']} - "
        f"baseline {theme['baseline_count']} -> current {theme['current_count']} - "
        f"sources {theme['source_diversity']}",
    ]
    if compact:
        return lines + [""]
    then = theme.get("then")
    now = theme.get("now")
    if then or now:
        lines.append("")
        lines.append("_Then -> Now_")
        if then:
            lines.append(f"- **{then['published_at']}** - {then['source']}: \"{then['title']}\"")
        if now:
            lines.append(f"- **{now['published_at']}** - {now['source']}: \"{now['title']}\"")
    if theme.get("evidence"):
        lines.append("")
        lines.append("_Evidence_")
        for ev in theme["evidence"][:3]:
            if ev and ev.get("url"):
                lines.append(f"- {ev.get('published_at', '')} - {ev['source']}: [{ev['title']}]({ev['url']})")
    lines.append("")
    return lines


def render_html(payload: dict[str, Any]) -> str:
    themes = payload.get("themes", [])
    by_dir: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in themes:
        by_dir[t.get("direction", "stable")].append(t)

    inflections_html = render_inflections_html(payload.get("inflection_moments", []))
    sections_html = ""
    for direction, label in (("emerging", "Emerging"), ("rising", "Accelerating"), ("fading", "Fading")):
        items = by_dir.get(direction, [])
        if not items:
            continue
        sections_html += f"<section class=\"theme-section\"><h2>{label} themes</h2>"
        sections_html += "".join(render_theme_card(t, payload["buckets"]) for t in items)
        sections_html += "</section>"

    stable = by_dir.get("stable", [])
    if stable:
        cards = "".join(render_theme_card(t, payload["buckets"]) for t in stable[:8])
        sections_html += (
            "<section class=\"theme-section\"><details><summary>"
            "<h2 style=\"display:inline\">Stable themes</h2></summary>"
            f"{cards}</details></section>"
        )

    entities_html = render_entities_html(payload.get("emerging_entities", []))
    vocab_html = render_vocab_html(payload.get("vocabulary_drift", []))
    forward_html = render_forward_html(payload.get("forward_signals", []))

    source_counts = payload.get("source_counts", {})
    bucket_counts = payload.get("bucket_counts", {})
    max_source = max([1, *source_counts.values()])
    source_bars = "".join(
        f"<div class=\"bar-row\"><span>{escape(s)}</span>"
        f"<div class=\"bar-track\"><div class=\"bar-fill\" "
        f"style=\"width:{(c / max_source) * 100:.1f}%\"></div></div>"
        f"<strong>{c}</strong></div>"
        for s, c in sorted(source_counts.items(), key=lambda x: x[1], reverse=True)
    )
    bucket_chips = "".join(
        f"<span class=\"chip bucket-{escape(b)}\">{escape(b)} {c}</span>"
        for b, c in sorted(bucket_counts.items(), key=lambda x: x[1], reverse=True)
    )
    notes_html = "".join(f"<li>{escape(n)}</li>" for n in payload.get("coverage_notes", []))

    comparison_html = ""
    if payload.get("compare_windows"):
        labels = " vs ".join(c["label"] for c in payload["compare_windows"])
        comparison_html = f"<div class=\"comparison\">Comparison: {escape(labels)}</div>"

    return TEMPLATE.format(
        topic=escape(payload["topic"]), version=escape(payload["version"]),
        generated_at=escape(payload["generated_at"]),
        window_start=escape(payload["window"]["start"]),
        window_end=escape(payload["window"]["end"]),
        retrieval_days=payload["retrieval_days"],
        source_count=payload["source_count"], n_themes=len(themes),
        n_entities=len(payload.get("emerging_entities", [])),
        n_vocab=len(payload.get("vocabulary_drift", [])),
        comparison_html=comparison_html,
        inflections_html=inflections_html,
        sections_html=sections_html or "<p class=\"muted\">No themes detected.</p>",
        entities_html=entities_html, vocab_html=vocab_html, forward_html=forward_html,
        source_bars=source_bars or "<p class=\"muted\">No sources returned evidence.</p>",
        bucket_chips=bucket_chips or "<span class=\"chip\">none</span>",
        notes_html=notes_html or "<li>No coverage warnings.</li>",
    )


def render_theme_card(theme: dict[str, Any], buckets: list[dict[str, str]]) -> str:
    bucket_counts = theme.get("bucket_counts", {})
    values = [int(bucket_counts.get(b["label"], 0)) for b in buckets]
    sparkline = svg_sparkline(values)
    then = theme.get("then")
    now = theme.get("now")
    quote_pair = ""
    if then or now:
        quote_pair = "<div class=\"quote-pair\">"
        if then:
            quote_pair += (
                f"<div class=\"quote then\"><div class=\"qlabel\">Then - "
                f"{escape(then.get('published_at', ''))}</div>"
                f"<a href=\"{escape(then.get('url', '#'))}\" target=\"_blank\" rel=\"noreferrer\">"
                f"{escape(then.get('title', ''))}</a>"
                f"<p>{escape((then.get('snippet') or '')[:240])}</p></div>"
            )
        if now:
            quote_pair += (
                f"<div class=\"quote now\"><div class=\"qlabel\">Now - "
                f"{escape(now.get('published_at', ''))}</div>"
                f"<a href=\"{escape(now.get('url', '#'))}\" target=\"_blank\" rel=\"noreferrer\">"
                f"{escape(now.get('title', ''))}</a>"
                f"<p>{escape((now.get('snippet') or '')[:240])}</p></div>"
            )
        quote_pair += "</div>"

    evidence_items = "".join(
        f"<li><div class=\"evidence-meta\">{escape(e.get('published_at') or 'unknown')} - "
        f"{escape(e.get('source', ''))}</div>"
        f"<a href=\"{escape(e.get('url') or '#')}\" target=\"_blank\" rel=\"noreferrer\">"
        f"{escape(e.get('title', ''))}</a>"
        f"<p>{escape((e.get('snippet') or '')[:220])}</p></li>"
        for e in (theme.get("evidence") or []) if e
    )
    direction = theme.get("direction", "stable")
    return (
        f"<article class=\"theme-card {escape(direction)}\">"
        f"<div class=\"theme-topline\">"
        f"<span class=\"chip {escape(direction)}\">{escape(direction)}</span>"
        f"<span class=\"muted\">slope {theme.get('slope', 0)} - "
        f"momentum {theme.get('momentum', 0)} - sources {theme.get('source_diversity', 0)}</span>"
        f"</div>"
        f"<h3>{escape(theme.get('title', 'Untitled'))}</h3>"
        f"<div class=\"movement\">"
        f"<div><strong>{theme.get('baseline_count', 0)}</strong><span>baseline</span></div>"
        f"<div class=\"arrow\">&#8594;</div>"
        f"<div><strong>{theme.get('current_count', 0)}</strong><span>current</span></div>"
        f"</div>"
        f"<div class=\"sparkline\">{sparkline}</div>"
        f"{quote_pair}"
        f"<details><summary>Evidence ({len(theme.get('evidence', []))})</summary>"
        f"<ul class=\"evidence-list\">{evidence_items}</ul>"
        f"</details>"
        f"</article>"
    )


def svg_sparkline(values: list[int]) -> str:
    if not values:
        return ""
    w, h = 320, 60
    n = len(values)
    max_v = max(values) or 1
    step = w / max(1, n - 1) if n > 1 else w
    points = []
    bars = []
    for i, v in enumerate(values):
        x = i * step
        y = h - (v / max_v) * (h - 8) - 4
        points.append(f"{x:.1f},{y:.1f}")
        bh = max(2, (v / max_v) * (h - 8))
        bars.append(
            f"<rect x=\"{x - 2:.1f}\" y=\"{h - bh:.1f}\" width=\"4\" height=\"{bh:.1f}\" "
            f"fill=\"#67e8f9\" opacity=\"0.4\"/>"
        )
    polyline = (
        f"<polyline fill=\"none\" stroke=\"#22d3ee\" stroke-width=\"2\" "
        f"points=\"{' '.join(points)}\"/>"
    )
    return f"<svg viewBox=\"0 0 {w} {h}\" width=\"100%\" height=\"{h}\">{''.join(bars)}{polyline}</svg>"


def render_inflections_html(moments: list[dict[str, Any]]) -> str:
    if not moments:
        return ""
    cards = ""
    for m in moments:
        head = m.get("headline") or {}
        link = (
            f"<a href=\"{escape(head.get('url', '#'))}\" target=\"_blank\" rel=\"noreferrer\">"
            f"{escape(head.get('title', ''))}</a>"
        ) if head.get("url") else ""
        cards += (
            f"<article class=\"inflection\">"
            f"<div class=\"date\">{escape(m['start'])} &#8594; {escape(m['end'])}</div>"
            f"<div class=\"lift\">{m['prior_count']} &#8594; {m['count']} (+{int(m['lift'] * 100)}%)</div>"
            f"<div class=\"headline\">{link}</div>"
            f"</article>"
        )
    return (
        f"<section class=\"inflections\"><h2>Inflection moments</h2>"
        f"<div class=\"inflection-row\">{cards}</div></section>"
    )


def render_entities_html(entities: list[dict[str, Any]]) -> str:
    if not entities:
        return ""
    rows = ""
    for ent in entities[:15]:
        ex = ent.get("example") or {}
        link = (
            f"<a href=\"{escape(ex.get('url', '#'))}\" target=\"_blank\" rel=\"noreferrer\">"
            f"{escape(ex.get('title', ''))}</a>"
        ) if ex.get("url") else ""
        rows += f"<li><strong>{escape(ent['entity'])}</strong> - {ent['current_count']}x - {link}</li>"
    return (
        f"<section class=\"panel\"><h2>New names in the conversation</h2>"
        f"<ul class=\"entities\">{rows}</ul></section>"
    )


def render_vocab_html(vocab: list[dict[str, Any]]) -> str:
    if not vocab:
        return ""
    rows = ""
    for term in vocab[:20]:
        ex = term.get("example") or {}
        ex_link = (
            f"<a href=\"{escape(ex.get('url', '#'))}\" target=\"_blank\" rel=\"noreferrer\">"
            f"{escape((ex.get('title') or '')[:80])}</a>"
        ) if ex.get("url") else ""
        rows += (
            f"<tr><td><strong>{escape(term['term'])}</strong></td>"
            f"<td>{term['lift']}</td>"
            f"<td>{term['baseline_count']} &#8594; {term['current_count']}</td>"
            f"<td>{ex_link}</td></tr>"
        )
    return (
        f"<section class=\"panel\"><h2>Vocabulary drift</h2>"
        f"<table class=\"vocab\"><thead><tr><th>term</th><th>lift</th>"
        f"<th>base &#8594; cur</th><th>example</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></section>"
    )


def render_forward_html(signals: list[dict[str, Any]]) -> str:
    if not signals:
        return ""
    rows = ""
    for sig in signals[:10]:
        if not sig:
            continue
        rows += (
            f"<li><div class=\"evidence-meta\">{escape(sig.get('published_at', 'unknown'))} - "
            f"{escape(sig.get('source', ''))}</div>"
            f"<a href=\"{escape(sig.get('url', '#'))}\" target=\"_blank\" rel=\"noreferrer\">"
            f"{escape(sig.get('title', ''))}</a>"
            f"<p>{escape((sig.get('snippet') or '')[:240])}</p></li>"
        )
    return f"<section class=\"panel\"><h2>Forward signals</h2><ul class=\"evidence-list\">{rows}</ul></section>"


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trender - {topic}</title>
<style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: radial-gradient(circle at top left, #172554, #08111f 45%, #050816); color: #eef2ff; font-family: Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 1180px; margin: 0 auto; padding: 32px; }}
.hero {{ border: 1px solid rgba(148,163,184,.25); background: linear-gradient(135deg, rgba(59,130,246,.22), rgba(15,23,42,.86)); border-radius: 28px; padding: 30px; }}
.eyebrow {{ color: #67e8f9; font-size: 12px; font-weight: 800; letter-spacing: .22em; text-transform: uppercase; }}
h1 {{ margin: 8px 0 10px; font-size: clamp(34px, 6vw, 56px); line-height: 1; }}
.subtitle, .muted {{ color: #a8b3cf; }}
.comparison {{ margin-top: 6px; color: #93c5fd; font-weight: 600; }}
.metrics {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin-top: 24px; }}
.metric {{ background: rgba(15,23,42,.72); border: 1px solid rgba(148,163,184,.2); border-radius: 18px; padding: 16px; }}
.metric strong {{ display: block; font-size: 28px; }}
.metric span {{ color: #a8b3cf; font-size: 13px; }}
.layout {{ display: grid; grid-template-columns: 1fr 320px; gap: 22px; margin-top: 22px; align-items: start; }}
.theme-section, .panel {{ background: rgba(15,23,42,.65); border: 1px solid rgba(148,163,184,.18); border-radius: 22px; padding: 18px; margin-bottom: 18px; }}
.theme-section h2, .panel h2 {{ margin: 0 0 14px; font-size: 18px; }}
.theme-card {{ background: rgba(15,23,42,.78); border: 1px solid rgba(148,163,184,.18); border-radius: 16px; padding: 16px; margin: 10px 0; }}
.theme-card h3 {{ margin: 10px 0 8px; font-size: 20px; }}
.theme-topline {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
.chip {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 4px 10px; font-size: 11px; font-weight: 800; background: #334155; color: #e2e8f0; text-transform: uppercase; letter-spacing: .04em; }}
.chip.emerging {{ background: #14532d; color: #bbf7d0; }}
.chip.rising {{ background: #1e3a8a; color: #bfdbfe; }}
.chip.fading {{ background: #7f1d1d; color: #fecaca; }}
.chip.stable {{ background: #3f3f46; color: #e4e4e7; }}
.movement {{ display: inline-grid; grid-template-columns: auto 24px auto; align-items: center; gap: 10px; margin: 6px 0 10px; }}
.movement div:not(.arrow) {{ background: rgba(255,255,255,.06); border-radius: 14px; padding: 8px 12px; }}
.movement strong {{ display: block; font-size: 20px; }}
.movement span {{ color: #a8b3cf; font-size: 11px; }}
.arrow {{ color: #67e8f9; font-weight: 900; }}
.sparkline {{ background: rgba(2,6,23,.55); border: 1px solid rgba(148,163,184,.14); border-radius: 12px; padding: 8px; margin: 8px 0; }}
.quote-pair {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 12px 0; }}
.quote {{ background: rgba(255,255,255,.04); border-radius: 12px; padding: 10px 12px; border-left: 3px solid #475569; }}
.quote.then {{ border-left-color: #94a3b8; }}
.quote.now {{ border-left-color: #67e8f9; }}
.quote .qlabel {{ color: #94a3b8; font-size: 11px; text-transform: uppercase; letter-spacing: .1em; margin-bottom: 4px; }}
.quote a {{ color: #93c5fd; font-weight: 600; }}
.quote p {{ color: #cbd5e1; font-size: 13px; margin: 6px 0 0; }}
details {{ margin-top: 8px; }}
summary {{ cursor: pointer; color: #93c5fd; font-weight: 600; }}
.evidence-list {{ padding-left: 18px; }}
.evidence-list li {{ margin: 10px 0; }}
.evidence-meta {{ color: #94a3b8; font-size: 12px; }}
.evidence-list a {{ color: #93c5fd; }}
.evidence-list p {{ color: #a8b3cf; margin: 4px 0 0; font-size: 13px; }}
.inflections .inflection-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }}
.inflection {{ background: rgba(15,23,42,.78); border: 1px solid rgba(148,163,184,.18); border-radius: 14px; padding: 14px; }}
.inflection .date {{ color: #67e8f9; font-weight: 700; font-size: 13px; }}
.inflection .lift {{ font-size: 22px; font-weight: 800; margin: 6px 0; }}
.inflection .headline a {{ color: #cbd5e1; }}
.bar-row {{ display: grid; grid-template-columns: 90px 1fr 36px; gap: 10px; align-items: center; margin: 8px 0; font-size: 12px; }}
.bar-track {{ height: 8px; border-radius: 999px; background: rgba(148,163,184,.18); overflow: hidden; }}
.bar-fill {{ height: 100%; background: linear-gradient(90deg, #22d3ee, #a78bfa); }}
.chips {{ display: flex; flex-wrap: wrap; gap: 8px; }}
.entities {{ padding-left: 18px; }}
.entities li {{ margin: 6px 0; color: #cbd5e1; }}
.vocab {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
.vocab th, .vocab td {{ text-align: left; padding: 6px 4px; border-bottom: 1px solid rgba(148,163,184,.15); }}
.notes {{ color: #fef3c7; padding-left: 18px; }}
@media (max-width: 900px) {{ .layout {{ grid-template-columns: 1fr; }} .metrics {{ grid-template-columns: 1fr 1fr; }} .quote-pair {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<main>
<section class="hero">
<div class="eyebrow">Trender v{version}</div>
<h1>{topic}</h1>
<p class="subtitle">Generated {generated_at} - window {window_start} to {window_end} - retrieval lookback {retrieval_days}d</p>
{comparison_html}
<div class="metrics">
<div class="metric"><strong>{source_count}</strong><span>evidence items</span></div>
<div class="metric"><strong>{n_themes}</strong><span>themes</span></div>
<div class="metric"><strong>{n_entities}</strong><span>emerging entities</span></div>
<div class="metric"><strong>{n_vocab}</strong><span>vocab shifts</span></div>
</div>
</section>
{inflections_html}
<section class="layout">
<div>
{sections_html}
{entities_html}
{vocab_html}
{forward_html}
</div>
<aside>
<section class="panel">
<h2>Source coverage</h2>
{source_bars}
</section>
<section class="panel">
<h2>Bucket mix</h2>
<div class="chips">{bucket_chips}</div>
</section>
<section class="panel">
<h2>Coverage notes</h2>
<ul class="notes">{notes_html}</ul>
</section>
</aside>
</section>
</main>
</body>
</html>"""


def mock_evidence(topic: str, primary: Window, compare: list[Window]) -> list[EvidenceItem]:
    if compare and len(compare) >= 2:
        baseline_w, current_w = compare[0], compare[-1]
    else:
        mid = primary.start + timedelta(days=primary.days // 2)
        baseline_w = Window("baseline", primary.start, mid)
        current_w = Window("current", mid + timedelta(days=1), primary.end)

    samples = [
        (-30, "research", "arxiv", f"{topic}: a survey of recent benchmarks",
         f"Comprehensive survey of {topic} evaluation methods."),
        (-15, "implementations", "github", f"open-{topic.lower().replace(' ', '-')}: reference implementation",
         f"Open source release demonstrating {topic} in practice."),
        (-5, "community", "reddit", f"r/programming: thoughts on {topic}",
         f"Discussion thread about {topic} workflows and adoption."),
        (3, "research", "arxiv", f"new {topic} benchmark released",
         f"Updated benchmark suite for {topic} with stronger evaluation criteria."),
        (5, "implementations", "github", f"vendor X ships {topic} integration",
         f"Major vendor announces production {topic} integration."),
        (7, "adoption", "agent-web", f"enterprise adoption of {topic} accelerates",
         f"Survey: 40% of teams now use {topic} in production workflows."),
        (10, "criticism", "reddit", f"the limits of {topic} in production",
         f"Practitioners discuss reliability and cost issues with {topic}."),
        (12, "forecasts", "polymarket", f"market: will {topic} reach 1M users by 2027?",
         f"Polymarket odds shifted on {topic} adoption forecast."),
        (14, "community", "hackernews", f"Show HN: {topic} debugger",
         f"New debugging tool for {topic} workflows. Lots of engagement."),
        (16, "community", "x", f"viral thread on {topic} pricing",
         f"Pricing changes around {topic} sparked broad discussion."),
        (18, "implementations", "github", f"v2.0 of popular {topic} framework",
         f"Framework v2.0 release with API redesign for {topic}."),
        (20, "research", "perplexity", f"comparing {topic} approaches",
         f"Side-by-side comparison of three {topic} architectures."),
    ]
    items: list[EvidenceItem] = []
    for i, (offset, bucket, source, title, body) in enumerate(samples):
        if offset < 0:
            published = baseline_w.end + timedelta(days=offset)
        else:
            published = current_w.start + timedelta(days=offset)
        if published > primary.end:
            published = primary.end
        if published < primary.start:
            published = primary.start
        items.append(EvidenceItem(
            id=f"mock-{i}", source=source, title=title, body=body,
            url=f"https://example.com/{slugify(topic)}/{i}",
            published_at=published, score=20.0 + i, bucket=bucket, raw={},
        ))
    return items


def resolve_last30days_dir(configured: str | None, skill_dir: Path) -> Path:
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend([
        skill_dir / "vendor" / "last30days",
        skill_dir.parent / "last30days",
        Path.home() / ".claude" / "skills" / "last30days",
        Path.home() / ".codex" / "skills" / "last30days",
        Path.home() / ".agents" / "skills" / "last30days",
        Path.home() / ".openclaw" / "skills" / "last30days",
    ])
    for candidate in candidates:
        if (candidate / "scripts" / "last30days.py").exists():
            return candidate
    raise SystemExit("Could not find last30days. Set LAST30DAYS_SKILL_DIR or rebuild Trender.")


def resolve_python_for_last30days() -> str:
    configured = os.getenv("TRENDER_LAST30DAYS_PYTHON")
    candidates = [configured] if configured else []
    candidates.extend([
        sys.executable,
        shutil.which("python3.13"),
        shutil.which("python3.12"),
        shutil.which("python3"),
        shutil.which("python"),
    ])
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if python_version_at_least(candidate, 3, 12):
            return candidate
    raise SystemExit("last30days requires Python 3.12+. Set TRENDER_LAST30DAYS_PYTHON.")


def python_version_at_least(executable: str, major: int, minor: int) -> bool:
    try:
        proc = subprocess.run(
            [executable, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
        )
    except OSError:
        return False
    if proc.returncode != 0:
        return False
    try:
        parts = proc.stdout.strip().split(".", 1)
        return (int(parts[0]), int(parts[1])) >= (major, minor)
    except (ValueError, IndexError):
        return False


def parse_json_from_mixed_output(output: str) -> dict[str, Any]:
    start = output.find("{")
    end = output.rfind("}")
    if start < 0 or end < start:
        raise SystemExit("last30days did not return JSON output")
    return json.loads(output[start:end + 1])


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise SystemExit(f"Invalid date {value!r}; expected YYYY-MM-DD") from exc


def parse_compare(raw: str | None, end: date) -> list[Window]:
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise SystemExit("--compare must be two day counts, e.g. --compare=30,180")
    short_days, long_days = sorted([int(parts[0]), int(parts[1])])
    if short_days <= 0:
        raise SystemExit("--compare day counts must be positive")
    baseline_start = end - timedelta(days=long_days - 1)
    baseline_end = end - timedelta(days=short_days)
    current_start = end - timedelta(days=short_days - 1)
    return [
        Window(f"prior {long_days - short_days}d baseline", baseline_start, baseline_end),
        Window(f"last {short_days}d current", current_start, end),
    ]


def first_text(raw: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def parse_item_date(raw: dict[str, Any]) -> date | None:
    candidates = [raw.get("published_at"), raw.get("created_at"), raw.get("date"),
                  raw.get("posted_at"), raw.get("timestamp")]
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


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return slug or "trend"


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def write_stdout(text: str) -> None:
    output = text.replace("\n", os.linesep)
    if not text.endswith("\n"):
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


if __name__ == "__main__":
    raise SystemExit(main())
