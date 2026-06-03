"""OpenStreetMap via Overpass API: gmina boundary + POIs/features.

Produces:
  * data/processed/kozy_boundary.geojson  (used as AOI by other sources)
  * data/processed/osm_features.parquet   (POIs/features with lat/lon + tags)
"""
from __future__ import annotations

import json
import logging
from typing import Any

import pandas as pd

from .. import io
from ..base import BaseDownloader, FetchResult
from ..config import PROCESSED_DIR
from ..geo import BOUNDARY_PATH
from .. import http

log = logging.getLogger("kozy_data.osm")


def _assemble_rings(ways: list[list[tuple[float, float]]]) -> list[list[list[float]]]:
    """Stitch open ways into closed rings. Returns list of [ [lon,lat], ... ]."""
    segments = [list(w) for w in ways if len(w) >= 2]
    rings: list[list[list[float]]] = []
    while segments:
        ring = segments.pop(0)
        changed = True
        while changed and ring[0] != ring[-1]:
            changed = False
            for i, seg in enumerate(segments):
                if seg[0] == ring[-1]:
                    ring.extend(seg[1:]); segments.pop(i); changed = True; break
                if seg[-1] == ring[-1]:
                    ring.extend(reversed(seg[:-1])); segments.pop(i); changed = True; break
                if seg[-1] == ring[0]:
                    ring[:0] = seg[:-1]; segments.pop(i); changed = True; break
                if seg[0] == ring[0]:
                    ring[:0] = list(reversed(seg))[:-1]; segments.pop(i); changed = True; break
        rings.append([[lon, lat] for (lon, lat) in ring])
    return rings


class OSMOverpassDownloader(BaseDownloader):
    name = "osm_overpass"
    license = "ODbL 1.0 (OpenStreetMap contributors)"

    def _overpass_url(self) -> str:
        return self.cfg.get("overpass_url", "https://overpass-api.de/api/interpreter")

    def _query(self, ql: str) -> dict[str, Any]:
        resp = http.post(self._overpass_url(), data={"data": ql}, timeout=180)
        return resp.json()

    # --- boundary ------------------------------------------------------
    def fetch_boundary(self) -> dict[str, Any] | None:
        terc = self.aoi.teryt_gmina
        ql = (
            "[out:json][timeout:180];"
            f'relation["boundary"="administrative"]["admin_level"="7"]["teryt:terc"="{terc}"];'
            "out geom;"
        )
        data = self._query(ql)
        rels = [e for e in data.get("elements", []) if e.get("type") == "relation"]
        if not rels:
            # fallback: match by name
            ql = (
                "[out:json][timeout:180];"
                f'relation["boundary"="administrative"]["admin_level"="7"]["name"="{self.aoi.name}"];'
                "out geom;"
            )
            data = self._query(ql)
            rels = [e for e in data.get("elements", []) if e.get("type") == "relation"]
        if not rels:
            return None
        rel = rels[0]
        outer, inner = [], []
        for m in rel.get("members", []):
            if m.get("type") != "way" or "geometry" not in m:
                continue
            coords = [(pt["lon"], pt["lat"]) for pt in m["geometry"]]
            (outer if m.get("role") != "inner" else inner).append(coords)
        outer_rings = _assemble_rings(outer)
        inner_rings = _assemble_rings(inner)
        if len(outer_rings) == 1:
            geometry = {"type": "Polygon", "coordinates": [outer_rings[0], *inner_rings]}
        else:
            geometry = {"type": "MultiPolygon",
                        "coordinates": [[r] for r in outer_rings]}
        feature = {
            "type": "Feature",
            "properties": {"name": rel.get("tags", {}).get("name", self.aoi.name),
                           "teryt:terc": terc, "osm_id": rel.get("id")},
            "geometry": geometry,
        }
        return {"type": "FeatureCollection", "features": [feature]}

    # --- features ------------------------------------------------------
    def fetch_features(self) -> list[dict[str, Any]]:
        terc = self.aoi.teryt_gmina
        keys = self.cfg.get("features", ["amenity", "shop", "highway", "building",
                                         "waterway", "landuse", "natural"])
        selectors = "".join(
            f'node["{k}"](area.a);way["{k}"](area.a);' for k in keys
        )
        ql = (
            "[out:json][timeout:240];"
            f'relation["boundary"="administrative"]["admin_level"="7"]["teryt:terc"="{terc}"]->.b;'
            ".b map_to_area->.a;"
            f"({selectors});"
            "out center tags;"
        )
        data = self._query(ql)
        rows = []
        for e in data.get("elements", []):
            if e["type"] == "node":
                lat, lon = e.get("lat"), e.get("lon")
            else:
                c = e.get("center", {})
                lat, lon = c.get("lat"), c.get("lon")
            if lat is None or lon is None:
                continue
            tags = e.get("tags", {})
            category = next((k for k in keys if k in tags), None)
            rows.append({
                "osm_type": e["type"],
                "osm_id": e["id"],
                "lat": lat,
                "lon": lon,
                "category": category,
                "value": tags.get(category) if category else None,
                "name": tags.get("name"),
                "tags": json.dumps(tags, ensure_ascii=False),
                "source": self.name,
            })
        return rows

    def run(self, since=None) -> FetchResult:
        urls = [self._overpass_url()]
        # 1) boundary -> AOI
        fc = self.fetch_boundary()
        outputs: list[str] = []
        if fc is not None:
            PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
            BOUNDARY_PATH.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
            outputs.append(BOUNDARY_PATH.name)
            log.info("saved boundary -> %s", BOUNDARY_PATH.name)
        else:
            log.warning("could not fetch boundary for %s", self.aoi.name)
        # 2) features
        rows = self.fetch_features()
        df = pd.DataFrame(rows)
        out = io.save_table(df, "osm_features")
        outputs.append(out.name)
        io.write_manifest(self.name, license=self.license, urls=urls,
                          n_records=len(df), date_range=(None, None), outputs=outputs)
        return FetchResult(self.name, len(df), outputs, (None, None),
                           note="boundary+features (static snapshot)")
