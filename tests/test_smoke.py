"""Smoke tests that do not hit the network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd

from kozy_data.config import load_aoi, load_sources
from kozy_data.geo import haversine_km
from kozy_data.sources import DEFAULT_ORDER, REGISTRY, get_downloader
from kozy_data.sources.osm_overpass import _assemble_rings


def test_aoi_loads():
    aoi = load_aoi()
    assert aoi.name == "Kozy"
    assert aoi.teryt_gmina == "2402072"
    assert 49 < aoi.centroid_lat < 50
    assert 18 < aoi.centroid_lon < 20


def test_every_source_importable():
    for name in REGISTRY:
        dl = get_downloader(name)
        assert dl.name == name


def test_default_order_matches_registry():
    assert set(DEFAULT_ORDER) == set(REGISTRY)


def test_sources_config_has_all():
    cfg = load_sources()
    for name in REGISTRY:
        assert name in cfg, f"{name} missing from sources.yaml"


def test_haversine_known_distance():
    # Kraków <-> Warszawa ~ 252 km
    d = haversine_km(50.0647, 19.9450, 52.2297, 21.0122)
    assert 240 < d < 265


def test_assemble_rings_closes_square():
    ways = [
        [(0.0, 0.0), (1.0, 0.0)],
        [(1.0, 0.0), (1.0, 1.0)],
        [(1.0, 1.0), (0.0, 1.0)],
        [(0.0, 1.0), (0.0, 0.0)],
    ]
    rings = _assemble_rings(ways)
    assert len(rings) == 1
    assert rings[0][0] == rings[0][-1]  # closed


def test_emit_writes_parquet(tmp_path, monkeypatch):
    import kozy_data.io as io_mod
    monkeypatch.setattr(io_mod, "PROCESSED_DIR", tmp_path)
    monkeypatch.setattr(io_mod, "RAW_DIR", tmp_path / "raw")
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2020-01-01", "2021-06-01"], utc=True),
        "lat": [49.8, 49.8], "lon": [19.1, 19.1],
        "variable": ["t", "t"], "value": [1.0, 2.0],
    })
    dl = get_downloader("open_meteo")
    res = dl.emit(df, "unit_test_table", urls=["http://example"])
    assert res.n_records == 2
    assert res.date_range == ("2020-01-01", "2021-06-01")
    assert (tmp_path / "unit_test_table.parquet").exists()
