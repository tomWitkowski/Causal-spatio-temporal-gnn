"""IMGW hydrological warnings & forecasts for the Kozy region (with history).

Two machine-readable feeds, kept in one long-format table:

* ``hydro_warning`` -- official hydrological warnings (ostrzeżenia hydrologiczne).
  History from the public archive of text bulletins (one zip per month, since
  2017): danepubliczne.imgw.pl/data/arch/ost_hydro/<year>/<MM>.zip
* ``hydro_forecast`` -- forecast of dangerous hydrological phenomena (PNZH),
  live JSON only (no archive), accumulated across runs.

Kept to the upper-Vistula / Beskidy / śląskie area that drains through Kozy
(Soła, Biała, Wapienica). Meteorological warnings are handled by ``meteoalarm``;
their archive and the road-hazard forecast (ZUK) are published only as PDFs and
are not ingested here.

Output: timestamp, valid_from, valid_to, lat, lon, warn_type, level, event,
        probability, area, description, identifier, source.
"""
from __future__ import annotations

import datetime as dt
import io as _io
import logging
import re
import zipfile

import pandas as pd
from bs4 import BeautifulSoup

from ..base import BaseDownloader, FetchResult
from ..config import PROCESSED_DIR
from .. import http

log = logging.getLogger("kozy_data.imgw_warnings")

ARCHIVE_BASE = "https://danepubliczne.imgw.pl/data/arch/ost_hydro/"
PNZH_URL = "https://meteo.imgw.pl/data/pnzh.json"
_TABLE = "imgw_warnings"
_PARQUET = PROCESSED_DIR / f"{_TABLE}.parquet"
_COLUMNS = ["timestamp", "valid_from", "valid_to", "lat", "lon", "warn_type",
            "level", "event", "probability", "area", "description",
            "identifier", "source"]

# Upper-Vistula / Beskidy / śląskie markers — the basins draining through Kozy.
_REGION_KEYWORDS = (
    "śląsk", "slask", "soł", "sol", "górnej wisły", "gornej wisly",
    "małej wisły", "malej wisly", "dopływów wisły", "doplywow wisly",
    "biał", "beskid", "bielsk", "skawa",
)

_DT_ISSUED = re.compile(r"wydania:\s*(\d{2})\.(\d{2})\.(\d{4})\s*-\s*godz\.\s*(\d{2}):(\d{2})")
_VALID = re.compile(
    r"Ważność:\s*od\s*godz\.\s*(\d{2}):(\d{2})\s*dnia\s*(\d{2})\.(\d{2})\.(\d{4})"
    r"\s*do\s*godz\.\s*(\d{2}):(\d{2})\s*dnia\s*(\d{2})\.(\d{2})\.(\d{4})")
_EVENT = re.compile(r"Zjawisko:\s*(.+)")
_LEVEL = re.compile(r"Stopień:\s*(-?\d+)")
_AREA = re.compile(r"Obszar:\s*(.+)")
_PRZEBIEG = re.compile(r"Przebieg:\s*(.+)")
_PROB = re.compile(r"Prawdopodobieństwo[^:]*:\s*(\d+)\s*%")


def _is_relevant(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in _REGION_KEYWORDS)


def _ts(d: str, m: str, y: str, hh: str, mm: str) -> pd.Timestamp:
    return pd.Timestamp(int(y), int(m), int(d), int(hh), int(mm), tz="UTC")


def _parse_bulletin(text: str, identifier: str, lat: float, lon: float) -> dict | None:
    """Parse one ost_hydro TXT bulletin into a normalized row (or None)."""
    if not _is_relevant(text):
        return None
    issued = _DT_ISSUED.search(text)
    valid = _VALID.search(text)
    event = _EVENT.search(text)
    level = _LEVEL.search(text)
    area = _AREA.search(text)
    przebieg = _PRZEBIEG.search(text)
    prob = _PROB.search(text)
    cancelled = "Odwołanie" in text
    return {
        "timestamp": _ts(*issued.groups()) if issued else pd.NaT,
        "valid_from": _ts(valid.group(3), valid.group(4), valid.group(5),
                          valid.group(1), valid.group(2)) if valid else pd.NaT,
        "valid_to": _ts(valid.group(8), valid.group(9), valid.group(10),
                        valid.group(6), valid.group(7)) if valid else pd.NaT,
        "lat": lat, "lon": lon,
        "warn_type": "hydro_warning",
        "level": "-1" if cancelled else (level.group(1) if level else None),
        "event": ("odwołanie: " if cancelled else "") + (event.group(1).strip() if event else ""),
        "probability": float(prob.group(1)) if prob else None,
        "area": area.group(1).strip()[:300] if area else None,
        "description": przebieg.group(1).strip()[:500] if przebieg else None,
        "identifier": identifier,
        "source": "imgw_warnings",
    }


