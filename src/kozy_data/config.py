"""Configuration loading and project paths."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# Project layout (repo root = three levels up from this file: src/kozy_data/config.py)
PKG_DIR = Path(__file__).resolve().parent
ROOT = PKG_DIR.parents[1]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
CACHE_DIR = ROOT / ".http_cache"


@dataclass
class BBox:
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float

    def as_tuple(self) -> tuple[float, float, float, float]:
        """(min_lat, min_lon, max_lat, max_lon)."""
        return (self.min_lat, self.min_lon, self.max_lat, self.max_lon)

    def overpass(self) -> str:
        """Overpass bbox order: south,west,north,east."""
        return f"{self.min_lat},{self.min_lon},{self.max_lat},{self.max_lon}"

    def contains(self, lat: float, lon: float) -> bool:
        return (
            self.min_lat <= lat <= self.max_lat
            and self.min_lon <= lon <= self.max_lon
        )


@dataclass
class AOIConfig:
    """Area-of-interest config (gmina Kozy)."""

    name: str
    teryt_gmina: str
    teryt_powiat: str
    teryt_woj: str
    centroid_lat: float
    centroid_lon: float
    bbox: BBox
    date_start: str
    timezone: str
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def start_date(self) -> dt.date:
        return dt.date.fromisoformat(self.date_start)


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@lru_cache(maxsize=1)
def load_aoi(path: Path | None = None) -> AOIConfig:
    data = _load_yaml(path or CONFIG_DIR / "kozy.yaml")
    bbox = BBox(**data["bbox"])
    return AOIConfig(
        name=data["name"],
        teryt_gmina=str(data["teryt_gmina"]),
        teryt_powiat=str(data["teryt_powiat"]),
        teryt_woj=str(data["teryt_woj"]),
        centroid_lat=data["centroid"]["lat"],
        centroid_lon=data["centroid"]["lon"],
        bbox=bbox,
        date_start=str(data["date_start"]),
        timezone=data.get("timezone", "Europe/Warsaw"),
        raw=data,
    )


@lru_cache(maxsize=1)
def load_sources(path: Path | None = None) -> dict[str, Any]:
    return _load_yaml(path or CONFIG_DIR / "sources.yaml")


def source_config(name: str) -> dict[str, Any]:
    return load_sources().get(name, {}) or {}
