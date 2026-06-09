"""Content crawling and lightweight extraction."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from .config import Config
from .models import Source


class Crawler:
    def __init__(self, config: Config, concurrency: int = 5) -> None:
        self.config = config
        self.concurrency = concurrency
        self.cache_dir = config.data_dir / "cache" / "pages"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    async def enrich(self, sources: list[Source]) -> list[Source]:
        semaphore = asyncio.Semaphore(self.concurrency)
        async with httpx.AsyncClient(
            timeout=self.config.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "trender/0.1"},
        ) as client:
            tasks = [self._enrich_one(client, semaphore, source) for source in sources]
            return list(await asyncio.gather(*tasks))

    async def _enrich_one(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        source: Source,
    ) -> Source:
        if source.source_type == "arxiv":
            return source
        if source.source_type == "github":
            readme = await self._fetch_github_readme(client, source.url)
            if readme:
                source.content = f"{source.summary}\n\n{readme}".strip()
            return source
        async with semaphore:
            try:
                html = await self._fetch_cached(client, source.url)
            except httpx.HTTPError:
                return source
        text = html_to_text(html)
        if text:
            source.content = f"{source.summary}\n\n{text[:12000]}".strip()
        return source

    async def _fetch_cached(self, client: httpx.AsyncClient, url: str) -> str:
        path = self._cache_path(url)
        if path.exists():
            return path.read_text(encoding="utf-8", errors="ignore")
        response = await client.get(url)
        response.raise_for_status()
        text = response.text
        path.write_text(text, encoding="utf-8")
        return text

    async def _fetch_github_readme(self, client: httpx.AsyncClient, repo_url: str) -> str:
        parts = repo_url.rstrip("/").split("/")
        if len(parts) < 2:
            return ""
        owner, repo = parts[-2], parts[-1]
        api_url = f"https://api.github.com/repos/{owner}/{repo}/readme"
        headers = {"Accept": "application/vnd.github.raw"}
        if self.config.github_token:
            headers["Authorization"] = f"Bearer {self.config.github_token}"
        try:
            response = await client.get(api_url, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError:
            return ""
        return response.text[:12000]

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.html"


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ").split())
    return text[:20000]

