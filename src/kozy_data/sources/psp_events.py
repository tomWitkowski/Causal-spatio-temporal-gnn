"""PSP / SWD-ST fire-service event statistics (powiat-level, yearly).

Scaffold. Source: dane.gov.pl open datasets "Statystyki zdarzeń SWD PSP"
(per-year datasets, e.g. dataset id 2080 for 2020) -- XLSX/CSV tables of fires and
local hazards broken down by voivodeship and powiat. Filter to powiat bielski
(TERYT 2402) / woj. śląskie.

To implement: query the dane.gov.pl CKAN API for the SWD PSP datasets, download
the powiat-breakdown resources per year, filter to bielski, and emit yearly counts
(timestamp = year, lat/lon = gmina centroid, category = event type, value = count).
"""
from __future__ import annotations

from ..base import BaseDownloader, FetchResult

DANE_GOV_API = "https://api.dane.gov.pl/1.4"


class PspEventsDownloader(BaseDownloader):
    name = "psp_events"
    license = "KG PSP / dane.gov.pl (open data)"

    def run(self, since=None) -> FetchResult:
        return FetchResult(self.name, 0,
                           note="scaffold -- enable & pull SWD PSP yearly powiat "
                                "tables from dane.gov.pl, filter TERYT 2402")
