"""Offline parser tests: feed canned API payloads, assert normalized output.

These validate the fetch->parse->emit transforms without network access.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd
import pytest

import kozy_data.http as http_mod
import kozy_data.io as io_mod
from kozy_data.sources import get_downloader


@pytest.fixture
def tmp_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(io_mod, "PROCESSED_DIR", tmp_path / "processed")
    monkeypatch.setattr(io_mod, "RAW_DIR", tmp_path / "raw")
    return tmp_path


def test_open_meteo_long_format(tmp_outputs, monkeypatch):
    payload = {
        "hourly_units": {"temperature_2m": "°C", "precipitation": "mm"},
        "hourly": {
            "time": ["2020-01-01T00:00", "2020-01-01T01:00"],
            "temperature_2m": [1.5, 2.0],
            "precipitation": [0.0, 0.3],
        },
    }
    monkeypatch.setattr(http_mod, "get_json", lambda *a, **k: payload)
    dl = get_downloader("open_meteo")
    dl.cfg["hourly"] = ["temperature_2m", "precipitation"]
    res = dl.run(since="2020-01-01")
    assert res.n_records == 4  # 2 timestamps x 2 variables
    out = pd.read_parquet(tmp_outputs / "processed" / "open_meteo_weather.parquet")
    assert set(out.columns) >= {"timestamp", "lat", "lon", "variable", "value", "unit"}
    assert set(out["variable"]) == {"temperature_2m", "precipitation"}
    assert (out["lat"] - 49.845).abs().lt(1e-4).all()


def test_gus_bdl_yearly(tmp_outputs, monkeypatch):
    def fake_get_json(url, **kwargs):
        if "units/search" in url:
            return {"results": [{"id": "061412402072", "name": "Kozy", "level": 5}]}
        if "/variables/" in url:
            return {"n1": "Ludność", "n2": "ogółem"}
        if "data/by-unit" in url:
            return {"results": [{"id": 72305, "values": [
                {"year": "2019", "val": 13000},
                {"year": "2020", "val": 13100},
                {"year": "2021", "val": 13200},
            ]}]}
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(http_mod, "get_json", fake_get_json)
    dl = get_downloader("gus_bdl")
    res = dl.run(since="2020-01-01")
    # 2019 dropped (before start), 2020+2021 kept
    assert res.n_records == 2
    out = pd.read_parquet(tmp_outputs / "processed" / "gus_bdl_stats.parquet")
    assert out["year"].min() == 2020
    assert set(out["variable_name"]) == {"Ludność / ogółem"}
    assert out["unit_id"].iloc[0] == "061412402072"


def test_osm_boundary_bbox_roundtrip(tmp_path, monkeypatch):
    """OSM relation -> boundary geojson -> geo helpers compute a valid bbox."""
    import json
    import kozy_data.geo as geo_mod

    bpath = tmp_path / "kozy_boundary.geojson"
    monkeypatch.setattr(geo_mod, "BOUNDARY_PATH", bpath)
    fc = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [[
            [19.10, 49.82], [19.18, 49.82], [19.18, 49.88], [19.10, 49.88],
            [19.10, 49.82],
        ]]},
    }]}
    bpath.write_text(json.dumps(fc), encoding="utf-8")
    bbox = geo_mod.active_bbox()
    assert bbox.min_lon == pytest.approx(19.10)
    assert bbox.max_lat == pytest.approx(49.88)
    assert geo_mod.in_aoi(49.85, 19.14) is True
    assert geo_mod.in_aoi(49.95, 19.30) is False
