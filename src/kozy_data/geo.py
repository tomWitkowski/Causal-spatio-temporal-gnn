"""Geospatial helpers: AOI boundary, bbox, point-in-polygon, distances."""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

import numpy as np
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry

from .config import PROCESSED_DIR, BBox, load_aoi

log = logging.getLogger("kozy_data.geo")

BOUNDARY_PATH = PROCESSED_DIR / "kozy_boundary.geojson"
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"


def fetch_elevations(
    lats: list[float], lons: list[float], *, batch_size: int = 100,
    timeout: int = 60, pause: float = 1.0,
) -> list[float | None]:
    """Elevation in metres for each (lat, lon) via Open-Meteo (Copernicus DEM 90m).

    Returns a list aligned to the inputs; entries are ``None`` only where the API
    could not be reached. Batches are paced (``pause`` seconds apart) to stay under
    Open-Meteo's rate limit, and each batch is retried with an escalating wait when
    the API is rate-limiting. Successful batches are cached by :func:`http.get`, so a
    re-run resumes instantly past everything already fetched.
    """
    import time

    from . import http  # local import avoids an import cycle at module load

    out: list[float | None] = [None] * len(lats)
    for i in range(0, len(lats), batch_size):
        b_lats, b_lons = lats[i:i + batch_size], lons[i:i + batch_size]
        params = {"latitude": ",".join(str(v) for v in b_lats),
                  "longitude": ",".join(str(v) for v in b_lons)}
        for attempt in range(4):
            try:
                data = http.get(ELEVATION_URL, params=params, timeout=timeout).json()
                for j, el in enumerate(data.get("elevation", [])):
                    out[i + j] = float(el) if el is not None else None
                break
            except Exception as exc:  # noqa: BLE001 - rate limit / transient failure
                wait = 30 * (attempt + 1)
                log.warning("elevation batch %d-%d failed (try %d/4): %s — waiting %ds",
                            i, i + len(b_lats), attempt + 1, exc, wait)
                if attempt < 3:
                    time.sleep(wait)
        if pause:
            time.sleep(pause)
    return out


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def distance_to_centroid_km(lat: float, lon: float) -> float:
    aoi = load_aoi()
    return haversine_km(lat, lon, aoi.centroid_lat, aoi.centroid_lon)


def load_boundary() -> BaseGeometry | None:
    """Load the gmina boundary geometry if it has been fetched (OSM/GUGiK)."""
    if not BOUNDARY_PATH.exists():
        return None
    data = json.loads(BOUNDARY_PATH.read_text(encoding="utf-8"))
    geoms = []
    if data.get("type") == "FeatureCollection":
        for feat in data["features"]:
            geoms.append(shape(feat["geometry"]))
    elif data.get("type") == "Feature":
        geoms.append(shape(data["geometry"]))
    else:
        geoms.append(shape(data))
    if not geoms:
        return None
    geom = geoms[0]
    for g in geoms[1:]:
        geom = geom.union(g)
    return geom


def bbox_from_geometry(geom: BaseGeometry) -> BBox:
    min_lon, min_lat, max_lon, max_lat = geom.bounds
    return BBox(min_lat=min_lat, min_lon=min_lon, max_lat=max_lat, max_lon=max_lon)


def active_bbox() -> BBox:
    """Use the boundary-derived bbox when available, else the config fallback."""
    geom = load_boundary()
    if geom is not None:
        return bbox_from_geometry(geom)
    return load_aoi().bbox


def in_aoi(lat: float, lon: float) -> bool:
    """Point-in-polygon against the boundary, falling back to bbox."""
    geom = load_boundary()
    if geom is not None:
        return geom.covers(Point(lon, lat))
    return active_bbox().contains(lat, lon)


def grid_points_in_boundary(n: int) -> list[tuple[float, float]]:
    """Return approximately *n* uniformly distributed (lat, lon) pairs inside the AOI.

    Uses a regular grid over the bbox, filtered by the boundary polygon.
    Falls back to a bbox grid when the boundary file is not available.
    """
    geom = load_boundary()
    aoi = load_aoi()

    if geom is None:
        bb = aoi.bbox
        side = max(1, round(math.sqrt(n)))
        lats = np.linspace(bb.min_lat, bb.max_lat, side)
        lons = np.linspace(bb.min_lon, bb.max_lon, side)
        return [(round(float(la), 6), round(float(lo), 6))
                for la in lats for lo in lons]

    min_lon, min_lat, max_lon, max_lat = geom.bounds
    bbox_area = (max_lat - min_lat) * (max_lon - min_lon)
    fill = max(0.05, geom.area / bbox_area)
    side = max(2, math.ceil(math.sqrt(n / fill)) + 1)

    lats = np.linspace(min_lat, max_lat, side)
    lons = np.linspace(min_lon, max_lon, side)

    inside = [
        (round(float(la), 6), round(float(lo), 6))
        for la in lats
        for lo in lons
        if geom.contains(Point(lo, la))
    ]

    if len(inside) <= n:
        return inside

    # Thin evenly to target n while preserving spatial coverage
    step = len(inside) / n
    return [inside[round(i * step)] for i in range(n)]


def filter_points_in_aoi(records: list[dict[str, Any]], lat_key: str = "lat",
                         lon_key: str = "lon") -> list[dict[str, Any]]:
    out = []
    for rec in records:
        try:
            if in_aoi(float(rec[lat_key]), float(rec[lon_key])):
                out.append(rec)
        except (KeyError, TypeError, ValueError):
            continue
    return out
