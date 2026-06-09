"""LLM-based theme extraction."""

from __future__ import annotations

import json

from openai import OpenAI

from .config import Config
from .models import ExtractedTopic, Source, TopicEvidence


class Analyzer:
    def __init__(self, config: Config) -> None:
        self.config = config

    def analyze(self, topic_query: str, sources: list[Source]) -> list[ExtractedTopic]:
        if not sources:
            return []
        if not self.config.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is required for topic analysis. "
                "Trender does not use heuristic fallback analysis."
            )
        return self._analyze_with_openai(topic_query, sources)

    def _analyze_with_openai(self, topic_query: str, sources: list[Source]) -> list[ExtractedTopic]:
        client = OpenAI(api_key=self.config.openai_api_key)
        source_blob = "\n\n".join(
            f"ID: {source.id}\nTYPE: {source.source_type}\nDATE: {source.published_at}\n"
            f"TITLE: {source.title}\nURL: {source.url}\nTEXT: {(source.content or source.summary)[:4000]}"
            for source in sources
        )
        prompt = (
            "You are extracting trend signals from noisy source material.\n"
            f"User topic: {topic_query}\n\n"
            "Return JSON only with a top-level key 'topics'. Each topic must include: "
            "name, description, relevance_score (0-1), novelty_score (0-1), "
            "key_findings (array of strings), source_ids (array), and evidence "
            "(array of objects with source_id, quote, claim). Prefer emerging, specific themes.\n\n"
            f"Sources:\n{source_blob}"
        )
        response = client.responses.create(
            model=self.config.openai_model,
            input=prompt,
            text={"format": {"type": "json_object"}},
        )
        text = getattr(response, "output_text", "") or "{}"
        payload = json.loads(text)
        return [topic_from_payload(item) for item in payload.get("topics", [])]


def topic_from_payload(item: dict) -> ExtractedTopic:
    return ExtractedTopic(
        name=str(item.get("name", "Untitled trend")).strip(),
        description=str(item.get("description", "")).strip(),
        relevance_score=float(item.get("relevance_score", 0.5)),
        novelty_score=float(item.get("novelty_score", 0.5)),
        key_findings=[str(value) for value in item.get("key_findings", [])][:5],
        source_ids=[str(value) for value in item.get("source_ids", [])],
        evidence=[
            TopicEvidence(
                source_id=str(ev.get("source_id", "")),
                quote=str(ev.get("quote", ""))[:500],
                claim=str(ev.get("claim", ""))[:500],
            )
            for ev in item.get("evidence", [])
            if ev.get("source_id")
        ],
    )

