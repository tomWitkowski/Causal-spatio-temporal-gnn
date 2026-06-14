"""Tauron Dystrybucja power outages (planned + failures) for gmina Kozy.

Uses the undocumented /waapi/outages REST endpoint discovered from the Tauron
website JavaScript. Returns all outages in a date range; we filter by "Kozy"
in the Message field and accumulate across runs.

Output: timestamp (outage start), lat, lon, category, title, url, source.
"""
from __future__ import annotations

import datetime as dt
import itertools
import logging

import pandas as pd

from ..base import BaseDownloader, FetchResult
from ..config import PROCESSED_DIR
from ..geo import haversine_km
from .. import geocode, http

log = logging.getLogger("kozy_data.tauron")

API_URL = "https://www.tauron-dystrybucja.pl/waapi/outages"
OUTAGE_PAGE = "https://www.tauron-dystrybucja.pl/wylaczenia"
_TYPE_MAP = {1: "planned", 2: "failure"}
_TABLE = "tauron_outages"
_PARQUET = PROCESSED_DIR / f"{_TABLE}.parquet"
_KEYWORD = "kozy"


def _geocode_outages(df: pd.DataFrame, cen_lat: float, cen_lon: float) -> pd.DataFrame:
    """Resolve each outage's streets to coordinates via local OSM matching.

    Overrides lat/lon with the matched streets' centroid and adds ``n_streets``
    and ``spread_km`` (max pairwise distance). Falls back to the gmina centroid
    when no street is recognised. Idempotent: re-derives everything from ``title``.
    """
    lats, lons, counts, spreads = [], [], [], []
    for title in df["title"].fillna(""):
        matches = geocode.match_streets(title)
        if matches:
            pts = [(m["lat"], m["lon"]) for m in matches]
            lats.append(sum(p[0] for p in pts) / len(pts))
            lons.append(sum(p[1] for p in pts) / len(pts))
            counts.append(len(pts))
            spreads.append(round(max(
                (haversine_km(a[0], a[1], b[0], b[1])
                 for a, b in itertools.combinations(pts, 2)), default=0.0), 3))
        else:
            lats.append(cen_lat)
            lons.append(cen_lon)
            counts.append(0)
            spreads.append(0.0)
    df = df.copy()
    df["lat"], df["lon"] = lats, lons
    df["n_streets"], df["spread_km"] = counts, spreads
    return df


class TauronOutagesDownloader(BaseDownloader):
    name = "tauron_outages"
    license = "Tauron Dystrybucja (public outage notices)"

    def run(self, since=None) -> FetchResult:
        lat, lon = self.aoi.centroid_lat, self.aoi.centroid_lon

        existing = pd.read_parquet(_PARQUET) if _PARQUET.exists() else None

        if since is not None:
            start = dt.date.fromisoformat(since) if isinstance(since, str) else since
        elif existing is not None and len(existing):
            max_ts = pd.to_datetime(existing["timestamp"], utc=True).max()
            start = max_ts.date() - dt.timedelta(days=7)
        else:
            start = self.default_start()

        end = dt.date.today()
        params = {"fromDate": start.isoformat(), "toDate": end.isoformat()}

        try:
            data = http.get_json(API_URL, params=params, use_cache=False, timeout=60)
        except Exception as exc:
            return FetchResult(self.name, 0, note=f"API error: {exc}")

        if not isinstance(data, list):
            return FetchResult(self.name, 0, note=f"unexpected response type: {type(data)}")

        rows = []
        for item in data:
            msg = item.get("Message") or ""
            if _KEYWORD not in msg.lower():
                continue
            start_ts = item.get("OutageStartDate")
            if not start_ts:
                continue
            category = _TYPE_MAP.get(item.get("TypeId"), "unknown")
            rows.append({
                "timestamp": pd.Timestamp(start_ts, tz="UTC"),
                "lat": lat, "lon": lon,
                "category": category,
                "title": msg[:300],
                "url": OUTAGE_PAGE,
                "source": self.name,
            })

        log.info("tauron: %d Kozy outages from %s to %s (of %d total fetched)",
                 len(rows), start, end, len(data))

        if not rows and existing is None:
            return FetchResult(self.name, 0, note="no Kozy outages in date range")

        df = pd.DataFrame(rows) if rows else pd.DataFrame(
            columns=["timestamp", "lat", "lon", "category", "title", "url", "source"])

        if existing is not None and len(existing):
            df = pd.concat([existing, df], ignore_index=True)
        df = df.drop_duplicates(subset=["timestamp", "category", "title"])
        df = df.sort_values("timestamp", ascending=False).reset_index(drop=True)
        df = _geocode_outages(df, lat, lon)
        log.info("tauron: geocoded %d/%d outages to streets",
                 int((df["n_streets"] > 0).sum()), len(df))

        return self.emit(df, _TABLE, urls=[API_URL],
                         note=f"{len(df)} Kozy outage records accumulated")
