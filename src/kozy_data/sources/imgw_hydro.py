"""IMGW hydrological daily data (water level / discharge) for nearby gauges.

Scaffold. Archive tree (analogous to the meteo crawler in ``imgw_meteo.py``):
  https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne/
      dane_hydrologiczne/dobowe/codz/<year>/codz_<MM>_<YYYY>.zip
Daily 'codz' CSV (cp1250, headerless): station code, name, hydro-year, month,
day, water level [cm], discharge [m3/s], water temperature [C].

Nearest gauges to Kozy sit on the Soła and Biała Białska. To implement: reuse the
crawl/parse pattern from ``ImgwMeteoDownloader`` against the hydro path and map the
gauge columns to long format (timestamp, lat, lon, station, variable, value).
"""
from __future__ import annotations

from ..base import BaseDownloader, FetchResult


class ImgwHydroDownloader(BaseDownloader):
    name = "imgw_hydro"
    license = "IMGW-PIB dane publiczne (free, attribution)"

    def run(self, since=None) -> FetchResult:
        return FetchResult(self.name, 0,
                           note="scaffold -- enable & implement crawl of "
                                "dane_hydrologiczne/dobowe/codz (see module docstring)")
