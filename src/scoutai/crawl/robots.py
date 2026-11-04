"""robots.txt rules: allow/deny, Crawl-delay and declared sitemaps."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib import robotparser

from scoutai.config import USER_AGENT_TOKEN


@dataclass
class Robots:
    parser: robotparser.RobotFileParser | None = None
    sitemaps: list[str] = field(default_factory=list)

    @classmethod
    def parse(cls, text: str) -> Robots:
        parser = robotparser.RobotFileParser()
        parser.parse(text.splitlines())
        return cls(parser=parser, sitemaps=list(parser.site_maps() or []))

    @classmethod
    def allow_all(cls) -> Robots:
        return cls()

    @classmethod
    def deny_all(cls) -> Robots:
        return cls.parse("User-agent: *\nDisallow: /")

    def can_fetch(self, url: str) -> bool:
        return self.parser is None or self.parser.can_fetch(USER_AGENT_TOKEN, url)

    @property
    def crawl_delay(self) -> float:
        if self.parser is None:
            return 0.0
        delay = self.parser.crawl_delay(USER_AGENT_TOKEN)
        return float(delay) if delay else 0.0


def robots_from_response(status: int, text: str) -> Robots:
    """Common convention: 4xx means no restrictions; 5xx means the site is unavailable, so stay out."""
    if status == 200:
        return Robots.parse(text)
    if 400 <= status < 500:
        return Robots.allow_all()
    return Robots.deny_all()
