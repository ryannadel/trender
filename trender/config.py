"""Runtime configuration for Trender."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_dir: Path = Path("data")
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.4"
    embedding_model: str = "text-embedding-3-small"
    github_token: str | None = None
    request_timeout_seconds: float = 20.0


def load_config(data_dir: Path | None = None) -> Config:
    env_file = Path(".env")
    env_values = read_env_file(env_file) if env_file.exists() else {}
    return Config(
        data_dir=data_dir or Path(os.getenv("TRENDER_DATA_DIR", "data")),
        openai_api_key=os.getenv("OPENAI_API_KEY") or env_values.get("OPENAI_API_KEY"),
        openai_model=os.getenv("TRENDER_OPENAI_MODEL", "gpt-5.4"),
        embedding_model=os.getenv("TRENDER_EMBEDDING_MODEL", "text-embedding-3-small"),
        github_token=os.getenv("GITHUB_TOKEN") or env_values.get("GITHUB_TOKEN"),
        request_timeout_seconds=float(os.getenv("TRENDER_TIMEOUT_SECONDS", "20")),
    )


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values

