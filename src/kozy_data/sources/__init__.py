"""Registry of available data sources.

Each entry maps a source name to a ``module:ClassName`` path. Modules are
imported lazily so a missing optional dependency only disables its own source.
"""
from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..base import BaseDownloader

log = logging.getLogger("kozy_data.sources")

# name -> "module:ClassName"
REGISTRY: dict[str, str] = {
    "osm_overpass": "kozy_data.sources.osm_overpass:OSMOverpassDownloader",
    "spatial_grid": "kozy_data.sources.spatial_grid:SpatialGridDownloader",
    "open_meteo": "kozy_data.sources.open_meteo:OpenMeteoDownloader",
    "gios_air": "kozy_data.sources.gios_air:GiosAirDownloader",
    "gus_bdl": "kozy_data.sources.gus_bdl:GusBdlDownloader",
    "imgw_warnings": "kozy_data.sources.imgw_warnings:ImgwWarningsDownloader",
    "meteoalarm": "kozy_data.sources.meteoalarm:MeteoalarmDownloader",
    "imgw_meteo": "kozy_data.sources.imgw_meteo:ImgwMeteoDownloader",
    "imgw_hydro": "kozy_data.sources.imgw_hydro:ImgwHydroDownloader",
    "psp_events": "kozy_data.sources.psp_events:PspEventsDownloader",
    "sewik_accidents": "kozy_data.sources.sewik_accidents:SewikAccidentsDownloader",
    "gugik": "kozy_data.sources.gugik:GugikDownloader",
    "rcb_alerts": "kozy_data.sources.rcb_alerts:RcbAlertsDownloader",
    "tauron_outages": "kozy_data.sources.tauron_outages:TauronOutagesDownloader",
    "kozy_news": "kozy_data.sources.kozy_news:KozyNewsDownloader",
}

# Order in which `fetch all` runs sources. OSM first to produce the boundary.
DEFAULT_ORDER: list[str] = [
    "osm_overpass",
    "spatial_grid",
    "open_meteo",
    "gios_air",
    "gus_bdl",
    "meteoalarm",
    "imgw_meteo",
    "imgw_hydro",
    "imgw_warnings",
    "psp_events",
    "sewik_accidents",
    "gugik",
    "rcb_alerts",
    "tauron_outages",
    "kozy_news",
]


def get_downloader(name: str) -> "BaseDownloader":
    if name not in REGISTRY:
        raise KeyError(f"unknown source '{name}'. Known: {sorted(REGISTRY)}")
    module_path, cls_name = REGISTRY[name].split(":")
    module = importlib.import_module(module_path)
    return getattr(module, cls_name)()


def available() -> list[str]:
    return list(REGISTRY)
