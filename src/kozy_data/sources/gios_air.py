"""GIOŚ air-quality: nearest stations + measurements via the public REST API v1.

The live API serves recent measurements (good for forward collection and station
geo-metadata). Full history back to 2020 is in the GIOŚ "Bank danych pomiarowych"
yearly archive files.

Long-format output: timestamp, lat, lon, station_id, station, variable, value, unit, source.
"""
from __future__ import annotations

import logging

import pandas as pd

from ..base import BaseDownloader, FetchResult
from ..geo import haversine_km
from ..config import PROCESSED_DIR
from .. import http

log = logging.getLogger("kozy_data.gios")

BASE = "https://api.gios.gov.pl/pjp-api/v1/rest"

# GIOŚ getData is slow and flaky; use a fail-fast session (1 retry, short timeout)
# so a hanging sensor is skipped rather than retried for minutes. The source
# accumulates across runs, so a sensor missed this time is picked up next time.
_FAST = http.make_session(total_retries=1, backoff=0.5)
_SENSOR_TIMEOUT = 25

# v1 API uses Polish key names
_ST_ID = "Identyfikator stacji"
_ST_LAT = "WGS84 φ N"
_ST_LON = "WGS84 λ E"
_ST_NAME = "Nazwa stacji"
_SENSOR_ID = "Identyfikator stanowiska"
_SENSOR_CODE = "Wskaźnik - wzór"
_DATA_LIST = "Lista danych pomiarowych"
_DATA_DATE = "Data"
_DATA_VAL = "Wartość"


def _get_all_pages(url: str, list_key: str, **kwargs) -> list[dict]:
    """Fetch all pages from a paginated v1 endpoint."""
    out = []
    page = 0
    while True:
        params = {**kwargs.get("params", {}), "page": page, "size": 500}
        data = http.get_json(url, params=params, timeout=60)
        items = data.get(list_key, [])
        out.extend(items)
        if page >= data.get("totalPages", 1) - 1:
            break
        page += 1
    return out


class GiosAirDownloader(BaseDownloader):
    name = "gios_air"
    license = "GIOŚ Państwowy Monitoring Środowiska (open, attribution)"

    def _nearest_stations(self) -> list[dict]:
        all_stations = _get_all_pages(
            f"{BASE}/station/findAll",
            "Lista stacji pomiarowych",
        )
        radius = float(self.cfg.get("search_radius_km", 30))
        max_n = int(self.cfg.get("max_stations", 3))
        scored = []
        for st in all_stations:
            try:
                lat = float(st[_ST_LAT])
                lon = float(st[_ST_LON])
            except (KeyError, TypeError, ValueError):
                continue
            d = haversine_km(lat, lon, self.aoi.centroid_lat, self.aoi.centroid_lon)
            if d <= radius:
                scored.append((d, lat, lon, st))
        scored.sort(key=lambda x: x[0])
        out = []
        for d, lat, lon, st in scored[:max_n]:
            out.append({"id": st[_ST_ID], "name": st.get(_ST_NAME),
                        "lat": lat, "lon": lon, "dist_km": round(d, 2)})
        return out

    def _sensor_data(self, sensor_id: int) -> list[dict]:
        """Fetch recent measurements for one sensor; returns [] for manual sensors.

        A large page size collapses the API's recent window into a single request
        (the default page size is tiny and would otherwise mean hundreds of slow
        calls per sensor). The source accumulates across runs, so only the recent
        window is needed each time.
        """
        rows: list[dict] = []
        page = 0
        while True:
            try:
                data = http.get_json(f"{BASE}/data/getData/{sensor_id}",
                                     params={"size": 500, "page": page},
                                     use_cache=False, timeout=_SENSOR_TIMEOUT,
                                     session_=_FAST)
            except Exception as exc:
                # 400 = manual sensor (released with weeks' delay); skip silently
                log.debug("sensor %s page %d unavailable: %s", sensor_id, page, exc)
                break
            rows.extend(data.get(_DATA_LIST, []))
            if page >= data.get("totalPages", 1) - 1:
                break
            page += 1
        return rows

    def run(self, since=None) -> FetchResult:
        stations = self._nearest_stations()
        if not stations:
            return FetchResult(self.name, 0, note="no stations within radius")
        rows = []
        for st in stations:
            try:
                sensors = http.get_json(f"{BASE}/station/sensors/{st['id']}", timeout=60)
            except Exception as exc:
                log.warning("sensors fetch failed for station %s: %s", st["id"], exc)
                continue
            sensor_list = sensors.get("Lista stanowisk pomiarowych dla podanej stacji", [])
            for sensor in sensor_list:
                sid = sensor.get(_SENSOR_ID)
                code = sensor.get(_SENSOR_CODE)
                if not sid:
                    continue
                for v in self._sensor_data(sid):
                    val = v.get(_DATA_VAL)
                    if val is None:
                        continue
                    rows.append({
                        "timestamp": v.get(_DATA_DATE),
                        "lat": st["lat"], "lon": st["lon"],
                        "station_id": st["id"], "station": st["name"],
                        "variable": code, "value": val,
                        "unit": "ug/m3", "source": self.name,
                    })
        if not rows:
            return FetchResult(self.name, 0, note="no measurements returned")
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.dropna(subset=["timestamp"])

        existing_path = PROCESSED_DIR / "gios_air_measurements.parquet"
        if existing_path.exists():
            existing = pd.read_parquet(existing_path)
            df = pd.concat([existing, df], ignore_index=True)
            df = df.drop_duplicates(subset=["timestamp", "station_id", "variable"])

        df = df.sort_values(["station_id", "variable", "timestamp"]).reset_index(drop=True)
        return self.emit(df, "gios_air_measurements", urls=[BASE],
                         note=f"{len(stations)} stations (accumulated)")
