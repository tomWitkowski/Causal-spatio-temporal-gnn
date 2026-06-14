"""Road accident statistics from the Polish Police (KGP), published on dane.gov.pl.

Police publishes yearly accident datasets at voivodeship/powiat level.
We filter to powiat bielski (2402 / śląskie) and emit yearly counts.

Output: timestamp (year-01-01 UTC), lat, lon, category, value, source.
"""
from __future__ import annotations

import logging

import pandas as pd

from ..base import BaseDownloader, FetchResult
from ..config import PROCESSED_DIR
from .. import http

log = logging.getLogger("kozy_data.sewik")

DANE_GOV_API = "https://api.dane.gov.pl/1.4"
_TABLE = "sewik_accidents"
_PARQUET = PROCESSED_DIR / f"{_TABLE}.parquet"

# dane.gov.pl dataset slugs — Police accident stats by powiat
_SLUGS = [
    "wypadki-drogowe-2020",
    "wypadki-drogowe-2021",
    "wypadki-drogowe-2022",
    "wypadki-drogowe-2023",
    "wypadki-drogowe-2024",
]
_POWIAT_CODE = "2402"
_POWIAT_NAME = "bielski"
# Fallback: query by keyword if above slugs don't resolve
_SEARCH_TERMS = ["wypadki drogowe powiat", "zdarzenia drogowe policja"]


def _find_resource(slug: str) -> tuple[str, str] | None:
    """Return (download_url, format) for the first CSV/XLSX in the dataset."""
    try:
        data = http.get_json(f"{DANE_GOV_API}/datasets/{slug}", timeout=30)
    except Exception as exc:
        log.debug("dataset %s: %s", slug, exc)
        return None
    for rel in (data.get("data", {}).get("relationships", {})
                .get("resources", {}).get("data", [])):
        rid = rel.get("id")
        if not rid:
            continue
        try:
            res = http.get_json(f"{DANE_GOV_API}/resources/{rid}", timeout=30)
        except Exception:
            continue
        attrs = res.get("data", {}).get("attributes", {})
        fmt = str(attrs.get("format", "")).upper()
        url = attrs.get("download_url") or attrs.get("file_url")
        if fmt in ("CSV", "XLS", "XLSX") and url:
            return url, fmt
    return None


def _search_slugs(term: str) -> list[str]:
    try:
        data = http.get_json(f"{DANE_GOV_API}/datasets",
                             params={"q": term, "page[size]": 10}, timeout=30)
    except Exception:
        return []
    return [
        d.get("attributes", {}).get("slug", "")
        for d in data.get("data", [])
        if d.get("attributes", {}).get("slug")
    ]


def _parse(content: bytes, url: str, year: int,
           lat: float, lon: float) -> list[dict]:
    try:
        if url.lower().endswith((".xls", ".xlsx")):
            df = pd.read_excel(content, dtype=str)
        else:
            for enc in ("cp1250", "utf-8"):
                try:
                    df = pd.read_csv(pd.io.common.BytesIO(content),
                                     sep=None, engine="python",
                                     dtype=str, encoding=enc)
                    break
                except Exception:
                    continue
            else:
                return []
    except Exception as exc:
        log.warning("parse failed %s: %s", url, exc)
        return []

    df.columns = [str(c).lower().strip() for c in df.columns]
    code_col = next((c for c in df.columns if "kod" in c and "powiat" in c), None)
    name_col = next((c for c in df.columns if "powiat" in c and "kod" not in c), None)
    if code_col:
        mask = df[code_col].str.contains(_POWIAT_CODE, na=False)
    elif name_col:
        mask = df[name_col].str.lower().str.contains(_POWIAT_NAME, na=False)
    else:
        log.warning("sewik: no powiat column in %s (columns: %s)", url, list(df.columns)[:8])
        return []

    subset = df[mask]
    if subset.empty:
        return []

    ts = pd.Timestamp(year=year, month=1, day=1, tz="UTC")
    rows = []
    for cat in ("wypadki", "zabici", "ranni", "kolizje"):
        col = next((c for c in subset.columns if cat in c), None)
        if col is None:
            continue
        val = pd.to_numeric(subset[col].str.replace(r"\s+", "", regex=True)
                            .str.replace(",", "."), errors="coerce").sum()
        if not pd.isna(val) and val > 0:
            rows.append({"timestamp": ts, "lat": lat, "lon": lon,
                         "category": cat, "value": float(val),
                         "source": "sewik_accidents"})
    return rows


class SewikAccidentsDownloader(BaseDownloader):
    name = "sewik_accidents"
    license = "KGP Policja / dane.gov.pl (open data)"

    def run(self, since=None) -> FetchResult:
        lat, lon = self.aoi.centroid_lat, self.aoi.centroid_lon
        rows: list[dict] = []
        existing = pd.read_parquet(_PARQUET) if _PARQUET.exists() else None

        slugs = list(_SLUGS)
        # Extend with search if configured
        if not slugs:
            for term in _SEARCH_TERMS:
                slugs.extend(_search_slugs(term))

        for slug in slugs:
            year_str = slug.rsplit("-", 1)[-1]
            if not year_str.isdigit():
                continue
            year = int(year_str)
            if since is not None and year < int(str(since)[:4]):
                continue
            result = _find_resource(slug)
            if not result:
                log.info("sewik: no resource for %s", slug)
                continue
            url, fmt = result
            log.info("sewik: year %d → %s", year, url)
            try:
                content = http.get(url, timeout=60).content
            except Exception as exc:
                log.warning("sewik download failed %s: %s", url, exc)
                continue
            rows.extend(_parse(content, url, year, lat, lon))

        if not rows and existing is None:
            return FetchResult(self.name, 0,
                               note="no data — dataset slugs may have changed on dane.gov.pl")

        df = pd.DataFrame(rows) if rows else pd.DataFrame()
        if existing is not None and len(existing):
            df = pd.concat([existing, df], ignore_index=True)
        df = df.drop_duplicates(subset=["timestamp", "category"])
        df = df.sort_values("timestamp").reset_index(drop=True)

        return self.emit(df, _TABLE, urls=[DANE_GOV_API],
                         note=f"powiat bielski accidents, {len(df)} rows")
