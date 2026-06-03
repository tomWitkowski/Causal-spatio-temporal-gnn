"""RCB alerts -- best-effort scraper of gov.pl/web/rcb news.

RCB exposes no public API and no clean per-powiat history, so this collects
article links/titles/dates and keeps items referencing śląskie / bielski / Kozy.
Best used in forward-collection mode (run periodically).

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

log = logging.getLogger("kozy_data.rcb")

KEYWORDS = ("bielski", "bielsko", "śląski", "slaski", "kozy", "ostrzeż", "alert")
DATE_RE = re.compile(r"(20\d{2})[-.](\d{2})[-.](\d{2})")


class RcbAlertsDownloader(BaseDownloader):
    name = "rcb_alerts"
    license = "RCB / gov.pl (public information)"

    def run(self, since=None) -> FetchResult:
        base = self.cfg.get("base", "https://www.gov.pl/web/rcb")
        try:
            html = http.get(base, use_cache=False, timeout=60).text
        except Exception as exc:  # noqa: BLE001
            return FetchResult(self.name, 0, note=f"fetch failed: {exc}")
        io.save_raw_bytes(self.name, "rcb_index.html", html.encode("utf-8"))
        soup = BeautifulSoup(html, "html.parser")
        rows = []
        for a in soup.find_all("a", href=True):
            title = a.get_text(strip=True)
            href = a["href"]
            text = f"{title} {href}".lower()
            if not title or not any(k in text for k in KEYWORDS):
                continue
            dm = DATE_RE.search(href) or DATE_RE.search(title)
            ts = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}" if dm else None
            rows.append({
                "timestamp": ts,
                "lat": self.aoi.centroid_lat, "lon": self.aoi.centroid_lon,
                "category": "rcb_alert", "title": title[:300],
                "url": urljoin(base, href), "source": self.name,
            })
        if not rows:
            return FetchResult(self.name, 0, note="no matching RCB items on index page")
        df = pd.DataFrame(rows).drop_duplicates(subset=["url"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        return self.emit(df, "rcb_alerts", urls=[base],
                         note="best-effort scrape; run periodically to accumulate")