def _list_links(url: str, suffix: str) -> list[str]:
    html = http.get(url, use_cache=False, timeout=60).text
    soup = BeautifulSoup(html, "html.parser")
    return [a["href"] for a in soup.find_all("a", href=True)
            if a["href"].endswith(suffix) and not a["href"].startswith(("?", "/"))]


class ImgwWarningsDownloader(BaseDownloader):
    name = "imgw_warnings"
    license = "IMGW-PIB dane publiczne (free, attribution)"

    def _hydro_history(self, start_year: int, lat: float, lon: float) -> list[dict]:
        rows: list[dict] = []
        today = dt.date.today()
        try:
            year_dirs = _list_links(ARCHIVE_BASE, "/")
        except Exception as exc:  # noqa: BLE001
            log.warning("ost_hydro listing failed: %s", exc)
            return rows
        for ydir in year_dirs:
            year = int(ydir.rstrip("/"))
            if year < start_year:
                continue
            year_url = f"{ARCHIVE_BASE}{ydir}"
            try:
                months = _list_links(year_url, ".zip")
            except Exception as exc:  # noqa: BLE001
                log.warning("ost_hydro %d listing failed: %s", year, exc)
                continue
            for mzip in months:
                month = int(mzip.split(".")[0])
                # Past months are immutable (cacheable); refetch the current month.
                fresh = (year == today.year and month >= today.month)
                try:
                    content = http.get(f"{year_url}{mzip}", timeout=180,
                                       use_cache=not fresh).content
                except Exception as exc:  # noqa: BLE001
                    log.warning("download failed %s%s: %s", year_url, mzip, exc)
                    continue
                rows.extend(self._parse_zip(content, lat, lon))
            log.info("imgw_warnings: parsed hydro archive %d (%d rows so far)", year, len(rows))
        return rows

    @staticmethod
    def _parse_zip(content: bytes, lat: float, lon: float) -> list[dict]:
        rows: list[dict] = []
        try:
            zf = zipfile.ZipFile(_io.BytesIO(content))
        except zipfile.BadZipFile:
            return rows
        for member in zf.namelist():
            if not member.upper().endswith(".TXT"):
                continue
            text = zf.read(member).decode("utf-8", errors="replace")
            row = _parse_bulletin(text, member.rsplit(".", 1)[0], lat, lon)
            if row is not None:
                rows.append(row)
        return rows

    def _hydro_forecast(self, lat: float, lon: float) -> list[dict]:
        try:
            data = http.get_json(PNZH_URL, use_cache=False, timeout=30)
        except Exception as exc:  # noqa: BLE001
            log.warning("pnzh fetch failed: %s", exc)
            return []
        rows = []
        for f in data:
            if not any(p.get("name") == "śląskie" for p in f.get("provinces", [])):
                continue
            rows.append({
                "timestamp": pd.to_datetime(f.get("released"), format="%Y%m%d%H%M%S",
                                            utc=True, errors="coerce"),
                "valid_from": pd.to_datetime(f.get("from"), format="%Y%m%d%H%M%S",
                                             utc=True, errors="coerce"),
                "valid_to": pd.to_datetime(f.get("to"), format="%Y%m%d%H%M%S",
                                           utc=True, errors="coerce"),
                "lat": lat, "lon": lon,
                "warn_type": "hydro_forecast",
                "level": str(f.get("degree")),
                "event": f.get("event", ""),
                "probability": float(f["prob"]) if f.get("prob") is not None else None,
                "area": "śląskie",
                "description": (f.get("area_description") or "")[:500],
                "identifier": f"pnzh_{f.get('no')}_{f.get('released')}",
                "source": "imgw_warnings",
            })
        return rows

    def run(self, since=None) -> FetchResult:
        lat, lon = self.aoi.centroid_lat, self.aoi.centroid_lon
        existing = pd.read_parquet(_PARQUET) if _PARQUET.exists() else None

        if since is not None:
            start_year = self.date_window(since)[0].year
        elif existing is not None and len(existing):
            issued = pd.to_datetime(existing["timestamp"], utc=True, errors="coerce")
            start_year = max(self.default_start().year, issued.max().year)
        else:
            start_year = self.default_start().year

        rows = self._hydro_history(start_year, lat, lon)
        rows.extend(self._hydro_forecast(lat, lon))
        log.info("imgw_warnings: %d region rows (warnings since %d + forecast)",
                 len(rows), start_year)

        if not rows and existing is None:
            return FetchResult(self.name, 0, note="no region-relevant warnings found")

        df = pd.DataFrame(rows, columns=_COLUMNS) if rows else pd.DataFrame(columns=_COLUMNS)
        if existing is not None and len(existing):
            df = pd.concat([existing, df], ignore_index=True)
        df = df.drop_duplicates(subset=["identifier"]).sort_values(
            "timestamp", ascending=False).reset_index(drop=True)

        return self.emit(df, _TABLE, urls=[ARCHIVE_BASE, PNZH_URL],
                         note=f"{len(df)} hydro warnings+forecasts (from {start_year})")
