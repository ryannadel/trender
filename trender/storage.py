"""JSON persistence for scans and generated reports."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .models import ScanRecord


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "topic"


class Storage:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    def ensure(self) -> None:
        for child in ("scans", "reports", "cache"):
            (self.data_dir / child).mkdir(parents=True, exist_ok=True)

    def scans_dir(self, topic_query: str) -> Path:
        return self.data_dir / "scans" / slugify(topic_query)

    def reports_dir(self, topic_query: str) -> Path:
        return self.data_dir / "reports" / slugify(topic_query)

    def save_scan(self, scan: ScanRecord) -> Path:
        directory = self.scans_dir(scan.topic_query)
        directory.mkdir(parents=True, exist_ok=True)
        safe_time = scan.generated_at.replace(":", "").replace("+", "Z")
        path = directory / f"{safe_time}.json"
        path.write_text(json.dumps(scan.to_dict(), indent=2), encoding="utf-8")
        return path

    def load_scans(self, topic_query: str) -> list[ScanRecord]:
        directory = self.scans_dir(topic_query)
        if not directory.exists():
            return []
        scans = []
        for path in sorted(directory.glob("*.json")):
            scans.append(ScanRecord.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        return scans

    def save_report(self, topic_query: str, generated_at: str, html: str) -> Path:
        directory = self.reports_dir(topic_query)
        directory.mkdir(parents=True, exist_ok=True)
        safe_time = generated_at.replace(":", "").replace("+", "Z")
        path = directory / f"{safe_time}.html"
        path.write_text(html, encoding="utf-8")
        latest = directory / "latest.html"
        latest.write_text(html, encoding="utf-8")
        return path

    def latest_report(self, topic_query: str) -> Path | None:
        path = self.reports_dir(topic_query) / "latest.html"
        return path if path.exists() else None

