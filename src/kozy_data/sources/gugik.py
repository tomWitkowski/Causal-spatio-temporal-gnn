"""GUGiK PRG: authoritative gmina boundary from GUGiK WFS.

Fetches the Kozy gmina polygon from the Krajowy Rejestr Granic WFS service
as an alternative / complement to the OSM boundary.

Output: gugik_boundary.geojson (saved directly) +
        gugik_prg.parquet summary (name, teryt, area_km2, source).
"""
from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd

from ..base import BaseDownloader, FetchResult
from ..config import PROCESSED_DIR
from .. import io

log = logging.getLogger("kozy_data.gugik")

WFS_BASE = "https://integracja.gugik.gov.pl/cgi-bin/KrajowyRejestrGranic"
# TERYT in WFS can appear with or without the administrative suffix _2 (wiejska)
_TERYT_CANDIDATES = ["240207_2", "2402072"]
_GEOJSON = PROCESSED_DIR / "gugik_boundary.geojson"
_TABLE = "gugik_prg"


def _fetch_gmina() -> gpd.GeoDataFrame | None:
    for teryt in _TERYT_CANDIDATES:
        url = (
            f"{WFS_BASE}?service=WFS&version=2.0.0&request=GetFeature"
            f"&typeName=ms:gminy&outputFormat=application/json"
            f"&CQL_FILTER=JPT_KOD_JE='{teryt}'"
        )
        try:
            gdf = gpd.read_file(url)
        except Exception as exc:
            log.debug("WFS attempt teryt=%s failed: %s", teryt, exc)
            continue
        if len(gdf):
            log.info("gugik: fetched %d feature(s) for teryt=%s", len(gdf), teryt)
            return gdf
    return None


class GugikDownloader(BaseDownloader):
    name = "gugik"
    license = "GUGiK / PZGiK (open since 2020, attribution)"

    def run(self, since=None) -> FetchResult:
        gdf = _fetch_gmina()
        if gdf is None:
            return FetchResult(self.name, 0, note="WFS returned no features for Kozy TERYT")

        gdf = gdf.to_crs("EPSG:4326")

        # Save as GeoJSON
        fc = gdf.__geo_interface__
        _GEOJSON.parent.mkdir(parents=True, exist_ok=True)
        import json
        _GEOJSON.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
        log.info("gugik: saved boundary to %s", _GEOJSON.name)

        # Area in km² (reproject to metric CRS)
        gdf_m = gdf.to_crs("EPSG:2180")
        area_km2 = round(float(gdf_m.geometry.area.sum()) / 1e6, 2)

        name_col = next((c for c in gdf.columns
                         if "nazwa" in c.lower() or "name" in c.lower()), None)
        name = str(gdf[name_col].iloc[0]).strip() if name_col else "Kozy"

        teryt_col = next((c for c in gdf.columns if "kod" in c.lower()), None)
        teryt = str(gdf[teryt_col].iloc[0]).strip() if teryt_col else "2402072"

        summary = pd.DataFrame([{
            "name": name, "teryt": teryt,
            "area_km2": area_km2, "source": self.name,
        }])
        log.info("gugik: %s, area=%.1f km²", name, area_km2)

        return self.emit(summary, _TABLE, urls=[WFS_BASE], time_col=None,
                         note=f"PRG boundary, area={area_km2} km²")
