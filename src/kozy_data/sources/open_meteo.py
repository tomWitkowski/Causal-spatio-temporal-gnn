"""Open-Meteo historical (ERA5 reanalysis) hourly weather for spatial grid points.

Free, no API key. Fetches all points from spatial_grid.parquet in a single batch
request. Falls back to the AOI centroid if the grid is not yet available.

Long-format output: timestamp, lat, lon, variable, value, unit, source.
"""
from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

from ..base import BaseDownloader, FetchResult
from ..config import PROCESSED_DIR
from .. import http

log = logging.getLogger("kozy_data.open_meteo")

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
LAG_DAYS = 6
_GRID_PATH = PROCESSED_DIR / "spatial_grid.parquet"
_WEATHER_PATH = PROCESSED_DIR / "open_meteo_weather.parquet"


def _load_grid(centroid_lat: float, centroid_lon: float) -> pd.DataFrame:
    if _GRID_PATH.exists():
        return pd.read_parquet(_GRID_PATH, columns=["lat", "lon", "elevation_m"])
    return pd.DataFrame({"lat": [centroid_lat], "lon": [centroid_lon], "elevation_m": [None]})


def _fetch_start(grid: pd.DataFrame, fallback: dt.date) -> dt.date:
    """Earliest date we need to fetch: min of per-point max timestamps, or fallback."""
    if not _WEATHER_PATH.exists():
        return fallback
    existing = pd.read_parquet(_WEATHER_PATH, columns=["timestamp", "lat", "lon"])
    existing["timestamp"] = pd.to_datetime(existing["timestamp"], utc=True)
    point_set = set(zip(grid["lat"], grid["lon"]))
    covered = {
        (la, lo): grp["timestamp"].max().date()
        for (la, lo), grp in existing.groupby(["lat", "lon"])
        if (la, lo) in point_set
    }
    if len(covered) < len(point_set):
        return fallback  # new points without any data → full history
    return min(covered.values()) - dt.timedelta(days=1)


class OpenMeteoDownloader(BaseDownloader):
    name = "open_meteo"
    license = "Open-Meteo / Copernicus ERA5 (CC-BY 4.0, attribution required)"

    def run(self, since=None) -> FetchResult:
        hourly = self.cfg.get("hourly") or [
            "temperature_2m", "precipitation", "wind_speed_10m",
        ]
        grid = _load_grid(self.aoi.centroid_lat, self.aoi.centroid_lon)
        elev_map = dict(zip(zip(grid["lat"], grid["lon"]), grid["elevation_m"]))
        end = dt.date.today() - dt.timedelta(days=LAG_DAYS)

        if since is not None:
            start = dt.date.fromisoformat(since) if isinstance(since, str) else since
        else:
            start = _fetch_start(grid, self.default_start())

        if end <= start:
            return FetchResult(self.name, 0, note="already up to date")

        log.info("open_meteo: %d point(s), %s → %s", len(grid), start, end)
        frames = []
        for i, (lat, lon) in enumerate(zip(grid["lat"], grid["lon"])):
            params = {
                "latitude": lat,
                "longitude": lon,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "hourly": ",".join(hourly),
                "timezone": "UTC",
            }
            log.info("  [%d/%d] (%.4f, %.4f)", i + 1, len(grid), lat, lon)
            try:
                loc = http.get_json(ARCHIVE_URL, params=params, timeout=120)
            except Exception as exc:
                log.warning("point (%.4f, %.4f) failed: %s — skipping", lat, lon, exc)
                continue
            block = loc.get("hourly", {})
            units = loc.get("hourly_units", {})
            times = block.get("time", [])
            for var in hourly:
                values = block.get(var)
                if not values:
                    continue
                frames.append(pd.DataFrame({
                    "timestamp": times,
                    "lat": lat,
                    "lon": lon,
                    "elevation_m": elev_map.get((lat, lon)),
                    "variable": var,
                    "value": values,
                    "unit": units.get(var),
                    "source": self.name,
                }))

        if not frames:
            return FetchResult(self.name, 0, note="no hourly data returned")

        new_df = pd.concat(frames, ignore_index=True)
        new_df["timestamp"] = pd.to_datetime(new_df["timestamp"], utc=True)

        if _WEATHER_PATH.exists():
            existing = pd.read_parquet(_WEATHER_PATH)
            # Backfill elevation_m in existing data if the column is missing
            if "elevation_m" not in existing.columns:
                existing = existing.merge(
                    grid[["lat", "lon", "elevation_m"]], on=["lat", "lon"], how="left")
            df = pd.concat([existing, new_df], ignore_index=True)
            df = df.drop_duplicates(subset=["timestamp", "lat", "lon", "variable"])
        else:
            df = new_df

        df = df.sort_values(["lat", "lon", "variable", "timestamp"]).reset_index(drop=True)
        return self.emit(df, "open_meteo_weather", urls=[ARCHIVE_URL],
                         note=f"{len(grid)} points, {start}..{end}")
