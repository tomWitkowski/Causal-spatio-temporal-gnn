"""Tauron Dystrybucja power outages (planned + failures) for the gmina.

Scaffold, forward-collection only (no public history). Source:
  https://www.tauron-dystrybucja.pl/wylaczenia -- outage search by
  voivodeship/city/street; backed by JSON endpoints used by the page.

To implement: call the outage search backend for woj. śląskie + "Kozy", parse the
returned outage windows, and emit (start, end, lat, lon=centroid, category=
planned|failure, area, source). Run on a schedule to accumulate a time series.
"""
from __future__ import annotations

from ..base import BaseDownloader, FetchResult


class TauronOutagesDownloader(BaseDownloader):
    name = "tauron_outages"
    license = "Tauron Dystrybucja (public outage notices)"

    def run(self, since=None) -> FetchResult:
        return FetchResult(self.name, 0,
                           note="scaffold -- enable & poll Tauron outage search for "
                                "Kozy; forward-collection only")
