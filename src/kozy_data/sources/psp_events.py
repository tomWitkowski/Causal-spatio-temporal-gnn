"""PSP / SWD-ST fire-service event statistics (powiat-level, yearly).

Source: dane.gov.pl open datasets published by KG PSP.
Filters to powiat bielski (TERYT 2402 / name "bielski").

Output: timestamp (year-01-01 UTC), lat, lon, category, value, source.
"""
from __future__ import annotations

import logging

import pandas as pd

from ..base import BaseDownloader, FetchResult
from ..config import PROCESSED_DIR
from .. import http

log = logging.getLogger("kozy_data.psp_events")

DANE_GOV_API = "https://api.dane.gov.pl/1.4"
_TABLE = "psp_events"
_PARQUET = PROCESSED_DIR / f"{_TABLE}.parquet"

# Known dataset slugs (checked annually; add new year slugs as they appear)
_SLUG_TMPL = ("statystyki-zdarzen-systemu-wspomagania-decyzji-panstwowej"
              "-strazy-pozarnej-swd-psp-za-rok-{year}")
_DATASET_SLUGS = [_SLUG_TMPL.format(year=y) for y in range(2015, 2026)]
_POWIAT_CODE = "2402"
_POWIAT_NAME = "bielski"


def _find_csv_url(slug: str) -> str | None:
    try:
        data = http.get_json(f"{DANE_GOV_API}/datasets/{slug}", timeout=30)
    except Exception as exc:
        log.debug("dataset %s unavailable: %s", slug, exc)
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
        title = str(attrs.get("title", "")).lower()
        url = attrs.get("download_url") or attrs.get("file_url")
        if fmt in ("CSV", "XLS", "XLSX") and url:
            if any(k in title for k in ("powiat", "zdarzenia", "stat")):
                return url
    return None


def _parse_resource(url: str, year: int,
                    lat: float, lon: float) -> list[dict]:
    try:
        content = http.get(url, timeout=60).content
    except Exception as exc:
        log.warning("download failed %s: %s", url, exc)
        return []
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

    # Find powiat filter column
    code_col = next((c for c in df.columns if "kod" in c and "powiat" in c), None)
    name_col = next((c for c in df.columns if "powiat" in c and "kod" not in c), None)
    if code_col:
        mask = df[code_col].str.contains(_POWIAT_CODE, na=False)
    elif name_col:
        mask = df[name_col].str.lower().str.contains(_POWIAT_NAME, na=False)
    else:
        log.warning("psp_events: cannot find powiat column in %s", url)
        return []

    subset = df[mask]
    if subset.empty:
        log.info("psp_events: no bielski rows in %s", url)
        return []

    val_col = next((c for c in subset.columns
                    if any(k in c for k in ("liczba", "count", "ilosc", "ilość", "wartosc"))), None)
    cat_col = next((c for c in subset.columns
                    if any(k in c for k in ("kategoria", "typ", "rodzaj", "category"))), None)

    ts = pd.Timestamp(year=year, month=1, day=1, tz="UTC")
    rows = []
    if val_col and cat_col:
        for _, r in subset.iterrows():
            val = pd.to_numeric(str(r[val_col]).replace(" ", "").replace(",", "."),
                                errors="coerce")
            if pd.isna(val):
                continue
            rows.append({"timestamp": ts, "lat": lat, "lon": lon,
                         "category": str(r[cat_col]).strip(),
                         "value": float(val), "source": "psp_events"})
    else:
        # Fallback: sum all numeric columns as aggregate
        total = 0.0
        for c in subset.columns:
            v = pd.to_numeric(subset[c].str.replace(" ", "").str.replace(",", "."),
                              errors="coerce").sum()
            total += v if not pd.isna(v) else 0
        if total:
            rows.append({"timestamp": ts, "lat": lat, "lon": lon,
                         "category": "total", "value": total, "source": "psp_events"})
    return rows


class PspEventsDownloader(BaseDownloader):
    name = "psp_events"
    license = "KG PSP / dane.gov.pl (open data)"

    def run(self, since=None) -> FetchResult:
        lat, lon = self.aoi.centroid_lat, self.aoi.centroid_lon
        rows: list[dict] = []

        existing = None
        if _PARQUET.exists():
            existing = pd.read_parquet(_PARQUET)

        for slug in _DATASET_SLUGS:
            year = int(slug.rsplit("-", 1)[-1])
            if since is not None:
                since_year = int(str(since)[:4])
                if year < since_year:
                    continue
            url = _find_csv_url(slug)
            if not url:
                log.info("psp_events: no resource for %s", slug)
                continue
            log.info("psp_events: downloading year %d from %s", year, url)
            rows.extend(_parse_resource(url, year, lat, lon))

        if not rows and existing is None:
            return FetchResult(self.name, 0, note="no data from dane.gov.pl")

        df = pd.DataFrame(rows) if rows else pd.DataFrame()
        if existing is not None and len(existing):
            df = pd.concat([existing, df], ignore_index=True)
        df = df.drop_duplicates(subset=["timestamp", "category"])
        df = df.sort_values("timestamp").reset_index(drop=True)

        return self.emit(df, _TABLE, urls=[DANE_GOV_API], time_col="timestamp",
                         note=f"powiat bielski, {len(df)} rows")
