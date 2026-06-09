"""Core data models used across discovery, analysis, trend scoring, and rendering."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class TimeWindow:
    start: str
    end: str


@dataclass
class Source:
    id: str
    title: str
    url: str
    source_type: str
    published_at: str
    summary: str = ""
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Source":
        return cls(**data)


@dataclass
class TopicEvidence:
    source_id: str
    quote: str
    claim: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractedTopic:
    name: str
    description: str
    relevance_score: float
    novelty_score: float
    key_findings: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    evidence: list[TopicEvidence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "evidence": [item.to_dict() for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExtractedTopic":
        evidence = [TopicEvidence(**item) for item in data.get("evidence", [])]
        payload = {**data, "evidence": evidence}
        return cls(**payload)


@dataclass
class ScanRecord:
    topic_query: str
    generated_at: str
    window: TimeWindow
    sources: list[Source]
    extracted_topics: list[ExtractedTopic]

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic_query": self.topic_query,
            "generated_at": self.generated_at,
            "window": asdict(self.window),
            "sources": [source.to_dict() for source in self.sources],
            "extracted_topics": [topic.to_dict() for topic in self.extracted_topics],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScanRecord":
        return cls(
            topic_query=data["topic_query"],
            generated_at=data["generated_at"],
            window=TimeWindow(**data["window"]),
            sources=[Source.from_dict(item) for item in data.get("sources", [])],
            extracted_topics=[ExtractedTopic.from_dict(item) for item in data.get("extracted_topics", [])],
        )


@dataclass
class TrendPoint:
    date: str
    count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TrendTopic:
    name: str
    description: str
    source_count: int
    source_diversity: int
    relevance_score: float
    novelty_score: float
    velocity: float
    direction: str
    first_seen: str
    last_seen: str
    time_series: list[TrendPoint]
    source_ids: list[str]
    key_findings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "time_series": [point.to_dict() for point in self.time_series],
        }


@dataclass
class TrendMap:
    topic_query: str
    generated_at: str
    window: TimeWindow
    topics: list[TrendTopic]
    sources: list[Source]
    scans: list[ScanRecord]

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic_query": self.topic_query,
            "generated_at": self.generated_at,
            "window": asdict(self.window),
            "topics": [topic.to_dict() for topic in self.topics],
            "sources": [source.to_dict() for source in self.sources],
            "scans": [scan.to_dict() for scan in self.scans],
        }

