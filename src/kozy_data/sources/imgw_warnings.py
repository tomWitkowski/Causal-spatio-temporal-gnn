"""IMGW archived warnings (meteorological + hydrological).

Lists the public archive directories, downloads monthly/yearly archives within the
date window, and extracts warning records that reference powiat bielski
(TERYT ``2402`` / "bielski"). File formats vary across years, so parsing is
defensive: raw archives are always saved, and any line referencing the powiat is
captured with its source file and a best-effort date.

Output: timestamp, lat, lon, category, teryt, kind, content, source_file, source.
"""
from __future__ import annotations

import io as _io
import logging
import re
import zipfile
from urllib.parse import urljoin

import pandas as pd
from bs4 import BeautifulSoup

from ..base import BaseDownloader, FetchResult
from .. import http
from .. import io

log = logging.getLogger("kozy_data.imgw_warn")

DATE_RE = re.compile(r"(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})")
YEAR_RE = re.compile(r"(20\d{2})")


def list_links(base_url: str, suffixes: tuple[str, ...]) -> list[str]:
    """Parse an Apache-style autoindex page for matching file links."""
    html = http.get(base_url, use_cache=False, timeout=60).text
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a"):
        href = a.get("href", "")
        if href.lower().endswith(suffixes):
            out.append(urljoin(base_url.rstrip("/") + "/", href))
    return out


class ImgwWarningsDownloader(BaseDownloader):
    name = "imgw_warnings"
    license = "IMGW-PIB dane publiczne (free, attribution)"

    def _collect(self, base_url: str, kind: str, start_year: int) -> list[dict]:
        rows: list[dict] = []
        try:
            links = list_links(base_url, (".zip", ".csv", ".xml", ".txt"))
        except Exception as exc:  # noqa: BLE001
            log.warning("cannot list %s: %s", base_url, exc)
            return rows
        terc4 = self.aoi.teryt_powiat  # "2402"
        for url in links:
            ym = YEAR_RE.search(url)
            if ym and int(ym.group(1)) < start_year:
                continue
            try:
                content = http.get(url, timeout=120).content
            except Exception as exc:  # noqa: BLE001
                log.warning("download failed %s: %s", url, exc)
                continue
            fname = url.rsplit("/", 1)[-1]
            io.save_raw_bytes(self.name, f"{kind}_{fname}", content)
            members: list[tuple[str, bytes]] = []
            if fname.lower().endswith(".zip"):
                try:
                    with zipfile.ZipFile(_io.BytesIO(content)) as zf:
                        for n in zf.namelist():
                            members.append((n, zf.read(n)))
                except zipfile.BadZipFile:
                    continue
            else:
                members.append((fname, content))
            for mname, mbytes in members:
                try:
                    text = mbytes.decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    continue
                for line in text.splitlines():
                    if terc4 in line or "bielski" in line.lower():
                        dm = DATE_RE.search(line) or DATE_RE.search(mname) or DATE_RE.search(fname)
                        ts = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}" if dm else None
                        rows.append({
                            "timestamp": ts,
                            "lat": self.aoi.centroid_lat,
                            "lon": self.aoi.centroid_lon,
                            "category": "warning",
                            "kind": kind,
                            "teryt": terc4,
                            "content": line.strip()[:500],
                            "source_file": mname,
                            "source": self.name,
                        })
        return rows

    def run(self, since=None) -> FetchResult:
        start_year = self.date_window(since)[0].year
        meteo = self.cfg.get("base_meteo", "https://danepubliczne.imgw.pl/data/arch/ost_meteo")
        hydro = self.cfg.get("base_hydro", "https://danepubliczne.imgw.pl/data/arch/ost_hydro")
        rows = self._collect(meteo, "meteo", start_year)
        rows += self._collect(hydro, "hydro", start_year)
        if not rows:
            return FetchResult(self.name, 0, note="no warnings matched powiat bielski")
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        return self.emit(df, "imgw_warnings", urls=[meteo, hydro],
                         note="meteo+hydro, filtered to powiat bielski")
