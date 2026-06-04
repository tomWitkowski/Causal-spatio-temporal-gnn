"""Gmina Kozy news/announcements via the official RSS feed (kozy.pl).

Parses the RSS feed for clean titles, publication dates, and URLs.
Falls back to HTML scraping of div.news__element if the RSS is unavailable.

Output: timestamp, lat, lon, category, title, url, source.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

import pandas as pd
from bs4 import BeautifulSoup

from ..base import BaseDownloader, FetchResult
from .. import http, io

log = logging.getLogger("kozy_data.kozy_news")

DATE_RE_DD = re.compile(r"(\d{2})\s*-\s*(\d{2})\s*-\s*(20\d{2})")


class KozyNewsDownloader(BaseDownloader):
    name = "kozy_news"
    license = "kozy.pl (public municipal information)"

    def _from_rss(self, rss_url: str) -> list[dict]:
        try:
            text = http.get(rss_url, use_cache=False, timeout=60).text
        except Exception as exc:
            log.warning("RSS fetch failed: %s", exc)
            return []
        io.save_raw_bytes(self.name, "kozy_aktualnosci.xml", text.encode("utf-8"))
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            log.warning("RSS parse failed: %s", exc)
            return []
        rows = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            if not title or not link:
                continue
            rows.append({
                "timestamp": pub_date or None,
                "lat": self.aoi.centroid_lat,
                "lon": self.aoi.centroid_lon,
                "category": "municipal_news",
                "title": title[:300],
                "url": link,
                "source": self.name,
            })
        return rows

    def _from_html(self, base: str) -> list[dict]:
        try:
            html = http.get(base, use_cache=False, timeout=60).text
        except Exception as exc:
            log.warning("HTML fetch failed: %s", exc)
            return []
        io.save_raw_bytes(self.name, "kozy_aktualnosci.html", html.encode("utf-8"))
        soup = BeautifulSoup(html, "html.parser")
        rows = []
        for el in soup.find_all("div", class_="news__element"):
            a = el.find("a", href=True)
            if not a:
                continue
            title = (a.get("title") or a.get_text(strip=True))
            title = re.sub(r"^Kliknij aby przejść do\s+", "", title).rstrip(".").strip()
            if not title or len(title) < 6:
                continue
            ts = None
            time_el = el.find("time", class_="news__date_text")
            if time_el:
                dt_attr = (time_el.get("datetime") or "").strip()
                if dt_attr:
                    ts = dt_attr[:10]
                else:
                    m = DATE_RE_DD.search(time_el.get_text())
                    if m:
                        ts = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            rows.append({
                "timestamp": ts,
                "lat": self.aoi.centroid_lat,
                "lon": self.aoi.centroid_lon,
                "category": "municipal_news",
                "title": title[:300],
                "url": urljoin(base, a["href"]),
                "source": self.name,
            })
        return rows

    def run(self, since=None) -> FetchResult:
        base = self.cfg.get("base", "https://kozy.pl/aktualnosci/")
        rss_url = self.cfg.get("rss", "https://kozy.pl/rss/aktualnosci.xml?all=true")
        rows = self._from_rss(rss_url)
        if not rows:
            log.info("RSS empty, falling back to HTML scraper")
            rows = self._from_html(base)
        if not rows:
            return FetchResult(self.name, 0, note="no items parsed (check site layout)")
        df = pd.DataFrame(rows).drop_duplicates(subset=["url"]).reset_index(drop=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        return self.emit(df, "kozy_news", urls=[rss_url],
                         note="RSS feed of municipal news")
