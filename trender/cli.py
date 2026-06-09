"""Command-line interface for Trender."""

from __future__ import annotations

import json
import webbrowser
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from .config import load_config
from .service import scan_trends_sync
from .storage import Storage
from .timeframes import make_window
from .trends import build_trend_map

console = Console()


def status(message: str) -> None:
    console.print(f"[dim]→[/dim] {message}")


@click.group()
@click.option("--data-dir", type=click.Path(path_type=Path), default=None, help="Directory for scans, cache, and reports.")
@click.pass_context
def main(ctx: click.Context, data_dir: Path | None) -> None:
    """Extract trend signals and generate interactive static HTML maps."""
    config = load_config(data_dir)
    storage = Storage(config.data_dir)
    storage.ensure()
    ctx.obj = {"config": config, "storage": storage}


@main.command()
@click.argument("topic")
@click.option("--days", type=int, default=None, help="Analyze sources from the last N days.")
@click.option("--from", "start", type=str, default=None, help="Start date, YYYY-MM-DD.")
@click.option("--to", "end", type=str, default=None, help="End date, YYYY-MM-DD.")
@click.option("--max-results", type=int, default=30, show_default=True, help="Maximum results per source family.")
@click.option("--no-arxiv", is_flag=True, help="Disable arXiv discovery.")
@click.option("--no-github", is_flag=True, help="Disable GitHub discovery.")
@click.option("--no-web", is_flag=True, help="Disable OpenAI web discovery.")
@click.pass_context
def scan(
    ctx: click.Context,
    topic: str,
    days: int | None,
    start: str | None,
    end: str | None,
    max_results: int,
    no_arxiv: bool,
    no_github: bool,
    no_web: bool,
) -> None:
    """Discover sources, extract trend topics, and write an HTML report."""
    console.print(f"[bold]Scanning[/bold] {topic!r}")
    try:
        result = scan_trends_sync(
            topic=topic,
            days=days,
            start=start,
            end=end,
            max_results=max_results,
            include_arxiv=not no_arxiv,
            include_github=not no_github,
            include_web=not no_web,
            data_dir=ctx.obj["config"].data_dir,
            progress=status,
        )
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    status(
        f"Trend map ready: ranked {result.trend_topic_count} topics from "
        f"{result.source_count} sources ({result.source_summary})."
    )
    console.print(f"[green]✓[/green] Saved scan: [cyan]{result.scan_path}[/cyan]")
    console.print(f"[green]✓[/green] Saved report: [cyan]{result.report_path}[/cyan]")


@main.command()
@click.argument("topic")
@click.pass_context
def explore(ctx: click.Context, topic: str) -> None:
    """Open the latest report for a topic."""
    storage = ctx.obj["storage"]
    path = storage.latest_report(topic)
    if not path:
        raise click.ClickException(f"No report found for {topic!r}. Run trender scan first.")
    webbrowser.open(path.resolve().as_uri())
    console.print(f"Opened [cyan]{path}[/cyan]")


@main.command()
@click.argument("topic", required=False)
@click.pass_context
def history(ctx: click.Context, topic: str | None) -> None:
    """List saved scans."""
    storage = ctx.obj["storage"]
    scans_root = storage.data_dir / "scans"
    table = Table(title="Trender history")
    table.add_column("Topic")
    table.add_column("Generated")
    table.add_column("Window")
    table.add_column("Sources", justify="right")
    for topic_dir in sorted(scans_root.glob("*")) if scans_root.exists() else []:
        if topic and topic.lower().replace(" ", "-") not in topic_dir.name:
            continue
        for path in sorted(topic_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            table.add_row(
                payload["topic_query"],
                payload["generated_at"],
                f'{payload["window"]["start"]} → {payload["window"]["end"]}',
                str(len(payload.get("sources", []))),
            )
    console.print(table)


@main.command()
@click.argument("topic")
@click.option("--from-a", required=True, help="First range start date.")
@click.option("--to-a", required=True, help="First range end date.")
@click.option("--from-b", required=True, help="Second range start date.")
@click.option("--to-b", required=True, help="Second range end date.")
@click.pass_context
def compare(ctx: click.Context, topic: str, from_a: str, to_a: str, from_b: str, to_b: str) -> None:
    """Compare topic counts across two stored time windows."""
    storage = ctx.obj["storage"]
    scans = storage.load_scans(topic)
    trend_a = build_trend_map(topic, scans, make_window(None, from_a, to_a))
    trend_b = build_trend_map(topic, scans, make_window(None, from_b, to_b))
    counts_a = {topic.name: topic.source_count for topic in trend_a.topics}
    counts_b = {topic.name: topic.source_count for topic in trend_b.topics}
    names = sorted(set(counts_a) | set(counts_b), key=lambda name: counts_b.get(name, 0) - counts_a.get(name, 0), reverse=True)
    table = Table(title=f"Comparison: {topic}")
    table.add_column("Topic")
    table.add_column(f"{from_a} → {to_a}", justify="right")
    table.add_column(f"{from_b} → {to_b}", justify="right")
    table.add_column("Delta", justify="right")
    for name in names[:20]:
        a = counts_a.get(name, 0)
        b = counts_b.get(name, 0)
        table.add_row(name, str(a), str(b), f"{b - a:+d}")
    console.print(table)


if __name__ == "__main__":
    main()

