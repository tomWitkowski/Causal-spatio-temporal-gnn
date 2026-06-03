"""Open-Meteo historical (ERA5 reanalysis) hourly weather for the AOI centroid.

Free, no API key. Long-format output: timestamp, lat, lon, variable, value, unit.
"""
from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

from ..base import BaseDownloader, FetchResult
from .. import http

log = logging.getLogger("kozy_data.open_meteo")

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
# ERA5 archive lags real time by ~5 days.
LAG_DAYS = 6


class OpenMeteoDownloader(BaseDownloader):
    name = "open_meteo"
    license = "Open-Meteo / Copernicus ERA5 (CC-BY 4.0, attribution required)"

    def run(self, since=None) -> FetchResult:
        start, _ = self.date_window(since)
        end = dt.date.today() - dt.timedelta(days=LAG_DAYS)
        if end < start:
            end = start
        hourly = self.cfg.get("hourly") or ["temperature_2m", "precipitation",
                                            "wind_speed_10m"]
        lat, lon = self.aoi.centroid_lat, self.aoi.centroid_lon
        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "hourly": ",".join(hourly),
            "timezone": "UTC",
        }
        data = http.get_json(ARCHIVE_URL, params=params, timeout=120)
        block = data.get("hourly", {})
        units = data.get("hourly_units", {})
        times = block.get("time", [])
        frames = []
        for var in hourly:
            values = block.get(var)
            if not values:
                continue
            frames.append(pd.DataFrame({
                "timestamp": times,
                "variable": var,
                "value": values,
                "unit": units.get(var),
            }))
        if not frames:
            return FetchResult(self.name, 0, note="no hourly data returned")
        df = pd.concat(frames, ignore_index=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df["lat"] = lat
        df["lon"] = lon
        df["source"] = self.name
        df = df[["timestamp", "lat", "lon", "variable", "value", "unit", "source"]]
        return self.emit(df, "open_meteo_weather", urls=[ARCHIVE_URL],
                         note=f"hourly ERA5 @centroid {start}..{end}")
