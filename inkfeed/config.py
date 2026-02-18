from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SourceConfig:
    name: str
    type: str
    frequency: str = "daily"
    enabled: bool = True
    display_name: str = ""
    params: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.display_name:
            self.display_name = self.name


@dataclass
class SleepscreenConfig:
    """Settings for the grayscale BMP sleep-screen export."""

    width: int = 480
    height: int = 800
    spotlight_count: int = 2
    max_headlines_per_card: int = 10
    max_excerpt_chars: int = 350


@dataclass
class Config:
    output_dir: Path
    sources: list[SourceConfig]
    embed_assets: bool = False
    output_formats: list[str] = field(default_factory=lambda: ["html"])
    max_workers: int = 8
    max_retries: int = 3
    sleepscreen: SleepscreenConfig = field(default_factory=SleepscreenConfig)


def load_config(path: Path) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    general = raw.get("general", {})
    output_dir = Path(general.get("output_dir", "output"))

    sources = []
    for name, src in raw.get("sources", {}).items():
        src = dict(src)
        sources.append(SourceConfig(
            name=name,
            type=src.pop("type", "api"),
            frequency=src.pop("frequency", "daily"),
            enabled=src.pop("enabled", True),
            display_name=src.pop("display_name", ""),
            params=src,
        ))

    embed_assets = bool(general.get("embed_assets", False))
    output_formats = list(general.get("output_formats", ["html"]))
    max_workers = int(general.get("max_workers", 8))
    max_retries = int(general.get("max_retries", 3))

    ss_raw = raw.get("sleepscreen", {})
    sleepscreen = SleepscreenConfig(
        width=int(ss_raw.get("width", 480)),
        height=int(ss_raw.get("height", 800)),
        spotlight_count=int(ss_raw.get("spotlight_count", 2)),
        max_headlines_per_card=int(ss_raw.get("max_headlines_per_card", 10)),
        max_excerpt_chars=int(ss_raw.get("max_excerpt_chars", 200)),
    )

    return Config(
        output_dir=output_dir,
        sources=sources,
        embed_assets=embed_assets,
        output_formats=output_formats,
        max_workers=max_workers,
        max_retries=max_retries,
        sleepscreen=sleepscreen,
    )
