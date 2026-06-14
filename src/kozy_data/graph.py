"""Assemble the OSM-grounded spatio-temporal graph for gmina Kozy.

Nodes are named OSM features (amenity/building/natural). Edges come from real
road/water geometry. Gmina-wide events are projected onto nodes through the
conduits they travel along (outages along streets, hydro warnings along streams),
which is the spatial structure a gmina-centroid event table cannot provide.

Outputs (data/processed/):
  * graph_nodes.parquet   — one row per node, static features
  * graph_edges.parquet   — src, dst, edge_type, distance_m, d_elev_m, directed
  * graph_dynamic.parquet — daily node x feature time series (weather + event exposure)
  * graph_spec.json       — node/edge metadata + expert causal prior (framework-agnostic)

Distances are computed in a local equirectangular projection (metres), accurate
enough across a ~10 km gmina.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
from functools import reduce
from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd
from shapely import wkt
from shapely.geometry import LineString, Point

from . import geocode, io
from .config import CONFIG_DIR, PROCESSED_DIR, load_aoi, _load_yaml
from .geo import fetch_elevations

log = logging.getLogger("kozy_data.graph")

_M_PER_DEG_LAT = 111_320.0


# --------------------------------------------------------------------------- #
# projection helpers (lon/lat -> local metres)
# --------------------------------------------------------------------------- #
def _projector(lat0: float, lon0: float):
    kx = _M_PER_DEG_LAT * math.cos(math.radians(lat0))

    def to_xy(lon: float, lat: float) -> tuple[float, float]:
        return ((lon - lon0) * kx, (lat - lat0) * _M_PER_DEG_LAT)

    return to_xy


def _line_to_xy(geom_wkt: str, to_xy) -> LineString:
    return LineString([to_xy(x, y) for x, y in wkt.loads(geom_wkt).coords])


# --------------------------------------------------------------------------- #
# nodes + static features
# --------------------------------------------------------------------------- #
def _build_nodes(cfg: dict, to_xy) -> tuple[pd.DataFrame, dict]:
    feats = pd.read_parquet(PROCESSED_DIR / "osm_features.parquet")
    lines = pd.read_parquet(PROCESSED_DIR / "osm_lines.parquet")

    cats = cfg["nodes"]["categories"]
    named = feats[
        feats["category"].isin(cats)
        & feats["name"].notna()
        & (feats["name"].astype(str).str.strip() != "")
    ].copy()
    named = named.reset_index(drop=True)
    named["node_id"] = named["osm_type"].str[0] + named["osm_id"].astype(str)

    node_xy = np.array([to_xy(lo, la) for la, lo in zip(named["lat"], named["lon"])])

    # distance to nearest stream / road (metres)
    streams = [_line_to_xy(w, to_xy) for w in lines.loc[lines["category"] == "waterway", "geometry_wkt"]]
    roads = [_line_to_xy(w, to_xy) for w in lines.loc[lines["category"] == "highway", "geometry_wkt"]]

    def _min_dist(pt: Point, geoms: list[LineString]) -> float:
        return round(min((pt.distance(g) for g in geoms), default=float("nan")), 1)

    pts = [Point(xy) for xy in node_xy]
    named["dist_to_stream_m"] = [_min_dist(p, streams) for p in pts]
    named["dist_to_road_m"] = [_min_dist(p, roads) for p in pts]

    # height above nearest drainage (HAND): node elevation minus elevation of the
    # nearest point on the nearest stream — a strong, simple flood-exposure feature.
    streams_ll = [wkt.loads(w) for w in lines.loc[lines["category"] == "waterway", "geometry_wkt"]]
    if streams_ll:
        s_lat, s_lon = [], []
        for la, lo in zip(named["lat"], named["lon"]):
            p_ll = Point(lo, la)
            nearest = min(streams_ll, key=lambda s: s.distance(p_ll))
            npt = nearest.interpolate(nearest.project(p_ll))
            s_lat.append(npt.y)
            s_lon.append(npt.x)
        s_elev = fetch_elevations(s_lat, s_lon)
        named["height_above_stream_m"] = [
            round(ne - se, 1) if pd.notna(ne) and se is not None else float("nan")
            for ne, se in zip(named["elevation_m"], s_elev)]
    else:
        named["height_above_stream_m"] = float("nan")

    # building density: count of ALL buildings within radius
    radius = cfg["features"]["building_density_radius_m"]
    blds = feats[feats["category"] == "building"]
    bld_xy = np.array([to_xy(lo, la) for la, lo in zip(blds["lat"], blds["lon"])])
    dens = []
    for xy in node_xy:
        d = np.hypot(bld_xy[:, 0] - xy[0], bld_xy[:, 1] - xy[1])
        dens.append(int((d <= radius).sum()))
    named["building_density"] = dens

    # nearest open-meteo grid point (weather is attached, not fetched per node)
    grid = pd.read_parquet(PROCESSED_DIR / "spatial_grid.parquet")[["lat", "lon"]]
    grid_xy = np.array([to_xy(lo, la) for la, lo in zip(grid["lat"], grid["lon"])])
    glat, glon = [], []
    for xy in node_xy:
        d = np.hypot(grid_xy[:, 0] - xy[0], grid_xy[:, 1] - xy[1])
        j = int(d.argmin())
        glat.append(float(grid["lat"].iloc[j]))
        glon.append(float(grid["lon"].iloc[j]))
    named["grid_lat"], named["grid_lon"] = glat, glon

    cols = ["node_id", "osm_type", "osm_id", "name", "category", "value",
            "lat", "lon", "elevation_m", "dist_to_stream_m", "dist_to_road_m",
            "height_above_stream_m", "building_density", "grid_lat", "grid_lon"]
    nodes = named[cols].copy()
    geom = {"node_xy": node_xy, "lines": lines, "to_xy": to_xy}
    return nodes, geom


# --------------------------------------------------------------------------- #
# edges
# --------------------------------------------------------------------------- #
def _node_road_water_keys(nodes: pd.DataFrame, geom: dict, cfg: dict) -> tuple[list, list]:
    """For each node, the keys (street/stream identity) it sits on."""
    to_xy, lines = geom["to_xy"], geom["lines"]
    pts = [Point(xy) for xy in geom["node_xy"]]

    def keyed_lines(category: str):
        sub = lines[lines["category"] == category]
        out = []
        for osm_id, name, w in zip(sub["osm_id"], sub["name"], sub["geometry_wkt"]):
            key = geocode.normalize(str(name)) if pd.notna(name) and str(name).strip() else f"id:{osm_id}"
            out.append((key, _line_to_xy(w, to_xy)))
        return out

    roads = keyed_lines("highway")
    waters = keyed_lines("waterway")

    road_snap = cfg["edges"]["road_snap_m"]
    water_snap = cfg["edges"]["water_snap_m"]
    road_keys, water_keys = [], []
    for p in pts:
        road_keys.append({k for k, g in roads if p.distance(g) <= road_snap})
        water_keys.append({k for k, g in waters if p.distance(g) <= water_snap})
    return road_keys, water_keys


def _build_edges(nodes: pd.DataFrame, geom: dict, cfg: dict) -> pd.DataFrame:
    xy = geom["node_xy"]
    n = len(nodes)
    ids = nodes["node_id"].tolist()
    elev = nodes["elevation_m"].tolist()
    D = np.sqrt(((xy[:, None, :] - xy[None, :, :]) ** 2).sum(-1))

    def d_elev(i, j):
        a, b = elev[i], elev[j]
        return None if pd.isna(a) or pd.isna(b) else round(float(b - a), 1)

    rows: list[dict] = []
    seen: set[tuple[int, int]] = set()

    # proximity (kNN, undirected)
    k = cfg["edges"]["proximity_k"]
    max_m = cfg["edges"]["proximity_max_m"]
    for i in range(n):
        order = np.argsort(D[i])
        c = 0
        for j in order:
            j = int(j)
            if j == i or D[i, j] > max_m:
                if D[i, j] > max_m:
                    break
                continue
            pair = (min(i, j), max(i, j))
            if pair not in seen:
                seen.add(pair)
                rows.append({"src": ids[i], "dst": ids[j], "edge_type": "proximity",
                             "distance_m": round(float(D[i, j]), 1),
                             "d_elev_m": d_elev(i, j), "directed": False})
            c += 1
            if c >= k:
                break

    road_keys, water_keys = _node_road_water_keys(nodes, geom, cfg)

    # road: nodes sharing a street, undirected
    road_groups: dict[str, list[int]] = {}
    for i, keys in enumerate(road_keys):
        for key in keys:
            road_groups.setdefault(key, []).append(i)
    road_seen: set[tuple[int, int]] = set()
    for members in road_groups.values():
        for i, j in combinations(members, 2):
            pair = (min(i, j), max(i, j))
            if pair in road_seen:
                continue
            road_seen.add(pair)
            rows.append({"src": ids[i], "dst": ids[j], "edge_type": "road",
                         "distance_m": round(float(D[i, j]), 1),
                         "d_elev_m": d_elev(i, j), "directed": False})

    # water: directed downstream chain per stream (higher -> lower elevation)
    water_groups: dict[str, list[int]] = {}
    for i, keys in enumerate(water_keys):
        for key in keys:
            water_groups.setdefault(key, []).append(i)
    for members in water_groups.values():
        chain = [m for m in members if pd.notna(elev[m])]
        chain.sort(key=lambda m: elev[m], reverse=True)  # highest first
        for a, b in zip(chain, chain[1:]):
            rows.append({"src": ids[a], "dst": ids[b], "edge_type": "water",
                         "distance_m": round(float(D[a, b]), 1),
                         "d_elev_m": d_elev(a, b), "directed": True})

    return pd.DataFrame(rows, columns=["src", "dst", "edge_type",
                                       "distance_m", "d_elev_m", "directed"])


# --------------------------------------------------------------------------- #
# dynamic features (daily): weather + event exposure
# --------------------------------------------------------------------------- #
def _daily_weather(grid_pts: set, daily_cfg: dict) -> pd.DataFrame:
    path = PROCESSED_DIR / "open_meteo_weather.parquet"
    frames = []
    for var, spec in daily_cfg.items():
        sub = pd.read_parquet(path, columns=["timestamp", "lat", "lon", "value"],
                              filters=[("variable", "==", var)])
        sub = sub[[(la, lo) in grid_pts for la, lo in zip(sub["lat"], sub["lon"])]]
        sub["date"] = (pd.to_datetime(sub["timestamp"], utc=True)
                       .dt.tz_convert("Europe/Warsaw").dt.normalize().dt.tz_localize(None))
        g = (sub.groupby(["lat", "lon", "date"])["value"].agg(spec["agg"])
             .round(3).rename(spec["col"]).reset_index())
        frames.append(g)
    weather = reduce(lambda a, b: a.merge(b, on=["lat", "lon", "date"], how="outer"), frames)
    return weather.rename(columns={"lat": "grid_lat", "lon": "grid_lon"})


def _active_level_by_date(starts, ends, levels) -> dict[dt.date, float]:
    """Max level active on each day across [start, end] spans."""
    out: dict[dt.date, float] = {}
    for s, e, lv in zip(starts, ends, levels):
        if pd.isna(s) or pd.isna(e):
            continue
        d = s.date()
        while d <= e.date():
            out[d] = max(out.get(d, 0.0), float(lv))
            d += dt.timedelta(days=1)
    return out


def _build_dynamic(nodes: pd.DataFrame, geom: dict, cfg: dict, start: dt.date) -> pd.DataFrame:
    dates = pd.date_range(start, dt.date.today(), freq="D")
    n_nodes = len(nodes)

    # skeleton: nodes x dates
    dyn = pd.DataFrame({
        "node_id": np.repeat(nodes["node_id"].values, len(dates)),
        "date": np.tile(dates.values, n_nodes),
    })
    dyn = dyn.merge(nodes[["node_id", "grid_lat", "grid_lon",
                           "dist_to_stream_m", "lat", "lon"]], on="node_id", how="left")

    # weather from nearest grid point
    grid_pts = set(zip(nodes["grid_lat"], nodes["grid_lon"]))
    weather = _daily_weather(grid_pts, cfg["weather"]["daily"])
    dyn = dyn.merge(weather, on=["grid_lat", "grid_lon", "date"], how="left")

    # --- hydro (flood) warnings: gated by stream proximity ---
    flood = {}
    wp = PROCESSED_DIR / "imgw_warnings.parquet"
    if wp.exists():
        w = pd.read_parquet(wp)
        nondrought = ~w["event"].astype(str).str.startswith("Susza")
        lv = pd.to_numeric(w["level"], errors="coerce").fillna(0).clip(lower=0)
        sel = nondrought & (lv > 0)
        flood = _active_level_by_date(
            pd.to_datetime(w.loc[sel, "valid_from"], utc=True),
            pd.to_datetime(w.loc[sel, "valid_to"], utc=True),
            lv[sel])
    flood_buf = cfg["events"]["flood_stream_m"]
    date_d = dyn["date"].dt.date
    flood_level = date_d.map(lambda d: flood.get(d, 0.0)).astype(float)
    dyn["flood_warn"] = np.where(dyn["dist_to_stream_m"] <= flood_buf, flood_level, 0.0)

    # --- meteo warnings (broadcast to all nodes) ---
    _SEV = {"yellow": 1.0, "orange": 2.0, "red": 3.0}
    meteo = {}
    mp = PROCESSED_DIR / "meteoalarm_warnings.parquet"
    if mp.exists():
        m = pd.read_parquet(mp)
        meteo = _active_level_by_date(
            pd.to_datetime(m["timestamp"], utc=True),
            pd.to_datetime(m["expires"], utc=True),
            m["category"].map(_SEV).fillna(1.0))
    dyn["meteo_warn"] = date_d.map(lambda d: meteo.get(d, 0.0)).astype(float)

    # --- RCB alerts (broadcast, single day) ---
    rcb_dates = set()
    rp = PROCESSED_DIR / "rcb_alerts.parquet"
    if rp.exists():
        r = pd.read_parquet(rp)
        rcb_dates = set(pd.to_datetime(r["timestamp"], utc=True).dt.date)
    dyn["rcb_alert"] = date_d.map(lambda d: 1.0 if d in rcb_dates else 0.0)

    # --- outages: projected onto nodes near the affected streets' line geometry ---
    dyn["outage"] = 0.0
    tp = PROCESSED_DIR / "tauron_outages.parquet"
    if tp.exists():
        to_xy = geom["to_xy"]
        snap = cfg["events"]["outage_snap_m"]
        # normalized street name -> its highway line(s) in projected metres
        hw = geom["lines"]
        hw = hw[hw["category"] == "highway"]
        street_lines: dict[str, list[LineString]] = {}
        for nm, w in zip(hw["name"], hw["geometry_wkt"]):
            if pd.isna(nm) or not str(nm).strip():
                continue
            street_lines.setdefault(geocode.normalize(str(nm)), []).append(_line_to_xy(w, to_xy))
        node_pts = [Point(xy) for xy in geom["node_xy"]]
        ids = nodes["node_id"].tolist()
        pos = {(nid, d): k for k, (nid, d) in enumerate(zip(dyn["node_id"], date_d))}
        t = pd.read_parquet(tp)
        flags = np.zeros(len(dyn))
        for ts, title in zip(pd.to_datetime(t["timestamp"], utc=True), t["title"].fillna("")):
            d = ts.date()
            names = {m["street"] for m in geocode.match_streets(title)}
            geoms = [g for nm in names for g in street_lines.get(nm, [])]
            if not geoms:
                continue
            for i, p in enumerate(node_pts):
                if (ids[i], d) in pos and min(p.distance(g) for g in geoms) <= snap:
                    flags[pos[(ids[i], d)]] = 1.0
        dyn["outage"] = flags

    feat_cols = ([s["col"] for s in cfg["weather"]["daily"].values()]
                 + ["flood_warn", "meteo_warn", "rcb_alert", "outage"])
    out = dyn[["node_id", "date"] + feat_cols].copy()
    return out, feat_cols


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
def build(since: str | None = None) -> dict[str, Any]:
    aoi = load_aoi()
    cfg = _load_yaml(CONFIG_DIR / "graph.yaml")
    to_xy = _projector(aoi.centroid_lat, aoi.centroid_lon)
    start = dt.date.fromisoformat(since) if since else aoi.start_date

    log.info("graph: building nodes …")
    nodes, geom = _build_nodes(cfg, to_xy)
    log.info("graph: %d nodes (%s)", len(nodes), nodes["category"].value_counts().to_dict())

    log.info("graph: building edges …")
    edges = _build_edges(nodes, geom, cfg)
    log.info("graph: %d edges (%s)", len(edges), edges["edge_type"].value_counts().to_dict())

    log.info("graph: building dynamic features from %s …", start)
    dynamic, feat_cols = _build_dynamic(nodes, geom, cfg, start)
    log.info("graph: dynamic rows=%d active(outage=%d flood=%d meteo=%d rcb=%d)",
             len(dynamic), int((dynamic.outage > 0).sum()), int((dynamic.flood_warn > 0).sum()),
             int((dynamic.meteo_warn > 0).sum()), int((dynamic.rcb_alert > 0).sum()))

    io.save_table(nodes, "graph_nodes")
    io.save_table(edges, "graph_edges")
    io.save_table(dynamic, "graph_dynamic")

    spec = {
        "crs": "local equirectangular metres (origin = gmina centroid)",
        "n_nodes": len(nodes),
        "node_categories": cfg["nodes"]["categories"],
        "static_features": ["elevation_m", "dist_to_stream_m", "dist_to_road_m",
                            "height_above_stream_m", "building_density"],
        "dynamic_features": feat_cols,
        "edge_types": sorted(edges["edge_type"].unique().tolist()),
        "n_edges": {t: int((edges["edge_type"] == t).sum())
                    for t in edges["edge_type"].unique()},
        "date_range": [str(start), str(dt.date.today())],
        "causal_edges": cfg["causal_edges"],
        "params": {k: cfg[k] for k in ("features", "edges", "events")},
    }
    spec_path = PROCESSED_DIR / "graph_spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("graph: wrote %s", spec_path.name)
    return spec
