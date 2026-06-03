"""GIOŚ air-quality: nearest stations + measurements via the public REST API.

The live API serves recent measurements (good for forward collection and station
geo-metadata). Full history back to 2020 is in the GIOŚ "Bank danych pomiarowych"
yearly archive files -- see docs/DATA_SOURCES.md (#6) for the archive route.

Long-format output: timestamp, lat, lon, station_id, station, variable, value, source.
"""
from __future__ import annotations

import logging

import pandas as pd

from ..base import BaseDownloader, FetchResult
from ..geo import haversine_km
from .. import http

log = logging.getLogger("kozy_data.gios")

# Legacy REST API (stable, simple JSON). v1 exists at /v1/rest/... with Polish keys.
BASE = "https://api.gios.gov.pl/pjp-api/rest"


class GiosAirDownloader(BaseDownloader):
    name = "gios_air"
    license = "GIOŚ Państwowy Monitoring Środowiska (open, attribution)"

    def _nearest_stations(self) -> list[dict]:
        stations = http.get_json(f"{BASE}/station/findAll", timeout=120)
        radius = float(self.cfg.get("search_radius_km", 30))
        max_n = int(self.cfg.get("max_stations", 3))
        scored = []
        for st in stations:
            try:
                lat = float(st["gegrLat"]); lon = float(st["gegrLon"])
            except (KeyError, TypeError, ValueError):
                continue
            d = haversine_km(lat, lon, self.aoi.centroid_lat, self.aoi.centroid_lon)
            if d <= radius:
                scored.append((d, lat, lon, st))
        scored.sort(key=lambda x: x[0])
        out = []
        for d, lat, lon, st in scored[:max_n]:
            out.append({"id": st["id"], "name": st.get("stationName"),
                        "lat": lat, "lon": lon, "dist_km": round(d, 2)})
        return out

    def run(self, since=None) -> FetchResult:
        stations = self._nearest_stations()
        if not stations:
            return FetchResult(self.name, 0, note="no stations within radius")
        rows = []
        for st in stations:
            sensors = http.get_json(f"{BASE}/station/sensors/{st['id']}", timeout=60)
            for sensor in sensors:
                sid = sensor["id"]
                code = sensor.get("param", {}).get("paramCode")
                data = http.get_json(f"{BASE}/data/getData/{sid}", timeout=60,
                                     use_cache=False)
                for v in data.get("values", []):
                    if v.get("value") is None:
                        continue
                    rows.append({
                        "timestamp": v["date"],
                        "lat": st["lat"], "lon": st["lon"],
                        "station_id": st["id"], "station": st["name"],
                        "variable": code, "value": v["value"],
                        "unit": "ug/m3", "source": self.name,
                    })
        if not rows:
            return FetchResult(self.name, 0, note="no measurements returned")
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.dropna(subset=["timestamp"])
        return self.emit(df, "gios_air_measurements", urls=[BASE],
                         note=f"{len(stations)} stations (recent API window)")
