"""Geospatial helpers: AOI boundary, bbox, point-in-polygon, distances."""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry

from .config import PROCESSED_DIR, BBox, load_aoi

log = logging.getLogger("kozy_data.geo")

BOUNDARY_PATH = PROCESSED_DIR / "kozy_boundary.geojson"


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
