"""RCB alerts -- best-effort scraper of gov.pl/web/rcb, filtered to gmina Kozy.

RCB exposes no public API. The "komunikaty" listing renders each alert as a
``<li>`` with a ``.title a`` link, a ``.event .date`` (DD.MM.YYYY) and an
``.intro`` blurb. RCB alerts are regional SMS warnings whose article body names
the affected voivodeship/powiat (e.g. "na terenie województwa śląskiego").

We keep only alerts relevant to Kozy: those whose title/intro or article body
mention śląskie / powiat bielski / Kozy and its neighbourhood (incl. roads).
No historical archive exists; results accumulate across runs.

Output: timestamp, lat, lon, category, title, description, url, source.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

import pandas as pd
from bs4 import BeautifulSoup

from ..base import BaseDownloader, FetchResult
from .. import http, io
from ..config import PROCESSED_DIR

log = logging.getLogger("kozy_data.rcb")

# Region terms identifying alerts relevant to Kozy (powiat bielski, śląskie).
REGION_KEYWORDS = (
    "śląsk", "slask", "bielski", "bielsko", "kozy", "żywiec", "zywiec",
    "pszczyn", "cieszyn", "beskid", "podbeskidzie", "czechowic",
)
# Phenomenon -> category, matched against the title (substring, lower-cased).
PHENOMENA = {
    "burz": "storm", "wiatr": "wind", "powodz": "flood", "powódz": "flood",
    "powó": "flood", "pożar": "fire", "pozar": "fire", "upał": "heat",
    "upal": "heat", "mróz": "frost", "mroz": "frost", "śnieg": "snow",
    "snieg": "snow", "oblodz": "ice", "gołoledz": "ice", "smog": "smog",
    "susza": "drought", "droga": "road", "drog": "road",
}
DATE_RE_PL = re.compile(r"(\d{2})\.(\d{2})\.(20\d{2})")
_PARQUET = PROCESSED_DIR / "rcb_alerts.parquet"


def _is_relevant(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in REGION_KEYWORDS)


def _category(title: str) -> str:
    t = title.lower()
    for token, cat in PHENOMENA.items():
        if token in t:
            return cat
    return "alert"


class RcbAlertsDownloader(BaseDownloader):
    name = "rcb_alerts"
    license = "RCB / gov.pl (public information)"

    def _article_relevant(self, url: str) -> bool:
        """Fetch the article body and check whether it names the Kozy region."""
        try:
            html = http.get(url, timeout=60).text
        except Exception as exc:  # noqa: BLE001
            log.debug("article fetch failed %s: %s", url, exc)
            return False
        return _is_relevant(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))

    def run(self, since=None) -> FetchResult:
        base = self.cfg.get("base", "https://www.gov.pl/web/rcb")
        rows: list[dict] = []
        seen_urls: set[str] = set()

        for page_url in [f"{base}/komunikaty", base]:
            try:
                html = http.get(page_url, use_cache=False, timeout=60).text
            except Exception as exc:  # noqa: BLE001
                log.warning("fetch failed %s: %s", page_url, exc)
                continue
            io.save_raw_bytes(self.name, f"rcb_{page_url.rsplit('/', 1)[-1]}.html",
                              html.encode("utf-8"))
            soup = BeautifulSoup(html, "html.parser")

            for li in soup.find_all("li"):
                a = li.select_one(".title a[href]")
                if not a:
                    continue
                title = a.get_text(strip=True)
                url = urljoin(base, a["href"])
                if not title or url in seen_urls:
                    continue

                date_el = li.select_one(".date")
                dm = DATE_RE_PL.search(date_el.get_text()) if date_el else None
                if not dm:
                    continue
                intro_el = li.select_one(".intro")
                intro = intro_el.get_text(" ", strip=True) if intro_el else ""

                # Cheap match on title/intro; otherwise confirm via the article body
                # (weather alerts name affected voivodeships only in the full text).
                if not _is_relevant(f"{title} {intro}") and not self._article_relevant(url):
                    continue

                seen_urls.add(url)
                rows.append({
                    "timestamp": f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}",
                    "lat": self.aoi.centroid_lat, "lon": self.aoi.centroid_lon,
                    "category": _category(title),
                    "title": title[:300],
                    "description": intro[:500],
                    "url": url, "source": self.name,
                })

        log.info("rcb_alerts: %d Kozy-relevant alerts scraped", len(rows))

        existing = pd.read_parquet(_PARQUET) if _PARQUET.exists() else None
        if not rows and existing is None:
            return FetchResult(self.name, 0, note="no Kozy-relevant RCB items found")

        df = pd.DataFrame(rows) if rows else pd.DataFrame(
            columns=["timestamp", "lat", "lon", "category",
                     "title", "description", "url", "source"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")

        if existing is not None and len(existing):
            df = pd.concat([existing, df], ignore_index=True)
        df = df.drop_duplicates(subset=["url"]).sort_values(
            "timestamp", ascending=False).reset_index(drop=True)

        return self.emit(df, "rcb_alerts", urls=[base],
                         note=f"{len(df)} Kozy-relevant alerts (accumulated)")
