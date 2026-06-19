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

VERSION = "0.7.0"


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

    narrative = load_agent_narrative(args.narrative_file)

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
        "forward_signals": [serialize_forward_signal(e) for e in forward[:12]],
        "coverage_notes": coverage_notes(in_scope, bool(args.agent_web_file)),
        "last30days_dir": str(last30days_dir),
    }

    bluf = narrative["bluf"]
    bluf_authored = narrative["authored"]
    if not bluf:
        bluf = build_bluf_fallback(payload)
    payload["bluf"] = bluf
    payload["bluf_authored"] = bluf_authored
    payload["forward_outlook"] = narrative["forward_outlook"]

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
    parser.add_argument("--narrative-file", default=os.getenv("TRENDER_NARRATIVE_FILE"),
                        help="JSON/Markdown authored by the host agent: bottom-line bullets (bluf) + forward_outlook.")
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
    as so not no yes can will would would not would have would be would also could should
    may might must about which what when where who whom why how than such only also
    more most less least new now today yesterday just very really i you he she they them
    we us our your their there here many much each all any both either neither some other
    others another like get got make made go going gone goes come came use uses using used
    show shown showing show hn ask hn launch hn ai""".split()
)

# Terms that are too generic to use as cluster *labels* even if they survive the
# document-level stopword filter. Helps avoid labels like "home, broke, going".
LABEL_BLACKLIST = frozenset(
    """home broke going broken best version run runs running ran ship ships shipped
    work works working worked time times today week weeks year years month months
    day days hour hours minute minutes second seconds first second third fourth
    big bigger biggest small smaller smallest large larger largest little long short
    top bottom right wrong full empty open closed coming due
    high low fast slow good bad better worse great cool nice fine simple easy hard
    really actually just maybe probably perhaps certainly definitely possibly
    quot amp lt gt apos nbsp http https www com org net io app blog post comment
    thing things stuff something nothing anything everything anyone someone everyone
    quite very pretty rather somewhat fairly highly""".split()
)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]{1,}")


def tokenize(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text) if w.lower() not in STOPWORDS and len(w) > 2]


def title_bigrams(title: str) -> list[str]:
    toks = tokenize(title)
    return [f"{a} {b}" for a, b in zip(toks, toks[1:]) if a not in LABEL_BLACKLIST and b not in LABEL_BLACKLIST]


def tfidf_vectors(items: list[EvidenceItem]) -> tuple[list[dict[str, float]], dict[str, float]]:
    """Document vectors weighted toward titles (titles are denser signal than bodies)."""
    docs: list[list[str]] = []
    for item in items:
        title_tokens = tokenize(item.title)
        body_tokens = tokenize(item.body)
        # boost titles 3x in TF
        docs.append(title_tokens * 3 + body_tokens)
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
                   *, threshold: float = 0.22) -> list[list[int]]:
    """Greedy cosine clustering with a small bucket-affinity boost."""
    clusters: list[dict[str, Any]] = []
    for idx, vec in enumerate(vectors):
        item = items[idx]
        if not vec:
            clusters.append({"indices": [idx], "centroid": dict(vec), "buckets": Counter([item.bucket]), "size": 1})
            continue
        best_i = -1
        best_sim = 0.0
        for ci, cluster in enumerate(clusters):
            sim = cosine(vec, cluster["centroid"])
            # bucket boost: items in same dominant bucket are slightly more similar
            dominant_bucket = cluster["buckets"].most_common(1)[0][0]
            if dominant_bucket == item.bucket:
                sim += 0.04
            if sim > best_sim:
                best_sim = sim
                best_i = ci
        if best_i >= 0 and best_sim >= threshold:
            cluster = clusters[best_i]
            cluster["indices"].append(idx)
            cluster["buckets"][item.bucket] += 1
            new_size = cluster["size"] + 1
            centroid = cluster["centroid"]
            for term, weight in vec.items():
                centroid[term] = centroid.get(term, 0.0) + (weight - centroid.get(term, 0.0)) / new_size
            cluster["size"] = new_size
        else:
            clusters.append({"indices": [idx], "centroid": dict(vec), "buckets": Counter([item.bucket]), "size": 1})
    return [c["indices"] for c in clusters]


def label_cluster(cluster_items_list: list[EvidenceItem], vectors: list[dict[str, float]],
                   indices: list[int], *, topic: str, idf: dict[str, float]) -> tuple[str, list[str]]:
    """Generate a clean human-readable label from titles only.

    Strategy: aggregate IDF-weighted unigrams + bigrams from titles across the
    cluster, drop topic words and label-blacklisted generic terms, prefer
    bigrams when they meet a frequency floor, fall back to top unigrams.
    """
    topic_set = set(tokenize(topic))
    titles = [it.title for it in cluster_items_list]

    unigram_score: Counter[str] = Counter()
    for title in titles:
        for tok in tokenize(title):
            if tok in topic_set or tok in LABEL_BLACKLIST or len(tok) < 3:
                continue
            unigram_score[tok] += idf.get(tok, 1.0)

    bigram_score: Counter[str] = Counter()
    bigram_freq: Counter[str] = Counter()
    for title in titles:
        for bg in title_bigrams(title):
            a, b = bg.split(" ", 1)
            if a in topic_set and b in topic_set:
                continue
            if a in LABEL_BLACKLIST or b in LABEL_BLACKLIST:
                continue
            bigram_score[bg] += idf.get(a, 1.0) + idf.get(b, 1.0)
            bigram_freq[bg] += 1

    # Prefer bigrams that actually repeat in the cluster (real phrases, not noise).
    repeat_bigrams = [bg for bg, n in bigram_freq.items() if n >= 2]
    if repeat_bigrams:
        repeat_bigrams.sort(key=lambda bg: bigram_score[bg], reverse=True)
        chosen = repeat_bigrams[:2]
        # Add one supporting unigram not already covered
        covered = set(" ".join(chosen).split())
        extra = [t for t, _ in unigram_score.most_common(10) if t not in covered]
        if extra:
            chosen.append(extra[0])
        terms = chosen
    else:
        # No repeating phrases — use top distinctive unigrams
        terms = [t for t, _ in unigram_score.most_common(3)]
        # If still empty (very short cluster), fall back to first title's distinctive words
        if not terms:
            terms = [t for t, _ in unigram_score.most_common(3)] or [
                cluster_items_list[0].title[:60].rstrip()
            ]

    title = humanize_label(terms)
    return title, terms


def humanize_label(terms: list[str]) -> str:
    if not terms:
        return "Untitled cluster"
    parts = []
    for term in terms:
        words = term.split()
        # Title-case acronyms-aware: keep ALL-CAPS acronyms uppercase, capitalize others
        cleaned = " ".join(w.upper() if len(w) <= 4 and w.isalpha() and w.lower() in {"ai", "api", "llm", "mcp", "rfc", "ide", "cli", "sdk", "gpu", "tts", "ocr"} else w.capitalize() for w in words)
        parts.append(cleaned)
    return " · ".join(parts)



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
    vectors, idf = tfidf_vectors(items)
    cluster_indices = cluster_items(items, vectors)

    # Drop singleton clusters from theme display — they're noise and inflate
    # overview counts. They still flow through entity / vocab / inflection
    # analyses elsewhere because those operate over `items` directly.
    cluster_indices = [c for c in cluster_indices if len(c) >= 2]

    themes: list[TrendTheme] = []
    for indices in cluster_indices:
        cluster_items_list = [items[i] for i in indices]
        title, terms = label_cluster(cluster_items_list, vectors, indices, topic=topic, idf=idf)
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
    if baseline == 0 and current >= 2:
        return "emerging"
    if baseline >= 2 and current == 0:
        return "fading"
    if slope > 0 and momentum > 0.5 and current >= 2:
        return "rising"
    if slope < 0 and momentum < -0.4 and baseline >= 2:
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
_ROADMAP_MARKERS = re.compile(r"\b(roadmap|rfc|milestone|planned|plans? to|ga\b|general availability|release plan)\b", re.IGNORECASE)
_FORECAST_MARKERS = re.compile(r"\b(forecast(?:s|ed)?|projection|projected|estimat\w*|expects?|analyst|outlook)\b", re.IGNORECASE)
_HORIZON_YEAR = re.compile(r"\b(20[2-9]\d)\b")


def classify_forward_signal(item: EvidenceItem) -> str:
    """Bucket a forward signal into a scannable category."""
    if item.source.lower() == "polymarket":
        return "betting market"
    text = item.text
    if _ROADMAP_MARKERS.search(text):
        return "roadmap"
    if _FORECAST_MARKERS.search(text):
        return "forecast"
    return "prediction"


def forward_horizon(item: EvidenceItem) -> str:
    """Extract the furthest future year referenced, if any (e.g. 'by 2027')."""
    years = _HORIZON_YEAR.findall(item.text)
    if not years:
        return ""
    current_year = date.today().year
    future = [y for y in years if int(y) >= current_year]
    pool = future or years
    return max(pool, key=int)


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


def serialize_forward_signal(item: EvidenceItem | None) -> dict[str, Any] | None:
    base = serialize_evidence(item)
    if base is None or item is None:
        return None
    base["kind"] = classify_forward_signal(item)
    base["horizon"] = forward_horizon(item)
    return base


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


def load_agent_narrative(path: str | None) -> dict[str, Any]:
    """Load host-agent-authored narrative (BLUF bullets + forward outlook).

    The host coding agent authors the bottom-line synthesis the same way it
    authors the rest of the analysis, and hands it to Trender via a JSON file:

        {"bluf": ["bullet", {"text": "bullet", "url": "https://..."}],
         "forward_outlook": "one-line what's-coming takeaway"}

    A plain Markdown bullet list (``bluf_md``) or a ``.md`` file is also accepted.
    Returns a normalized dict: {"bluf": [{"text","url"}], "forward_outlook": str,
    "authored": bool}.
    """
    empty = {"bluf": [], "forward_outlook": "", "authored": False}
    if not path:
        return empty
    p = Path(path).expanduser()
    if not p.exists():
        raise SystemExit(f"--narrative-file does not exist: {p}")
    raw_text = p.read_text(encoding="utf-8").strip()
    if not raw_text:
        return empty

    bluf_items: list[Any] = []
    forward_outlook = ""
    if p.suffix.lower() in (".md", ".markdown") or not raw_text.startswith(("{", "[")):
        bluf_items = _bullets_from_markdown(raw_text)
    else:
        data = json.loads(raw_text)
        if isinstance(data, list):
            bluf_items = data
        elif isinstance(data, dict):
            if isinstance(data.get("bluf"), list):
                bluf_items = data["bluf"]
            elif isinstance(data.get("bluf_md"), str):
                bluf_items = _bullets_from_markdown(data["bluf_md"])
            forward_outlook = str(data.get("forward_outlook") or "").strip()
        else:
            raise SystemExit("--narrative-file JSON must be an object or array.")

    bluf = normalize_bluf(bluf_items)
    return {
        "bluf": bluf,
        "forward_outlook": forward_outlook,
        "authored": bool(bluf or forward_outlook),
    }


def _bullets_from_markdown(text: str) -> list[str]:
    bullets: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        stripped = re.sub(r"^[-*+]\s+", "", stripped)
        stripped = re.sub(r"^\d+[.)]\s+", "", stripped)
        if stripped:
            bullets.append(stripped)
    return bullets


def normalize_bluf(items: list[Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for it in items:
        if isinstance(it, str):
            text = it.strip()
            if text:
                out.append({"text": text, "url": ""})
        elif isinstance(it, dict):
            text = str(it.get("text") or it.get("bullet") or "").strip()
            if text:
                out.append({"text": text, "url": str(it.get("url") or "").strip()})
    return out


def build_bluf_fallback(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Derive a minimal, clearly-labeled BLUF when the agent supplied none.

    These are deterministic data-derived highlights — never presented as the
    agent's synthesis. The HTML/Markdown flag them as auto-generated.
    """
    bullets: list[dict[str, str]] = []
    by_dir: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in payload.get("themes", []):
        by_dir[t.get("direction", "stable")].append(t)

    for direction, verb in (("emerging", "emerging"), ("rising", "accelerating")):
        ranked = sorted(by_dir.get(direction, []), key=lambda t: t.get("score", 0), reverse=True)
        if ranked:
            top = ranked[0]
            bullets.append({
                "text": f"Strongest {verb} theme: \u201c{top['title']}\u201d "
                        f"({top.get('current_count', 0)} items, momentum {top.get('momentum', 0)}).",
                "url": (top.get("now") or {}).get("url", "") if top.get("now") else "",
            })

    inflections = payload.get("inflection_moments", [])
    if inflections:
        m = max(inflections, key=lambda x: x.get("lift", 0))
        head = m.get("headline") or {}
        bullets.append({
            "text": f"Biggest volume jump: {m['start']}\u2192{m['end']} "
                    f"(+{int(m.get('lift', 0) * 100)}%)"
                    + (f" \u2014 {head.get('title', '')}" if head.get("title") else "") + ".",
            "url": head.get("url", ""),
        })

    entities = payload.get("emerging_entities", [])
    if entities:
        names = ", ".join(e["entity"] for e in entities[:3])
        bullets.append({"text": f"New names entering the conversation: {names}.", "url": ""})

    forward = [f for f in payload.get("forward_signals", []) if f]
    if forward:
        top = forward[0]
        bullets.append({
            "text": f"Forward signal to watch ({top.get('kind', 'prediction')}): {top.get('title', '')}.",
            "url": top.get("url", ""),
        })

    return bullets[:5]


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

    bluf = payload.get("bluf") or []
    if bluf:
        if payload.get("bluf_authored"):
            lines.append("## Bottom line up front")
        else:
            lines.append("## Bottom line up front (auto-generated)")
        for b in bluf:
            text = b.get("text", "") if isinstance(b, dict) else str(b)
            url = b.get("url", "") if isinstance(b, dict) else ""
            tail = f" ([source]({url}))" if url else ""
            lines.append(f"- {text}{tail}")
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
        if payload.get("forward_outlook"):
            lines.append(f"_{payload['forward_outlook']}_")
            lines.append("")
        for sig in payload["forward_signals"][:8]:
            if sig and sig.get("url"):
                kind = sig.get("kind", "prediction")
                horizon = f" (by {sig['horizon']})" if sig.get("horizon") else ""
                lines.append(
                    f"- **[{kind}]**{horizon} {sig.get('published_at', '')} - "
                    f"{sig['source']}: [{sig['title']}]({sig['url']})"
                )
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
    """Render a modern, interactive trend map.

    The page is fully self-contained (CSS + small vanilla-JS bundle inline).
    The complete trend payload is embedded in a JSON island; the bundle
    reads it and renders direction filtering, search, sparklines, and
    smooth scroll navigation on top of it.
    """
    themes = payload.get("themes", [])
    by_dir: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in themes:
        by_dir[t.get("direction", "stable")].append(t)

    direction_counts = {d: len(by_dir.get(d, [])) for d in ("emerging", "rising", "stable", "fading")}
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")

    return MODERN_TEMPLATE.format(
        topic=escape(payload["topic"]),
        version=escape(payload["version"]),
        generated_at=escape(payload["generated_at"]),
        generated_short=escape(payload["generated_at"][:10]),
        window_start=escape(payload["window"]["start"]),
        window_end=escape(payload["window"]["end"]),
        retrieval_days=payload["retrieval_days"],
        source_count=payload["source_count"],
        n_themes=len(themes),
        n_entities=len(payload.get("emerging_entities", [])),
        n_inflections=len(payload.get("inflection_moments", [])),
        n_vocab=len(payload.get("vocabulary_drift", [])),
        n_forward=len(payload.get("forward_signals", [])),
        n_emerging=direction_counts["emerging"],
        n_rising=direction_counts["rising"],
        n_stable=direction_counts["stable"],
        n_fading=direction_counts["fading"],
        comparison_label=escape(
            " vs ".join(c["label"] for c in payload.get("compare_windows", []))
            or "single window"
        ),
        payload_json=payload_json,
    )


