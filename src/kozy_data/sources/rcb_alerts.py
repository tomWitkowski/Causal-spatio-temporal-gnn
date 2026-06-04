"""RCB alerts -- best-effort scraper of gov.pl/web/rcb news.

RCB exposes no public API. The index page lists recent alerts in <li> elements
with dates in DD.MM.YYYY format preceding the title. Items are filtered to those
referencing śląskie / bielski / weather events.

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

KEYWORDS = ("bielski", "bielsko", "śląski", "slaski", "kozy", "ostrzeż", "alert", "burz",
            "wiatr", "powódź", "pożar", "upał", "mróz", "śnieg")
# RCB dates appear as DD.MM.YYYY at the start of <li> item text
DATE_RE_PL = re.compile(r"(\d{2})\.(\d{2})\.(20\d{2})")
# Fallback ISO pattern for future-proofing
DATE_RE_ISO = re.compile(r"(20\d{2})[-.](\d{2})[-.](\d{2})")


class RcbAlertsDownloader(BaseDownloader):
    name = "rcb_alerts"
    license = "RCB / gov.pl (public information)"

    def run(self, since=None) -> FetchResult:
        base = self.cfg.get("base", "https://www.gov.pl/web/rcb")
        try:
            html = http.get(base, use_cache=False, timeout=60).text
        except Exception as exc:
            return FetchResult(self.name, 0, note=f"fetch failed: {exc}")
        io.save_raw_bytes(self.name, "rcb_index.html", html.encode("utf-8"))
        soup = BeautifulSoup(html, "html.parser")
        rows = []
        seen_urls: set[str] = set()
        for li in soup.find_all("li"):
            a = li.find("a", href=True)
            if not a:
                continue
            title = a.get_text(strip=True)
            href = a["href"]
            if not title or not href:
                continue
            text = li.get_text(" ", strip=True)
            text_lower = f"{title} {href} {text}".lower()
            if not any(k in text_lower for k in KEYWORDS):
                continue
            # Extract date: prefer DD.MM.YYYY from item text (RCB page format)
            dm = DATE_RE_PL.search(text)
            if dm:
                ts = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"
            else:
                dm2 = DATE_RE_ISO.search(href) or DATE_RE_ISO.search(title)
                ts = f"{dm2.group(1)}-{dm2.group(2)}-{dm2.group(3)}" if dm2 else None
            if not ts:
                continue
            url = urljoin(base, href)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            rows.append({
                "timestamp": ts,
                "lat": self.aoi.centroid_lat, "lon": self.aoi.centroid_lon,
                "category": "rcb_alert", "title": title[:300],
                "url": url, "source": self.name,
            })
        if not rows:
            return FetchResult(self.name, 0, note="no matching RCB items on index page")
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        return self.emit(df, "rcb_alerts", urls=[base],
                         note="best-effort scrape; run periodically to accumulate")
