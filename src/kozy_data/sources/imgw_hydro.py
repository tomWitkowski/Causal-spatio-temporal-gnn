"""IMGW hydrological daily data (water level / discharge) for nearby gauges.

Archive: https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne/
         dane_hydrologiczne/dobowe/<year>/codz_<year>.zip  (one zip per year)

CSV (utf-8, semicolon-separated, headerless):
  0: station_code  1: station_name  2: river_name  3: year  4: month  5: day
  6: water_level_cm  7: discharge_m3s  8: water_temp_c  (99.9 / 9999 = missing)

Nearest gauges to Kozy (Soła/Biała/Wapienica/Iłownica rivers).

Output: timestamp, lat, lon, station, variable, value, unit, source.
"""
from __future__ import annotations

import io as _io
import logging
import zipfile
from urllib.parse import urljoin

import pandas as pd
from bs4 import BeautifulSoup

from ..base import BaseDownloader, FetchResult
from ..config import PROCESSED_DIR
from .. import http, io

log = logging.getLogger("kozy_data.imgw_hydro")

BASE_URL = ("https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne"
            "/dane_hydrologiczne/dobowe/")

# Coords from IMGW station catalogs (lat, lon)
STATION_COORDS: dict[str, tuple[float, float]] = {
    "PODKĘPIE":              (49.877, 19.057),   # Wapienica ~7 km
    "MIKUSZOWICE":           (49.863, 19.012),   # Biała ~6 km
    "CZECHOWICE-DZIEDZICE":  (50.000, 18.917),   # Iłownica ~20 km
    "CZECHOWICE-BESTWINA":   (49.997, 19.017),   # Biała ~18 km
    "CZANIEC-KOBIERNICE":    (49.972, 19.130),   # Soła ~15 km
    "ŻYWIEC":                (49.685, 19.193),   # Soła ~18 km
}

HYDRO_COLS = [
    (6, "water_level_cm", "cm"),
    (7, "discharge_m3s",  "m3/s"),
    (8, "water_temp_c",   "C"),
]
_MISSING = {9999.0, 99.9, 9999}
_TABLE = "imgw_hydro_daily"
_PARQUET = PROCESSED_DIR / f"{_TABLE}.parquet"


def _year_urls(start_year: int) -> list[tuple[int, str]]:
    """Return [(year, zip_url), ...] for years >= start_year.

    Older years have monthly zips (codz_YYYY_MM.zip); newer ones are annual (codz_YYYY.zip).
    We list each year directory and return whatever zip(s) are there.
    """
    html = http.get(BASE_URL, use_cache=False, timeout=60).text
    soup = BeautifulSoup(html, "html.parser")
    result = []
    for a in soup.find_all("a", href=True):
        href = a["href"].rstrip("/")
        if not href.isdigit():
            continue
        year = int(href)
        if year < start_year:
            continue
        year_url = urljoin(BASE_URL, f"{year}/")
        try:
            year_html = http.get(year_url, use_cache=False, timeout=30).text
        except Exception:
            continue
        year_soup = BeautifulSoup(year_html, "html.parser")
        for za in year_soup.find_all("a", href=True):
            zhref = za["href"]
            if "codz" in zhref.lower() and zhref.endswith(".zip"):
                result.append((year, urljoin(year_url, zhref)))
    return result


def _parse_zip(content: bytes, stations: set[str]) -> list[dict]:
    rows: list[dict] = []
    try:
        zf = zipfile.ZipFile(_io.BytesIO(content))
    except zipfile.BadZipFile:
        return rows
    for member in zf.namelist():
        if not member.lower().endswith(".csv"):
            continue
        raw = zf.read(member)
        df = None
        for enc in ("utf-8", "cp1250", "latin-1"):
            try:
                text = raw.decode(enc)
            except Exception:
                continue
            # Some years wrap each entire row in outer quotes ("row..."); unwrap.
            lines = []
            for line in text.splitlines():
                line = line.strip()
                if line.startswith('"') and line.endswith('"'):
                    line = line[1:-1].replace('""', '"')
                lines.append(line)
            cleaned = "\n".join(lines)
            for sep in (";", ","):
                try:
                    candidate = pd.read_csv(
                        _io.StringIO(cleaned), header=None,
                        sep=sep, dtype=str, engine="python")
                    if len(candidate.columns) >= 9:
                        df = candidate
                        break
                except Exception:
                    continue
            if df is not None:
                break
        if df is None:
            log.debug("parse fail %s: no valid encoding/separator found", member)
            continue
        for _, r in df.iterrows():
            station = str(r.iloc[1]).strip().upper()
            if station not in stations:
                continue
            try:
                ts = pd.Timestamp(year=int(r.iloc[3]), month=int(r.iloc[4]),
                                  day=int(r.iloc[5]), tz="UTC")
            except Exception:
                continue
            lat, lon = STATION_COORDS.get(station, (None, None))
            for idx, var, unit in HYDRO_COLS:
                if idx >= len(r):
                    continue
                val = pd.to_numeric(str(r.iloc[idx]).replace(",", "."), errors="coerce")
                if pd.isna(val) or val in _MISSING:
                    continue
                rows.append({"timestamp": ts, "lat": lat, "lon": lon,
                             "station": station, "variable": var,
                             "value": float(val), "unit": unit,
                             "source": "imgw_hydro"})
    return rows


class ImgwHydroDownloader(BaseDownloader):
    name = "imgw_hydro"
    license = "IMGW-PIB dane publiczne (free, attribution)"

    def run(self, since=None) -> FetchResult:
        stations = {s.upper() for s in self.cfg.get(
            "station_names", list(STATION_COORDS))}

        start_year = self.default_start().year
        existing = None
        if since is not None:
            start_year = self.date_window(since)[0].year
        elif _PARQUET.exists():
            existing = pd.read_parquet(_PARQUET)
            if len(existing):
                max_ts = pd.to_datetime(existing["timestamp"], utc=True).max()
                start_year = max(self.default_start().year, max_ts.year)

        try:
            year_urls = _year_urls(start_year)
        except Exception as exc:
            return FetchResult(self.name, 0, note=f"archive listing failed: {exc}")

        log.info("imgw_hydro: %d year(s) from %d for %s",
                 len(year_urls), start_year, sorted(stations))

        rows: list[dict] = []
        for year, url in year_urls:
            log.info("  downloading %d: %s", year, url)
            try:
                content = http.get(url, timeout=180).content
            except Exception as exc:
                log.warning("download failed %s: %s", url, exc)
                continue
            io.save_raw_bytes(self.name, f"codz_{year}.zip", content)
            rows.extend(_parse_zip(content, stations))

        if not rows and existing is None:
            return FetchResult(self.name, 0, note="no rows for configured stations")

        df = pd.DataFrame(rows) if rows else pd.DataFrame()
        if existing is not None and len(existing):
            df = pd.concat([existing, df], ignore_index=True)
        df = df.drop_duplicates(subset=["timestamp", "station", "variable"])
        df = df.sort_values(["station", "variable", "timestamp"]).reset_index(drop=True)

        return self.emit(df, _TABLE, urls=[BASE_URL],
                         note=f"stations={sorted(stations)}, from {start_year}")