MODERN_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trender · {topic}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {{
  --bg: #06080f;
  --bg-elev: #0d1220;
  --bg-card: rgba(20, 26, 44, 0.7);
  --border: rgba(148, 163, 184, 0.12);
  --border-strong: rgba(148, 163, 184, 0.24);
  --text: #e8ecf5;
  --text-dim: #94a3b8;
  --text-muted: #64748b;
  --accent: #67e8f9;
  --accent-2: #a78bfa;
  --emerging: #34d399;
  --rising: #60a5fa;
  --fading: #f87171;
  --stable: #94a3b8;
  --radius: 14px;
  --radius-lg: 22px;
  --shadow: 0 1px 0 rgba(255,255,255,0.03) inset, 0 8px 24px rgba(0,0,0,0.32);
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{
  background: var(--bg);
  background-image:
    radial-gradient(1200px 600px at 10% -10%, rgba(103, 232, 249, 0.08), transparent 60%),
    radial-gradient(1000px 600px at 90% -20%, rgba(167, 139, 250, 0.07), transparent 60%);
  color: var(--text);
  font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  font-feature-settings: "cv11", "ss01";
  -webkit-font-smoothing: antialiased;
  line-height: 1.5;
  min-height: 100vh;
}}
.mono {{ font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace; }}
a {{ color: var(--accent); text-decoration: none; transition: color .15s ease; }}
a:hover {{ color: #a5f3fc; }}

/* layout */
.container {{ max-width: 1240px; margin: 0 auto; padding: 28px 28px 80px; }}
.topbar {{
  position: sticky; top: 0; z-index: 50;
  backdrop-filter: blur(14px) saturate(140%);
  -webkit-backdrop-filter: blur(14px) saturate(140%);
  background: rgba(6, 8, 15, 0.72);
  border-bottom: 1px solid var(--border);
}}
.topbar-inner {{
  max-width: 1240px; margin: 0 auto; padding: 12px 28px;
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
}}
.brand {{ display: flex; align-items: center; gap: 10px; font-weight: 700; letter-spacing: -0.01em; }}
.brand-dot {{
  width: 10px; height: 10px; border-radius: 50%;
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
  box-shadow: 0 0 16px rgba(103, 232, 249, 0.6);
}}
.brand-version {{ color: var(--text-muted); font-weight: 500; font-size: 12px; }}
.nav {{ display: flex; gap: 4px; flex-wrap: wrap; margin-left: auto; }}
.nav a {{
  padding: 6px 12px; border-radius: 999px; color: var(--text-dim);
  font-size: 13px; font-weight: 500;
  transition: background .15s ease, color .15s ease;
}}
.nav a:hover {{ background: rgba(255,255,255,.04); color: var(--text); }}

/* hero */
.hero {{ padding: 56px 0 36px; }}
.eyebrow {{
  display: inline-flex; align-items: center; gap: 8px;
  color: var(--text-dim); font-size: 12px; font-weight: 600;
  letter-spacing: 0.18em; text-transform: uppercase;
  padding: 6px 12px; border: 1px solid var(--border-strong); border-radius: 999px;
  background: rgba(148, 163, 184, 0.04);
}}
.eyebrow .pulse {{
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 0 rgba(103, 232, 249, 0.7);
  animation: pulse 2.4s ease-out infinite;
}}
@keyframes pulse {{
  0% {{ box-shadow: 0 0 0 0 rgba(103, 232, 249, 0.5); }}
  70% {{ box-shadow: 0 0 0 12px rgba(103, 232, 249, 0); }}
  100% {{ box-shadow: 0 0 0 0 rgba(103, 232, 249, 0); }}
}}
h1 {{
  font-size: clamp(40px, 7vw, 76px);
  line-height: 1.0; letter-spacing: -0.035em;
  font-weight: 800; margin: 18px 0 16px;
  background: linear-gradient(180deg, #ffffff 0%, #cbd5e1 100%);
  -webkit-background-clip: text; background-clip: text; color: transparent;
}}
.hero-sub {{ color: var(--text-dim); font-size: 16px; max-width: 760px; }}
.hero-meta {{
  display: flex; flex-wrap: wrap; gap: 8px; margin-top: 18px; align-items: center;
}}
.tag {{
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 12px; padding: 5px 10px; border-radius: 8px;
  background: rgba(148,163,184,.08); color: var(--text-dim);
  border: 1px solid var(--border);
}}
.tag strong {{ color: var(--text); font-weight: 600; }}

/* metric grid */
.metrics {{
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-top: 36px;
}}
.metric {{
  position: relative; padding: 18px 18px 16px; border-radius: var(--radius);
  background: var(--bg-card); border: 1px solid var(--border);
  overflow: hidden;
}}
.metric::before {{
  content: ''; position: absolute; inset: 0;
  background: radial-gradient(400px 80px at 100% 0%, rgba(103,232,249,.06), transparent 60%);
  pointer-events: none;
}}
.metric .label {{
  font-size: 11px; text-transform: uppercase; letter-spacing: .12em; color: var(--text-muted);
}}
.metric .value {{
  font-size: 36px; font-weight: 700; letter-spacing: -0.02em; margin-top: 4px;
  font-variant-numeric: tabular-nums;
}}
.metric .delta {{ font-size: 12px; color: var(--text-dim); margin-top: 4px; }}

/* section */
section.block {{ margin-top: 56px; scroll-margin-top: 80px; }}
.block-head {{
  display: flex; align-items: baseline; justify-content: space-between;
  margin-bottom: 18px; gap: 16px; flex-wrap: wrap;
}}
.block-head h2 {{
  font-size: 24px; letter-spacing: -0.02em; margin: 0; font-weight: 700;
}}
.block-head .hint {{ color: var(--text-muted); font-size: 13px; }}

/* inflections strip */
.inflection-grid {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px;
}}
.inflection {{
  padding: 18px; border-radius: var(--radius); border: 1px solid var(--border);
  background: linear-gradient(160deg, rgba(103,232,249,.06), rgba(167,139,250,.04) 60%, transparent);
  transition: transform .2s ease, border-color .2s ease;
}}
.inflection:hover {{ transform: translateY(-2px); border-color: var(--border-strong); }}
.inflection .when {{ color: var(--accent); font-size: 12px; font-weight: 600; letter-spacing: .04em; }}
.inflection .lift {{
  font-size: 32px; font-weight: 700; margin: 8px 0 6px; letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}}
.inflection .lift small {{ font-size: 13px; color: var(--text-dim); font-weight: 500; }}
.inflection .head a {{ color: var(--text); font-size: 14px; font-weight: 500; }}
.inflection .head a:hover {{ color: var(--accent); }}

/* filter chips */
.filterbar {{
  display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 16px;
}}
.chip {{
  display: inline-flex; align-items: center; gap: 8px;
  padding: 8px 14px; border-radius: 999px;
  background: var(--bg-card); border: 1px solid var(--border);
  color: var(--text-dim); font-size: 13px; font-weight: 500;
  cursor: pointer; user-select: none;
  transition: all .15s ease;
}}
.chip:hover {{ border-color: var(--border-strong); color: var(--text); }}
.chip[data-active="true"] {{ background: rgba(103,232,249,.1); border-color: rgba(103,232,249,.5); color: var(--text); }}
.chip .swatch {{ width: 8px; height: 8px; border-radius: 50%; }}
.chip[data-direction="emerging"] .swatch {{ background: var(--emerging); }}
.chip[data-direction="rising"] .swatch {{ background: var(--rising); }}
.chip[data-direction="stable"] .swatch {{ background: var(--stable); }}
.chip[data-direction="fading"] .swatch {{ background: var(--fading); }}
.chip .count {{ color: var(--text-muted); font-variant-numeric: tabular-nums; }}
.search {{
  flex: 1 1 220px; max-width: 360px; min-width: 200px; margin-left: auto;
  background: var(--bg-card); border: 1px solid var(--border); border-radius: 999px;
  padding: 8px 14px; color: var(--text); font-size: 13px;
  transition: border-color .15s ease, background .15s ease;
}}
.search:focus {{ outline: none; border-color: var(--accent); background: rgba(103,232,249,.04); }}

/* theme cards */
.theme-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 14px; }}
.theme-card {{
  position: relative;
  padding: 18px; border-radius: var(--radius);
  background: var(--bg-card); border: 1px solid var(--border);
  transition: transform .2s ease, border-color .2s ease, box-shadow .2s ease;
  display: flex; flex-direction: column; gap: 12px;
}}
.theme-card:hover {{
  transform: translateY(-2px); border-color: var(--border-strong);
  box-shadow: var(--shadow);
}}
.theme-card.hidden {{ display: none; }}
.theme-card .topline {{
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
}}
.dir-badge {{
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
  padding: 4px 10px; border-radius: 6px;
}}
.dir-badge[data-direction="emerging"] {{ background: rgba(52,211,153,.12); color: var(--emerging); }}
.dir-badge[data-direction="rising"]   {{ background: rgba(96,165,250,.12); color: var(--rising); }}
.dir-badge[data-direction="fading"]   {{ background: rgba(248,113,113,.12); color: var(--fading); }}
.dir-badge[data-direction="stable"]   {{ background: rgba(148,163,184,.12); color: var(--stable); }}
.dir-badge .swatch {{ width: 6px; height: 6px; border-radius: 50%; background: currentColor; }}
.theme-card h3 {{
  font-size: 18px; font-weight: 600; margin: 0;
  letter-spacing: -0.015em; line-height: 1.3;
}}
.kpis {{
  display: flex; gap: 18px; align-items: center; color: var(--text-dim); font-size: 12px;
}}
.kpis .kpi {{ display: flex; flex-direction: column; gap: 2px; }}
.kpis .kpi b {{ color: var(--text); font-size: 16px; font-weight: 600; font-variant-numeric: tabular-nums; }}
.movement {{
  display: inline-flex; align-items: center; gap: 12px;
  font-variant-numeric: tabular-nums;
}}
.movement .from, .movement .to {{
  background: rgba(255,255,255,.04); padding: 6px 12px; border-radius: 8px;
  font-weight: 600; font-size: 18px;
}}
.movement .arrow {{ color: var(--accent); font-weight: 700; }}
.spark {{
  position: relative; height: 64px; border-radius: 10px;
  background: rgba(2,6,23,.6); border: 1px solid var(--border);
  overflow: hidden;
}}
.quote-pair {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
.quote {{
  padding: 10px 12px; border-radius: 10px; background: rgba(255,255,255,.025);
  border-left: 2px solid var(--text-muted);
  font-size: 13px;
}}
.quote.then {{ border-left-color: var(--text-dim); }}
.quote.now  {{ border-left-color: var(--accent); }}
.quote .qlabel {{
  color: var(--text-muted); font-size: 10px; text-transform: uppercase;
  letter-spacing: .12em; margin-bottom: 4px;
}}
.quote a {{ color: var(--text); font-weight: 500; }}
.quote a:hover {{ color: var(--accent); }}
.quote p {{ color: var(--text-dim); font-size: 12px; margin: 4px 0 0; line-height: 1.45; }}
details.evidence {{ margin-top: 4px; }}
details.evidence summary {{
  cursor: pointer; color: var(--text-dim); font-size: 12px; padding: 6px 0;
  list-style: none;
}}
details.evidence summary::-webkit-details-marker {{ display: none; }}
details.evidence summary::after {{ content: ' ›'; transition: transform .2s ease; display: inline-block; }}
details.evidence[open] summary::after {{ transform: rotate(90deg); }}
details.evidence ul {{ list-style: none; padding: 0; margin: 8px 0 0; }}
details.evidence li {{
  padding: 8px 0; border-top: 1px solid var(--border); font-size: 13px;
}}
details.evidence li .meta {{ color: var(--text-muted); font-size: 11px; }}
details.evidence li a {{ color: var(--text); font-weight: 500; display: block; margin: 2px 0; }}
details.evidence li a:hover {{ color: var(--accent); }}
details.evidence li p {{ color: var(--text-dim); font-size: 12px; margin: 2px 0 0; }}

/* entity / vocab */
.tag-grid {{ display: flex; flex-wrap: wrap; gap: 8px; }}
.entity {{
  display: inline-flex; align-items: center; gap: 8px;
  padding: 6px 12px; border-radius: 8px;
  background: rgba(167,139,250,.08); border: 1px solid rgba(167,139,250,.18);
  color: var(--text); font-size: 13px; font-weight: 500;
  transition: all .15s ease;
}}
.entity:hover {{ background: rgba(167,139,250,.14); transform: translateY(-1px); }}
.entity .count {{ color: var(--text-dim); font-size: 11px; font-variant-numeric: tabular-nums; }}

table.vocab {{
  width: 100%; border-collapse: collapse; font-size: 13px;
  background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius);
  overflow: hidden;
}}
table.vocab thead {{
  background: rgba(255,255,255,.02);
}}
table.vocab th {{
  text-align: left; padding: 10px 14px; font-weight: 600; color: var(--text-dim);
  font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
}}
table.vocab td {{ padding: 10px 14px; border-top: 1px solid var(--border); }}
table.vocab td:first-child {{ font-weight: 600; }}
table.vocab .lift-bar {{
  display: inline-block; height: 4px; border-radius: 2px;
  background: linear-gradient(90deg, var(--accent), var(--accent-2));
  vertical-align: middle; margin-right: 8px;
}}

/* aside / coverage */
.layout {{ display: grid; grid-template-columns: 1fr 320px; gap: 24px; align-items: start; margin-top: 24px; }}
@media (max-width: 980px) {{ .layout {{ grid-template-columns: 1fr; }} }}
.aside-card {{
  padding: 18px; border-radius: var(--radius);
  background: var(--bg-card); border: 1px solid var(--border);
  margin-bottom: 14px;
}}
.aside-card h3 {{
  margin: 0 0 12px; font-size: 12px; text-transform: uppercase;
  letter-spacing: .12em; color: var(--text-dim); font-weight: 600;
}}
.bar-row {{
  display: grid; grid-template-columns: 100px 1fr 30px;
  align-items: center; gap: 10px; margin: 8px 0;
  font-size: 12px;
}}
.bar-row .label {{ color: var(--text-dim); }}
.bar-track {{
  height: 6px; border-radius: 999px; background: rgba(148,163,184,.12); overflow: hidden;
}}
.bar-fill {{
  height: 100%; border-radius: inherit;
  background: linear-gradient(90deg, var(--accent), var(--accent-2));
  transition: width .8s cubic-bezier(.2,.8,.2,1);
}}
.bar-row strong {{ text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }}
.notes {{ list-style: none; padding: 0; margin: 0; }}
.notes li {{
  padding: 10px 12px; border-radius: 10px; margin-bottom: 8px;
  background: rgba(251,191,36,.05); border: 1px solid rgba(251,191,36,.18);
  color: #fde68a; font-size: 12px; line-height: 1.5;
}}

/* responsive */
@media (max-width: 720px) {{
  .metrics {{ grid-template-columns: 1fr 1fr; }}
  .quote-pair {{ grid-template-columns: 1fr; }}
  .nav {{ display: none; }}
  .hero {{ padding: 32px 0 24px; }}
  .forward-grid {{ grid-template-columns: 1fr; }}
}}

/* bottom line up front */
.bluf-block {{ margin-top: 40px; }}
.bluf {{
  position: relative;
  border: 1px solid var(--border-strong);
  border-left: 3px solid var(--accent);
  border-radius: var(--radius);
  background:
    linear-gradient(180deg, rgba(103,232,249,.06), rgba(103,232,249,0)) ,
    var(--bg-card);
  padding: 22px 24px 18px;
  box-shadow: var(--shadow);
}}
.bluf ul {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 12px; }}
.bluf li {{
  position: relative; padding-left: 26px;
  font-size: 16px; line-height: 1.55; color: var(--text);
}}
.bluf li::before {{
  content: ''; position: absolute; left: 4px; top: 9px;
  width: 8px; height: 8px; border-radius: 50%;
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
  box-shadow: 0 0 10px rgba(103,232,249,.5);
}}
.bluf li a {{ font-weight: 500; }}
.bluf-src {{ font-size: 12px; margin-left: 6px; }}
.bluf-auto {{
  display: inline-flex; align-items: center; gap: 6px;
  margin-top: 14px; font-size: 11px; letter-spacing: .04em;
  color: var(--text-muted); text-transform: uppercase;
}}

