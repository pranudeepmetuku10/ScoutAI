"""Runtime settings, read once from `SCOUTAI_*` environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from scoutai import __version__

USER_AGENT_TOKEN = "ScoutAI"
USER_AGENT = f"{USER_AGENT_TOKEN}/{__version__} (+https://github.com/pranudeepmetuku10/ScoutAI)"


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("SCOUTAI_DATA_DIR", "data")))
    allow_private_hosts: bool = field(default_factory=lambda: _env_bool("SCOUTAI_ALLOW_PRIVATE_HOSTS", False))
    # Seconds between requests to the same host; robots.txt Crawl-delay wins if larger.
    request_delay: float = field(default_factory=lambda: float(os.getenv("SCOUTAI_REQUEST_DELAY", "0.25")))
    request_timeout: float = field(default_factory=lambda: float(os.getenv("SCOUTAI_REQUEST_TIMEOUT", "20")))
    chunk_size: int = field(default_factory=lambda: int(os.getenv("SCOUTAI_CHUNK_SIZE", "1000")))  # chars
    chunk_overlap: int = field(default_factory=lambda: int(os.getenv("SCOUTAI_CHUNK_OVERLAP", "150")))
    max_response_bytes: int = field(
        default_factory=lambda: int(os.getenv("SCOUTAI_MAX_RESPONSE_BYTES", str(5 * 1024 * 1024)))
    )


def get_settings() -> Settings:
    return Settings()
