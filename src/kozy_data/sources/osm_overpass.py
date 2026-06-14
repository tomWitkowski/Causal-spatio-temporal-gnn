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
from shapely.geometry import LineString

from .. import io
from ..base import BaseDownloader, FetchResult
from ..config import PROCESSED_DIR
from ..geo import BOUNDARY_PATH, fetch_elevations
from .. import http

log = logging.getLogger("kozy_data.osm")

# Categories fetched with full line geometry (for the graph's road/water edges).
LINE_CATEGORIES = ("highway", "waterway")
LINES_PATH = PROCESSED_DIR / "osm_lines.parquet"


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

    # --- line geometry (roads / waterways for graph edges) -------------
    def fetch_lines(self) -> list[dict[str, Any]]:
        """Fetch highway/waterway ways with full polyline geometry (``out geom;``).

        Unlike :meth:`fetch_features` (which collapses ways to a center point),
        this keeps each way's node sequence so the graph builder can derive
        connectivity, downstream direction, and road×waterway intersections.
        """
        terc = self.aoi.teryt_gmina
        configured = self.cfg.get("features", list(LINE_CATEGORIES))
        keys = [k for k in LINE_CATEGORIES if k in configured]
        if not keys:
            return []
        selectors = "".join(f'way["{k}"](area.a);' for k in keys)
        ql = (
            "[out:json][timeout:240];"
            f'relation["boundary"="administrative"]["admin_level"="7"]["teryt:terc"="{terc}"]->.b;'
            ".b map_to_area->.a;"
            f"({selectors});"
            "out geom;"
        )
        data = self._query(ql)
        rows = []
        for e in data.get("elements", []):
            if e.get("type") != "way":
                continue
            coords = [
                (pt["lon"], pt["lat"])
                for pt in e.get("geometry", [])
                if pt.get("lon") is not None and pt.get("lat") is not None
            ]
            if len(coords) < 2:
                continue
            tags = e.get("tags", {})
            category = next((k for k in keys if k in tags), None)
            rows.append({
                "osm_id": e["id"],
                "category": category,
                "value": tags.get(category) if category else None,
                "name": tags.get("name"),
                "geometry_wkt": LineString(coords).wkt,
                "source": self.name,
            })
        return rows

    def _add_elevation(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add elevation_m: extract from OSM ele tag first, fill rest via Open-Meteo API."""
        # Extract ele from tags where OSM already has it
        def _ele_from_tags(tags_json: str) -> float | None:
            try:
                v = json.loads(tags_json).get("ele")
                return float(v) if v is not None else None
            except Exception:
                return None

        df = df.copy()
        df["elevation_m"] = df["tags"].apply(_ele_from_tags)
        missing_mask = df["elevation_m"].isna()
        if not missing_mask.any():
            return df

        # Batch-fetch from Open-Meteo for rows missing elevation
        coords = df.loc[missing_mask, ["lat", "lon"]].drop_duplicates()
        elevations = fetch_elevations(coords["lat"].tolist(), coords["lon"].tolist())
        elev_map = {(la, lo): el for (la, lo), el in
                    zip(zip(coords["lat"], coords["lon"]), elevations) if el is not None}

        df.loc[missing_mask, "elevation_m"] = df.loc[missing_mask].apply(
            lambda r: elev_map.get((r["lat"], r["lon"])), axis=1)
        log.info("osm: elevation filled — from_tags=%d api=%d missing=%d",
                 int((~missing_mask).sum()), len(elev_map),
                 int(df["elevation_m"].isna().sum()))
        return df

    def run(self, since=None) -> FetchResult:
        # Static snapshot: on a plain `fetch all` (since=None) skip if already
        # present; pass --since to force a refresh.
        features_path = PROCESSED_DIR / "osm_features.parquet"
        if (since is None and BOUNDARY_PATH.exists()
                and features_path.exists() and LINES_PATH.exists()):
            n = len(pd.read_parquet(features_path, columns=["osm_id"]))
            log.info("osm_overpass: snapshot exists (%d rows) — skipping; --since to refresh", n)
            return FetchResult(self.name, n,
                               [features_path.name, LINES_PATH.name, BOUNDARY_PATH.name],
                               (None, None), note="snapshot exists; --since to refresh")

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
        # 2) point features
        rows = self.fetch_features()
        df = pd.DataFrame(rows)
        df = self._add_elevation(df)
        out = io.save_table(df, "osm_features")
        outputs.append(out.name)
        # 3) line geometry (roads / waterways) for graph edges
        line_rows = self.fetch_lines()
        lines_df = pd.DataFrame(
            line_rows,
            columns=["osm_id", "category", "value", "name", "geometry_wkt", "source"],
        )
        io.save_table(lines_df, "osm_lines")
        outputs.append(LINES_PATH.name)
        log.info("osm: %d line geometries (%s)", len(lines_df),
                 lines_df["category"].value_counts().to_dict() if len(lines_df) else {})
        io.write_manifest(self.name, license=self.license, urls=urls,
                          n_records=len(df), date_range=(None, None), outputs=outputs)
        return FetchResult(self.name, len(df), outputs, (None, None),
                           note=f"boundary + {len(df)} features + {len(lines_df)} lines")
