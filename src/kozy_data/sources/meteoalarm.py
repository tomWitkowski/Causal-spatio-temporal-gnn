"""Meteoalarm weather warnings for powiat bielski (EMMA_ID PL2402).

Source: https://feeds.meteoalarm.org/api/v1/warnings/feeds-poland
Issued by IMGW-PIB (Polish Meteorological Institute) via CAP protocol.
Covers all active warnings; accumulates across runs to build history.

Note: no historical archive available — data grows from first run forward.

Output: timestamp (=effective), lat, lon, category (severity), title (event),
        description, expires, identifier, source.
"""
from __future__ import annotations

import logging

import pandas as pd

from ..base import BaseDownloader, FetchResult
from ..config import PROCESSED_DIR
from .. import http

log = logging.getLogger("kozy_data.meteoalarm")

API_URL = "https://feeds.meteoalarm.org/api/v1/warnings/feeds-poland"
_EMMA_ID = "PL2402"       # śląskie powiat bielski
_EMMA_FALLBACK = "2402"   # partial match fallback
_TABLE = "meteoalarm_warnings"
_PARQUET = PROCESSED_DIR / f"{_TABLE}.parquet"

_SEVERITY_MAP = {
    "Minor":    "green",
    "Moderate": "yellow",
    "Severe":   "orange",
    "Extreme":  "red",
}


def _matches_bielski(info: dict) -> bool:
    for area in info.get("area", []):
        for gc in area.get("geocode", []):
            if gc.get("value") == _EMMA_ID:
                return True
        desc = area.get("areaDesc", "").lower()
        if "lski" in desc and "bielski" in desc:
            return True
    return False


class MeteoalarmDownloader(BaseDownloader):
    name = "meteoalarm"
    license = "IMGW-PIB / Meteoalarm (CC-BY, open data)"

    def run(self, since=None) -> FetchResult:
        lat, lon = self.aoi.centroid_lat, self.aoi.centroid_lon
        existing = pd.read_parquet(_PARQUET) if _PARQUET.exists() else None

        try:
            data = http.get_json(API_URL, use_cache=False, timeout=30)
        except Exception as exc:
            return FetchResult(self.name, 0, note=f"API error: {exc}")

        rows = []
        seen_ids: set[str] = set()
        for w in data.get("warnings", []):
            alert = w.get("alert", {})
            identifier = alert.get("identifier", "")
            if not identifier or identifier in seen_ids:
                continue
            for info in alert.get("info", []):
                if not _matches_bielski(info):
                    continue
                # prefer Polish language entry
                if info.get("language", "").startswith("en") and any(
                    i.get("language", "").startswith("pl")
                    for i in alert.get("info", [])
                ):
                    continue
                seen_ids.add(identifier)
                rows.append({
                    "timestamp": pd.Timestamp(info["effective"]),
                    "expires":   pd.Timestamp(info["expires"]),
                    "lat": lat, "lon": lon,
                    "category": _SEVERITY_MAP.get(info.get("severity", ""), info.get("severity", "")),
                    "title": info.get("event", "")[:200],
                    "description": info.get("description", "")[:500],
                    "identifier": identifier,
                    "source": self.name,
                })
                break  # one row per alert

        log.info("meteoalarm: %d PL2402 (bielski) warnings in current feed", len(rows))

        if not rows and existing is None:
            return FetchResult(self.name, 0, note="no active bielski warnings")

        df = pd.DataFrame(rows) if rows else pd.DataFrame(
            columns=["timestamp", "expires", "lat", "lon",
                     "category", "title", "description", "identifier", "source"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df["expires"] = pd.to_datetime(df["expires"], utc=True, errors="coerce")

        if existing is not None and len(existing):
            df = pd.concat([existing, df], ignore_index=True)
        df = df.drop_duplicates(subset=["identifier"])
        df = df.sort_values("timestamp", ascending=False).reset_index(drop=True)

        return self.emit(df, _TABLE, urls=[API_URL],
                         note=f"{len(df)} bielski warnings accumulated")
