"""Source discovery across arXiv, GitHub, and OpenAI-backed web search."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable

import arxiv
import httpx
from openai import OpenAI

from .config import Config
from .models import Source, TimeWindow
from .timeframes import in_window


def source_id(source_type: str, url: str) -> str:
    digest = hashlib.sha1(f"{source_type}:{url}".encode("utf-8")).hexdigest()[:12]
    return f"{source_type}-{digest}"


@dataclass(frozen=True)
class DiscoveryOptions:
    max_results: int = 30
    include_arxiv: bool = True
    include_github: bool = True
    include_web: bool = True


class ArxivDiscoverer:
    def discover(self, topic: str, window: TimeWindow, max_results: int) -> list[Source]:
        search = arxiv.Search(
            query=topic,
            max_results=max_results * 3,
            sort_by=arxiv.SortCriterion.SubmittedDate,
        )
        client = arxiv.Client(page_size=min(100, max_results * 3), delay_seconds=3)
        sources: list[Source] = []
        for result in client.results(search):
            published = result.published.date().isoformat()
            if not in_window(published, window):
                continue
            sources.append(
                Source(
                    id=source_id("arxiv", result.entry_id),
                    title=result.title.strip(),
                    url=result.entry_id,
                    source_type="arxiv",
                    published_at=published,
                    summary=result.summary.strip(),
                    content=result.summary.strip(),
                    metadata={
                        "authors": [author.name for author in result.authors],
                        "primary_category": result.primary_category,
                    },
                )
            )
            if len(sources) >= max_results:
                break
        return sources


class GitHubDiscoverer:
    def __init__(self, config: Config) -> None:
        self.config = config

    async def discover(self, topic: str, window: TimeWindow, max_results: int) -> list[Source]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.config.github_token:
            headers["Authorization"] = f"Bearer {self.config.github_token}"
        query = f'{topic} created:{window.start}..{window.end} stars:>5'
        params = {"q": query, "sort": "stars", "order": "desc", "per_page": max_results}
        async with httpx.AsyncClient(timeout=self.config.request_timeout_seconds, headers=headers) as client:
            response = await client.get("https://api.github.com/search/repositories", params=params)
            response.raise_for_status()
            payload = response.json()
        sources: list[Source] = []
        for item in payload.get("items", []):
            description = item.get("description") or ""
            sources.append(
                Source(
                    id=source_id("github", item["html_url"]),
                    title=item["full_name"],
                    url=item["html_url"],
                    source_type="github",
                    published_at=(item.get("created_at") or "")[:10],
                    summary=description,
                    content=description,
                    metadata={
                        "stars": item.get("stargazers_count", 0),
                        "forks": item.get("forks_count", 0),
                        "language": item.get("language"),
                    },
                )
            )
        return sources


class WebSearchDiscoverer:
    def __init__(self, config: Config) -> None:
        self.config = config

    def discover(self, topic: str, window: TimeWindow, max_results: int) -> list[Source]:
        if not self.config.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is required for OpenAI web discovery. "
                "Use --no-web to scan only arXiv/GitHub, but analysis still requires GPT-5.4."
            )
        client = OpenAI(api_key=self.config.openai_api_key)
        prompt = (
            "Find recent, high-signal web sources for trend analysis.\n"
            f"Topic: {topic}\nWindow: {window.start} to {window.end}\n"
            f"Return at most {max_results} items as JSON array only. Each item must have "
            "title, url, published_at (YYYY-MM-DD if known), summary."
        )
        response = client.responses.create(
            model=self.config.openai_model,
            tools=[{"type": "web_search_preview"}],
            input=prompt,
        )
        text = getattr(response, "output_text", "") or "[]"
        try:
            items = json.loads(text[text.find("[") : text.rfind("]") + 1])
        except (ValueError, json.JSONDecodeError):
            return []
        sources: list[Source] = []
        for item in items:
            url = item.get("url")
            if not url:
                continue
            published_at = item.get("published_at") or window.end
            if not in_window(published_at, window):
                continue
            summary = item.get("summary") or ""
            sources.append(
                Source(
                    id=source_id("web", url),
                    title=item.get("title") or url,
                    url=url,
                    source_type="web",
                    published_at=published_at[:10],
                    summary=summary,
                    content=summary,
                )
            )
        return sources[:max_results]


def dedupe_sources(sources: Iterable[Source]) -> list[Source]:
    seen: set[str] = set()
    unique: list[Source] = []
    for source in sources:
        if source.url in seen:
            continue
        seen.add(source.url)
        unique.append(source)
    return unique


async def discover_sources(
    topic: str,
    window: TimeWindow,
    config: Config,
    options: DiscoveryOptions,
) -> list[Source]:
    found: list[Source] = []
    per_source_limit = max(5, options.max_results)
    if options.include_arxiv:
        found.extend(ArxivDiscoverer().discover(topic, window, per_source_limit))
    if options.include_github:
        found.extend(await GitHubDiscoverer(config).discover(topic, window, per_source_limit))
    if options.include_web:
        found.extend(WebSearchDiscoverer(config).discover(topic, window, per_source_limit))
    return dedupe_sources(found)[: options.max_results * 3]

