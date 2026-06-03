"""Gmina Kozy news/announcements scraper (kozy.pl/aktualnosci).

Local events with dates -- useful as discrete spatio-temporal events anchored to
the gmina centroid. Best-effort HTML parsing (site layout may change).

Output: timestamp, lat, lon, category, title, url, source.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

import pandas as pd
from bs4 import BeautifulSoup

from ..base import BaseDownloader, FetchResult
from .. import http, io

log = logging.getLogger("kozy_data.kozy_news")

DATE_RE = re.compile(r"(\d{2})[-./](\d{2})[-./](20\d{2})")
ISO_RE = re.compile(r"(20\d{2})[-./](\d{2})[-./](\d{2})")


class KozyNewsDownloader(BaseDownloader):
    name = "kozy_news"
    license = "kozy.pl (public municipal information)"

    @staticmethod
    def _parse_date(text: str) -> str | None:
        m = ISO_RE.search(text)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        m = DATE_RE.search(text)
        if m:
            return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        return None

    def run(self, since=None) -> FetchResult:
        base = self.cfg.get("base", "https://kozy.pl/aktualnosci/")
        try:
            html = http.get(base, use_cache=False, timeout=60).text
        except Exception as exc:  # noqa: BLE001
            return FetchResult(self.name, 0, note=f"fetch failed: {exc}")
        io.save_raw_bytes(self.name, "kozy_aktualnosci.html", html.encode("utf-8"))
        soup = BeautifulSoup(html, "html.parser")
        rows = []
        for art in soup.find_all(["article", "li", "div"]):
            link = art.find("a", href=True)
            if not link:
                continue
            title = link.get_text(strip=True)
            if not title or len(title) < 8:
                continue
            ts = self._parse_date(art.get_text(" ", strip=True))
            rows.append({
                "timestamp": ts,
                "lat": self.aoi.centroid_lat, "lon": self.aoi.centroid_lon,
                "category": "municipal_news", "title": title[:300],
                "url": urljoin(base, link["href"]), "source": self.name,
            })
        if not rows:
            return FetchResult(self.name, 0, note="no items parsed (check site layout)")
        df = (pd.DataFrame(rows).drop_duplicates(subset=["url"])
              .reset_index(drop=True))
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        return self.emit(df, "kozy_news", urls=[base],
                         note="best-effort scrape of municipal news")
