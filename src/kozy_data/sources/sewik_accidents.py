"""Road accidents (SEWIK) with coordinates, clipped to the AOI.

Scaffold. Sources:
  * Polish Road Safety Observatory (obserwatoriumbrd.pl) accident map -- table
    export (json/csv) with lat/lon, data from 2010+.
  * sewik.pl -- searchable SEWIK frontend, data from 2018-01-01.

To implement: query the POBR accident-map backend for the bbox/powiat, download
the table, then clip points with ``kozy_data.geo.filter_points_in_aoi`` and emit
(timestamp, lat, lon, category=severity, value, source).
"""
from __future__ import annotations

from ..base import BaseDownloader, FetchResult


class SewikAccidentsDownloader(BaseDownloader):
    name = "sewik_accidents"
    license = "POBR / Policja SEWIK (see portal terms)"

    def run(self, since=None) -> FetchResult:
        return FetchResult(self.name, 0,
                           note="scaffold -- enable & pull POBR accident table for "
                                "bbox, clip with geo.filter_points_in_aoi")