/* forward signals */
.forward-outlook {{
  margin: 0 0 14px; padding: 12px 16px;
  border-radius: 10px; font-size: 15px; line-height: 1.5;
  color: var(--text); background: rgba(96,165,250,.08);
  border: 1px solid rgba(96,165,250,.22);
}}
.forward-summary {{
  display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px;
  font-size: 12px; color: var(--text-dim);
}}
.forward-summary .fwd-stat {{
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px; border-radius: 999px;
  background: rgba(148,163,184,.07); border: 1px solid var(--border);
}}
.forward-summary .fwd-stat strong {{ color: var(--text); font-variant-numeric: tabular-nums; }}
.forward-grid {{
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px;
}}
.fwd-card {{
  display: flex; flex-direction: column; gap: 8px;
  padding: 16px; border-radius: var(--radius);
  background: var(--bg-card); border: 1px solid var(--border);
  border-top: 2px solid var(--border-strong);
  animation: fadein .4s ease both;
}}
.fwd-top {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
.fwd-badge {{
  font-size: 11px; font-weight: 600; letter-spacing: .03em;
  padding: 3px 9px; border-radius: 999px; text-transform: capitalize;
  border: 1px solid currentColor;
}}
.fwd-badge.k-prediction {{ color: var(--rising); }}
.fwd-badge.k-forecast {{ color: var(--accent); }}
.fwd-badge.k-roadmap {{ color: var(--accent-2); }}
.fwd-badge.k-betting {{ color: var(--emerging); }}
.fwd-card.k-prediction {{ border-top-color: var(--rising); }}
.fwd-card.k-forecast {{ border-top-color: var(--accent); }}
.fwd-card.k-roadmap {{ border-top-color: var(--accent-2); }}
.fwd-card.k-betting {{ border-top-color: var(--emerging); }}
.fwd-horizon {{
  font-size: 11px; font-weight: 600; color: var(--text-dim);
  padding: 3px 8px; border-radius: 6px;
  background: rgba(148,163,184,.1); border: 1px solid var(--border);
  font-family: 'JetBrains Mono', ui-monospace, monospace;
}}
.fwd-meta {{ font-size: 11px; color: var(--text-muted); margin-left: auto; }}
.fwd-title {{ font-size: 14px; font-weight: 600; line-height: 1.4; }}
.fwd-snippet {{ font-size: 12px; color: var(--text-dim); margin: 0; line-height: 1.5; }}

/* utility */
.empty {{ color: var(--text-muted); font-style: italic; padding: 12px 0; }}

/* fade-in stagger */
@keyframes fadein {{
  from {{ opacity: 0; transform: translateY(8px); }}
  to   {{ opacity: 1; transform: translateY(0); }}
}}
.theme-card, .inflection, .metric, .entity {{
  animation: fadein .4s ease both;
}}
</style>
</head>
<body>

<header class="topbar">
  <div class="topbar-inner">
    <div class="brand">
      <span class="brand-dot"></span>
      Trender
      <span class="brand-version mono">v{version}</span>
    </div>
    <nav class="nav">
      <a href="#bluf">Bottom line</a>
      <a href="#inflections">Inflections</a>
      <a href="#themes">Themes</a>
      <a href="#entities">Entities</a>
      <a href="#vocab">Vocabulary</a>
      <a href="#forward">Forward</a>
      <a href="#coverage">Coverage</a>
    </nav>
  </div>
</header>

<main class="container">

  <section class="hero">
    <span class="eyebrow"><span class="pulse"></span> Trend report · {generated_short}</span>
    <h1>{topic}</h1>
    <p class="hero-sub">How this topic moved across <strong>{retrieval_days} days</strong> of evidence — comparing <span class="mono">{comparison_label}</span>.</p>
    <div class="hero-meta">
      <span class="tag"><strong>{window_start}</strong> → <strong>{window_end}</strong></span>
      <span class="tag">window</span>
      <span class="tag"><strong>{source_count}</strong> evidence items</span>
    </div>

    <div class="metrics">
      <div class="metric">
        <div class="label">Themes</div>
        <div class="value" data-counter="{n_themes}">{n_themes}</div>
        <div class="delta">{n_emerging} emerging · {n_rising} rising · {n_stable} stable · {n_fading} fading</div>
      </div>
      <div class="metric">
        <div class="label">Inflections</div>
        <div class="value" data-counter="{n_inflections}">{n_inflections}</div>
        <div class="delta">moments of acceleration</div>
      </div>
      <div class="metric">
        <div class="label">New names</div>
        <div class="value" data-counter="{n_entities}">{n_entities}</div>
        <div class="delta">entities new to current window</div>
      </div>
      <div class="metric">
        <div class="label">Vocabulary shifts</div>
        <div class="value" data-counter="{n_vocab}">{n_vocab}</div>
        <div class="delta">terms with significant lift</div>
      </div>
    </div>
  </section>

  <section id="bluf" class="block bluf-block">
    <div class="block-head">
      <h2>Bottom line up front</h2>
      <span class="hint" id="bluf-hint">the few things worth knowing before you scroll</span>
    </div>
    <div id="bluf-card" class="bluf"></div>
  </section>

  <section id="inflections" class="block">
    <div class="block-head">
      <h2>Inflection moments</h2>
      <span class="hint">weeks where volume jumped vs the prior week</span>
    </div>
    <div id="inflection-grid" class="inflection-grid"></div>
  </section>

  <section id="themes" class="block">
    <div class="block-head">
      <h2>Themes</h2>
      <span class="hint">click a chip to filter · search to narrow further</span>
    </div>
    <div class="filterbar" id="filterbar"></div>
    <div id="theme-grid" class="theme-grid"></div>
  </section>

  <div class="layout">
    <div>

      <section id="entities" class="block">
        <div class="block-head">
          <h2>New names in the conversation</h2>
          <span class="hint">capitalized n-grams new to the current window</span>
        </div>
        <div id="entity-grid" class="tag-grid"></div>
      </section>

      <section id="vocab" class="block">
        <div class="block-head">
          <h2>Vocabulary drift</h2>
          <span class="hint">terms with biggest baseline → current frequency lift</span>
        </div>
        <div id="vocab-table"></div>
      </section>

      <section id="forward" class="block">
        <div class="block-head">
          <h2>Forward signals</h2>
          <span class="hint">predictions, roadmaps, forecasts, betting markets</span>
        </div>
        <p id="forward-outlook" class="forward-outlook" hidden></p>
        <div id="forward-summary" class="forward-summary"></div>
        <div id="forward-list" class="forward-grid"></div>
      </section>

    </div>

    <aside id="coverage">
      <section class="block aside" style="margin-top:0">
        <div class="aside-card">
          <h3>Source coverage</h3>
          <div id="source-bars"></div>
        </div>
        <div class="aside-card">
          <h3>Bucket mix</h3>
          <div id="bucket-chips" class="tag-grid"></div>
        </div>
        <div class="aside-card">
          <h3>Coverage notes</h3>
          <ul id="coverage-notes" class="notes"></ul>
        </div>
      </section>
    </aside>
  </div>

</main>

<script id="trender-data" type="application/json">{payload_json}</script>
<script>
(function() {{
  const dataEl = document.getElementById('trender-data');
  const data = JSON.parse(dataEl.textContent);
  const buckets = data.buckets || [];
  const themes = data.themes || [];

  const escapeHtml = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  const fmt = (n) => Number.isFinite(n) ? n.toLocaleString() : '0';

  // bottom line up front
  (function renderBluf() {{
    const host = document.getElementById('bluf-card');
    if (!host) return;
    const bluf = (data.bluf || []).filter(b => b && b.text);
    const hint = document.getElementById('bluf-hint');
    if (!bluf.length) {{
      host.innerHTML = '<p class="empty">No bottom-line summary supplied. Have the host agent author one via --narrative-file.</p>';
      return;
    }}
    const items = bluf.map(b => {{
      const src = b.url
        ? ` <a class="bluf-src" href="${{escapeHtml(b.url)}}" target="_blank" rel="noreferrer">source ›</a>`
        : '';
      return `<li>${{escapeHtml(b.text)}}${{src}}</li>`;
    }}).join('');
    const auto = data.bluf_authored
      ? ''
      : '<div class="bluf-auto">⚙ auto-generated from signals — agent did not supply a bottom line</div>';
    host.innerHTML = `<ul>${{items}}</ul>${{auto}}`;
    if (hint && !data.bluf_authored) hint.textContent = 'auto-generated highlights · agent BLUF not provided';
  }})();

  // sparkline as SVG area chart
  function sparkline(values) {{
    if (!values || !values.length) return '';
    const w = 320, h = 64, pad = 4;
    const n = values.length;
    const max = Math.max(1, ...values);
    const step = n > 1 ? (w - pad * 2) / (n - 1) : 0;
    const points = values.map((v, i) => {{
      const x = pad + i * step;
      const y = h - pad - (v / max) * (h - pad * 2);
      return [x, y];
    }});
    const linePath = points.map((p, i) => (i === 0 ? 'M' : 'L') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
    const areaPath = linePath + ' L ' + points[n-1][0].toFixed(1) + ' ' + (h - pad) + ' L ' + points[0][0].toFixed(1) + ' ' + (h - pad) + ' Z';
    const dots = points.map((p, i) =>
      `<circle cx="${{p[0].toFixed(1)}}" cy="${{p[1].toFixed(1)}}" r="2" fill="#67e8f9" opacity="${{values[i] > 0 ? 1 : 0.3}}"/>`
    ).join('');
    return `<svg viewBox="0 0 ${{w}} ${{h}}" preserveAspectRatio="none" width="100%" height="${{h}}">
      <defs>
        <linearGradient id="sg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#67e8f9" stop-opacity="0.35"/>
          <stop offset="100%" stop-color="#67e8f9" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <path d="${{areaPath}}" fill="url(#sg)" stroke="none"/>
      <path d="${{linePath}}" fill="none" stroke="#67e8f9" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
      ${{dots}}
    </svg>`;
  }}

  // counter animation
  document.querySelectorAll('[data-counter]').forEach(el => {{
    const target = parseInt(el.getAttribute('data-counter'), 10) || 0;
    if (target <= 1) {{ el.textContent = target; return; }}
    const dur = 600;
    const start = performance.now();
    function tick(now) {{
      const t = Math.min(1, (now - start) / dur);
      const eased = 1 - Math.pow(1 - t, 3);
      el.textContent = Math.round(target * eased);
      if (t < 1) requestAnimationFrame(tick);
    }}
    requestAnimationFrame(tick);
  }});

  // inflection cards
  const inflectionGrid = document.getElementById('inflection-grid');
  const moments = data.inflection_moments || [];
  if (!moments.length) {{
    inflectionGrid.innerHTML = '<p class="empty">No inflection moments detected — volume is steady across the window.</p>';
  }} else {{
    inflectionGrid.innerHTML = moments.map(m => {{
      const head = m.headline || {{}};
      const headLink = head.url
        ? `<a href="${{escapeHtml(head.url)}}" target="_blank" rel="noreferrer">${{escapeHtml(head.title || '')}}</a>`
        : '<span class="empty">no headline</span>';
      const pct = Math.round((m.lift || 0) * 100);
      return `<article class="inflection">
        <div class="when">${{escapeHtml(m.start)}} → ${{escapeHtml(m.end)}}</div>
        <div class="lift">+${{pct}}% <small>${{m.prior_count}} → ${{m.count}}</small></div>
        <div class="head">${{headLink}}</div>
      </article>`;
    }}).join('');
  }}

  // direction filter chips
  const directions = ['emerging', 'rising', 'stable', 'fading'];
  const counts = directions.reduce((acc, d) => {{
    acc[d] = themes.filter(t => t.direction === d).length;
    return acc;
  }}, {{}});
  const filterbar = document.getElementById('filterbar');
  let activeDirs = new Set(directions.filter(d => counts[d] > 0));
  filterbar.innerHTML = directions.map(d => `
    <button class="chip" data-direction="${{d}}" data-active="true">
      <span class="swatch"></span>
      <span>${{d}}</span>
      <span class="count">${{counts[d]}}</span>
    </button>
  `).join('') + `<input class="search" id="search" placeholder="search themes & evidence…" />`;

  filterbar.querySelectorAll('.chip').forEach(chip => {{
    chip.addEventListener('click', () => {{
      const d = chip.getAttribute('data-direction');
      if (activeDirs.has(d)) {{ activeDirs.delete(d); chip.dataset.active = 'false'; }}
      else {{ activeDirs.add(d); chip.dataset.active = 'true'; }}
      applyFilters();
    }});
  }});
  document.getElementById('search').addEventListener('input', applyFilters);

  // theme cards
  const themeGrid = document.getElementById('theme-grid');
  if (!themes.length) {{
    themeGrid.innerHTML = '<p class="empty">No themes detected. Try a broader window or supply --agent-web-file.</p>';
  }} else {{
    themeGrid.innerHTML = themes.map((t, idx) => {{
      const values = buckets.map(b => (t.bucket_counts || {{}})[b.label] || 0);
      const then = t.then;
      const now = t.now;
      const quotePair = (then || now) ? `<div class="quote-pair">
        ${{ then ? `<div class="quote then">
          <div class="qlabel">Then · ${{escapeHtml(then.published_at || '')}}</div>
          <a href="${{escapeHtml(then.url || '#')}}" target="_blank" rel="noreferrer">${{escapeHtml(then.title || '')}}</a>
          <p>${{escapeHtml((then.snippet || '').slice(0, 220))}}</p>
        </div>` : '<div></div>' }}
        ${{ now ? `<div class="quote now">
          <div class="qlabel">Now · ${{escapeHtml(now.published_at || '')}}</div>
          <a href="${{escapeHtml(now.url || '#')}}" target="_blank" rel="noreferrer">${{escapeHtml(now.title || '')}}</a>
          <p>${{escapeHtml((now.snippet || '').slice(0, 220))}}</p>
        </div>` : '<div></div>' }}
      </div>` : '';
      const evidence = (t.evidence || []).filter(Boolean);
      const evidenceList = evidence.map(e => `<li>
        <div class="meta">${{escapeHtml(e.published_at || 'unknown')}} · ${{escapeHtml(e.source || '')}} · ${{escapeHtml(e.bucket || '')}}</div>
        <a href="${{escapeHtml(e.url || '#')}}" target="_blank" rel="noreferrer">${{escapeHtml(e.title || '')}}</a>
        <p>${{escapeHtml((e.snippet || '').slice(0, 200))}}</p>
      </li>`).join('');
      return `<article class="theme-card" data-direction="${{escapeHtml(t.direction)}}" data-search="${{escapeHtml(((t.title || '') + ' ' + evidence.map(e => (e.title || '') + ' ' + (e.snippet || '')).join(' ')).toLowerCase())}}" style="animation-delay:${{Math.min(idx, 12) * 30}}ms">
        <div class="topline">
          <span class="dir-badge" data-direction="${{escapeHtml(t.direction)}}"><span class="swatch"></span>${{escapeHtml(t.direction)}}</span>
          <span style="color:var(--text-muted);font-size:12px" class="mono">slope ${{t.slope}} · momentum ${{t.momentum}}</span>
        </div>
        <h3>${{escapeHtml(t.title || 'Untitled')}}</h3>
        <div class="kpis">
          <div class="movement">
            <span class="from">${{t.baseline_count || 0}}</span>
            <span class="arrow">→</span>
            <span class="to">${{t.current_count || 0}}</span>
          </div>
          <div class="kpi"><span>sources</span><b>${{t.source_diversity || 0}}</b></div>
          <div class="kpi"><span>evidence</span><b>${{t.evidence_count || 0}}</b></div>
        </div>
        <div class="spark">${{sparkline(values)}}</div>
        ${{quotePair}}
        ${{evidence.length ? `<details class="evidence">
          <summary>Evidence (${{evidence.length}})</summary>
          <ul>${{evidenceList}}</ul>
        </details>` : ''}}
      </article>`;
    }}).join('');
  }}

  function applyFilters() {{
    const q = (document.getElementById('search').value || '').toLowerCase().trim();
    document.querySelectorAll('#theme-grid .theme-card').forEach(card => {{
      const dirOk = activeDirs.has(card.dataset.direction);
      const text = card.dataset.search || '';
      const searchOk = !q || text.includes(q);
      card.classList.toggle('hidden', !(dirOk && searchOk));
    }});
  }}

  // entities
  const entityGrid = document.getElementById('entity-grid');
  const entities = data.emerging_entities || [];
  if (!entities.length) {{
    entityGrid.innerHTML = '<p class="empty">No new entities detected.</p>';
  }} else {{
    entityGrid.innerHTML = entities.slice(0, 24).map(e => {{
      const ex = e.example || {{}};
      const href = ex.url ? escapeHtml(ex.url) : '#';
      const title = ex.title ? escapeHtml(ex.title) : '';
      return `<a class="entity" href="${{href}}" target="_blank" rel="noreferrer" title="${{title}}">
        ${{escapeHtml(e.entity)}}
        <span class="count">${{e.current_count}}×</span>
      </a>`;
    }}).join('');
  }}

  // vocab table
  const vocabHost = document.getElementById('vocab-table');
  const vocab = data.vocabulary_drift || [];
  if (!vocab.length) {{
    vocabHost.innerHTML = '<p class="empty">No significant vocabulary drift in this window.</p>';
  }} else {{
    const maxLift = Math.max(1, ...vocab.map(v => v.lift || 0));
    vocabHost.innerHTML = `<table class="vocab">
      <thead><tr><th>term</th><th>lift</th><th>baseline → current</th><th>example</th></tr></thead>
      <tbody>${{ vocab.slice(0, 20).map(v => {{
        const ex = v.example || {{}};
        const exLink = ex.url
          ? `<a href="${{escapeHtml(ex.url)}}" target="_blank" rel="noreferrer">${{escapeHtml((ex.title || '').slice(0, 70))}}</a>`
          : '';
        const barW = Math.max(20, (v.lift / maxLift) * 100);
        return `<tr>
          <td>${{escapeHtml(v.term)}}</td>
          <td><span class="lift-bar" style="width:${{barW.toFixed(0)}}px"></span>${{v.lift}}</td>
          <td class="mono">${{v.baseline_count}} → ${{v.current_count}}</td>
          <td>${{exLink}}</td>
        </tr>`;
      }}).join('') }}</tbody>
    </table>`;
  }}

  // forward signals
  const forwardHost = document.getElementById('forward-list');
  const forwardSummary = document.getElementById('forward-summary');
  const outlookEl = document.getElementById('forward-outlook');
  const forward = (data.forward_signals || []).filter(Boolean);

  if (data.forward_outlook && outlookEl) {{
    outlookEl.textContent = data.forward_outlook;
    outlookEl.hidden = false;
  }}

  const KIND_CLASS = {{
    'betting market': 'k-betting',
    'roadmap': 'k-roadmap',
    'forecast': 'k-forecast',
    'prediction': 'k-prediction',
  }};

  if (!forward.length) {{
    forwardHost.innerHTML = '<p class="empty">No forward signals detected. Populate the forecasts bucket via --agent-web-file.</p>';
  }} else {{
    const counts = {{}};
    forward.forEach(s => {{ const k = s.kind || 'prediction'; counts[k] = (counts[k] || 0) + 1; }});
    const order = ['betting market', 'roadmap', 'forecast', 'prediction'];
    const stats = order.filter(k => counts[k]).map(k =>
      `<span class="fwd-stat"><strong>${{counts[k]}}</strong> ${{escapeHtml(k)}}${{counts[k] > 1 ? 's' : ''}}</span>`
    ).join('');
    forwardSummary.innerHTML =
      `<span class="fwd-stat"><strong>${{forward.length}}</strong> signal${{forward.length > 1 ? 's' : ''}}</span>` + stats;

    forwardHost.innerHTML = forward.slice(0, 10).map(s => {{
      const kind = s.kind || 'prediction';
      const cls = KIND_CLASS[kind] || 'k-prediction';
      const horizon = s.horizon
        ? `<span class="fwd-horizon">by ${{escapeHtml(s.horizon)}}</span>` : '';
      const meta = `${{escapeHtml(s.published_at || 'undated')}} · ${{escapeHtml(s.source || '')}}`;
      const snippet = s.snippet
        ? `<p class="fwd-snippet">${{escapeHtml(s.snippet.slice(0, 200))}}</p>` : '';
      return `<div class="fwd-card ${{cls}}">
        <div class="fwd-top">
          <span class="fwd-badge ${{cls}}">${{escapeHtml(kind)}}</span>
          ${{horizon}}
          <span class="fwd-meta">${{meta}}</span>
        </div>
        <a class="fwd-title" href="${{escapeHtml(s.url || '#')}}" target="_blank" rel="noreferrer">${{escapeHtml(s.title || '')}}</a>
        ${{snippet}}
      </div>`;
    }}).join('');
  }}

  // source bars
  const sourceCounts = data.source_counts || {{}};
  const maxSource = Math.max(1, ...Object.values(sourceCounts));
  document.getElementById('source-bars').innerHTML = Object.entries(sourceCounts)
    .sort((a, b) => b[1] - a[1])
    .map(([s, c]) => `<div class="bar-row">
      <span class="label">${{escapeHtml(s)}}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${{(c / maxSource) * 100}}%"></div></div>
      <strong>${{c}}</strong>
    </div>`).join('') || '<p class="empty">No sources returned evidence.</p>';

  // bucket chips
  const bucketCounts = data.bucket_counts || {{}};
  document.getElementById('bucket-chips').innerHTML = Object.entries(bucketCounts)
    .sort((a, b) => b[1] - a[1])
    .map(([b, c]) => `<span class="entity">${{escapeHtml(b)}} <span class="count">${{c}}</span></span>`)
    .join('') || '<span class="empty">none</span>';

  // coverage notes
  const notes = data.coverage_notes || [];
  document.getElementById('coverage-notes').innerHTML =
    notes.length
      ? notes.map(n => `<li>${{escapeHtml(n)}}</li>`).join('')
      : '<li style="background:rgba(52,211,153,.05);border-color:rgba(52,211,153,.2);color:#a7f3d0">All checks passed.</li>';

  // smooth scroll for nav
  document.querySelectorAll('.nav a').forEach(a => {{
    a.addEventListener('click', (e) => {{
      const id = a.getAttribute('href').slice(1);
      const el = document.getElementById(id);
      if (el) {{ e.preventDefault(); el.scrollIntoView({{behavior: 'smooth', block: 'start'}}); }}
    }});
  }});
}})();
</script>

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
    resolved = path.resolve()
    uri = resolved.as_uri()

    if webbrowser.open(uri):
        return

    if os.name == "nt":
        os.startfile(str(resolved))  # type: ignore[attr-defined]
        return

    for command, target in (
        ("wslview", str(resolved)),
        ("xdg-open", uri),
        ("open", str(resolved)),
    ):
        if shutil.which(command):
            subprocess.Popen(
                [command, target],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return

    raise RuntimeError(f"Could not find a browser opener for {resolved}")


if __name__ == "__main__":
    raise SystemExit(main())
