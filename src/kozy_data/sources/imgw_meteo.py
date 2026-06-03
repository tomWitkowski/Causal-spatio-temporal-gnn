"""IMGW measurement-observation data (daily 'klimat' files) for nearby stations.

Crawls the public archive tree, downloads monthly zips from ``start`` onward,
parses the daily climate CSV (cp1250, headerless) and keeps rows for the
configured stations. Emits a long-format daily series.

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
from .. import http
from .. import io

log = logging.getLogger("kozy_data.imgw_meteo")

# Daily 'klimat' (k_d) column layout -> (0-based index, variable, unit).
KD_COLUMNS = [
    (5, "tmax_c", "C"),
    (7, "tmin_c", "C"),
    (9, "tavg_c", "C"),
    (11, "tmin_ground_c", "C"),
    (13, "precip_mm", "mm"),
    (16, "snow_cm", "cm"),
]
# Approx station coordinates (lat, lon) for geo-referencing the series.
STATION_COORDS = {
    "BIELSKO-BIAŁA": (49.807, 19.002),
    "PSZCZYNA": (49.983, 18.945),
    "ŻYWIEC": (49.685, 19.193),
}


def _links(url: str, suffixes: tuple[str, ...] | None = None) -> list[str]:
    html = http.get(url, use_cache=False, timeout=60).text
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a"):
        href = a.get("href", "")
        if href in ("../", "/") or href.startswith("?"):
            continue
        full = urljoin(url.rstrip("/") + "/", href)
        if suffixes is None or href.lower().endswith(suffixes) or href.endswith("/"):
            out.append(full)
    return out


class ImgwMeteoDownloader(BaseDownloader):
    name = "imgw_meteo"
    license = "IMGW-PIB dane publiczne (free, attribution)"

    def _zip_urls(self, start_year: int) -> list[str]:
        base = self.cfg.get("base", "https://danepubliczne.imgw.pl/data/"
                            "dane_pomiarowo_obserwacyjne")
        kind = self.cfg.get("kind", "klimat")
        root = f"{base}/dane_meteorologiczne/dobowe/{kind}/"
        zips: list[str] = []
        for link in _links(root):
            if link.lower().endswith(".zip"):
                if any(str(y) in link for y in range(start_year, 2100)):
                    zips.append(link)
            elif link.endswith("/"):
                year = link.rstrip("/").rsplit("/", 1)[-1]
                if year.isdigit() and int(year) >= start_year:
                    zips.extend(l for l in _links(link, (".zip",))
                                if l.lower().endswith(".zip"))
        return zips

    def _parse_zip(self, content: bytes, stations: set[str]) -> list[dict]:
        rows: list[dict] = []
        try:
            zf = zipfile.ZipFile(_io.BytesIO(content))
        except zipfile.BadZipFile:
            return rows
        for member in zf.namelist():
            base = member.lower()
            if "k_d_" not in base or "k_d_t" in base or not base.endswith(".csv"):
                continue
            try:
                df = pd.read_csv(_io.BytesIO(zf.read(member)), header=None,
                                 encoding="cp1250", sep=",", dtype=str,
                                 engine="python")
            except Exception as exc:  # noqa: BLE001
                log.debug("parse fail %s: %s", member, exc)
                continue
            for _, r in df.iterrows():
                station = str(r.get(1, "")).strip().upper()
                if station not in stations:
                    continue
                try:
                    y, m, d = int(r[2]), int(r[3]), int(r[4])
                    ts = pd.Timestamp(year=y, month=m, day=d, tz="UTC")
                except Exception:  # noqa: BLE001
                    continue
                lat, lon = STATION_COORDS.get(station, (None, None))
                for idx, var, unit in KD_COLUMNS:
                    if idx >= len(r):
                        continue
                    val = pd.to_numeric(str(r[idx]).replace(",", "."), errors="coerce")
                    if pd.isna(val):
                        continue
                    rows.append({"timestamp": ts, "lat": lat, "lon": lon,
                                 "station": station, "variable": var,
                                 "value": float(val), "unit": unit,
                                 "source": self.name})
        return rows

    def run(self, since=None) -> FetchResult:
        start_year = self.date_window(since)[0].year
        stations = {s.upper() for s in self.cfg.get("station_names",
                                                    list(STATION_COORDS))}
        try:
            zip_urls = self._zip_urls(start_year)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(self.name, 0, note=f"archive listing failed: {exc}")
        rows: list[dict] = []
        for url in zip_urls:
            try:
                content = http.get(url, timeout=180).content
            except Exception as exc:  # noqa: BLE001
                log.warning("download failed %s: %s", url, exc)
                continue
            io.save_raw_bytes(self.name, url.rsplit("/", 1)[-1], content)
            rows.extend(self._parse_zip(content, stations))
        if not rows:
            return FetchResult(self.name, 0, note="no rows for configured stations")
        df = pd.DataFrame(rows).drop_duplicates(
            subset=["timestamp", "station", "variable"])
        return self.emit(df, "imgw_meteo_daily", urls=zip_urls[:5],
                         note=f"klimat daily, stations={sorted(stations)}")
