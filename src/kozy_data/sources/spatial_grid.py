"""Spatial grid: N uniform points within the AOI boundary with DEM elevation.

Elevation is fetched via the Open-Meteo Elevation API (Copernicus DEM 90m, free).
Run this source once after the boundary (osm_overpass) is available.

Output: point_id, lat, lon, elevation_m.
"""
from __future__ import annotations

import logging

import pandas as pd

from ..base import BaseDownloader, FetchResult
from ..config import PROCESSED_DIR
from ..geo import ELEVATION_URL, fetch_elevations, grid_points_in_boundary

log = logging.getLogger("kozy_data.spatial_grid")

_PARQUET = PROCESSED_DIR / "spatial_grid.parquet"


class SpatialGridDownloader(BaseDownloader):
    name = "spatial_grid"
    license = "Open-Meteo / Copernicus DEM 90m (CC-BY 4.0)"

    def run(self, since=None) -> FetchResult:
        # Static snapshot: skip on a plain `fetch all`; --since forces a refresh.
        if since is None and _PARQUET.exists():
            n = len(pd.read_parquet(_PARQUET, columns=["point_id"]))
            log.info("spatial_grid: snapshot exists (%d pts) — skipping; --since to refresh", n)
            return FetchResult(self.name, n, [_PARQUET.name],
                               note="snapshot exists; --since to refresh")

        n = int(
            self.cfg.get("n_points")
            or self.aoi.raw.get("spatial", {}).get("n_points", 50)
        )
        points = grid_points_in_boundary(n)
        if not points:
            return FetchResult(self.name, 0, note="no boundary available")

        lats = [p[0] for p in points]
        lons = [p[1] for p in points]
        elevations = fetch_elevations(lats, lons)

        df = pd.DataFrame({
            "point_id": range(len(points)),
            "lat": lats,
            "lon": lons,
            "elevation_m": elevations,
        })
        return self.emit(df, "spatial_grid", urls=[ELEVATION_URL], time_col=None,
                         note=f"{len(df)} points, elevation via Copernicus DEM")
