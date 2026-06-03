"""GUGiK reference geodata: PRG boundary, BDOT10k objects, NMT/DEM.

Scaffold (needs the optional ``geo`` extra: geopandas + owslib). Sources:
  * PRG administrative boundaries -- WFS via integracja.gugik.gov.pl.
  * BDOT10k topographic objects -- per-powiat GML download services.
  * NMT/NMPT terrain models + orthophoto -- GUGiK download services (GeoTIFF).

To implement: read the PRG WFS filtered to TERYT 2402072 with
``geopandas.read_file(WFS_URL)`` (richer/authoritative alternative to the OSM
boundary), and fetch BDOT10k for the powiat, clipping to the gmina geometry.
"""
from __future__ import annotations

from ..base import BaseDownloader, FetchResult


class GugikDownloader(BaseDownloader):
    name = "gugik"
    license = "GUGiK / PZGiK (open since 2020, attribution)"

    def run(self, since=None) -> FetchResult:
        return FetchResult(self.name, 0,
                           note="scaffold -- enable & read PRG/BDOT10k WFS (needs "
                                "'geo' extra: geopandas, owslib)")
