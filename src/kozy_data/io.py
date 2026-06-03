"""Output helpers: Parquet/GeoJSON writers + provenance manifest."""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from .config import PROCESSED_DIR, RAW_DIR

log = logging.getLogger("kozy_data.io")


def raw_dir(source: str) -> Path:
    p = RAW_DIR / source
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ensure_processed() -> Path:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    return PROCESSED_DIR


def save_raw_bytes(source: str, filename: str, content: bytes) -> Path:
    path = raw_dir(source) / filename
    path.write_bytes(content)
    return path


def save_raw_json(source: str, filename: str, obj: Any) -> Path:
    path = raw_dir(source) / filename
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_table(df: pd.DataFrame, name: str) -> Path:
    """Write a normalized table to data/processed/<name>.parquet."""
    out = _ensure_processed() / f"{name}.parquet"
    df.to_parquet(out, index=False)
    log.info("wrote %s rows=%d cols=%d", out.name, len(df), df.shape[1])
    return out


def save_geojson(feature_collection: dict[str, Any], name: str) -> Path:
    out = _ensure_processed() / f"{name}.geojson"
    out.write_text(json.dumps(feature_collection, ensure_ascii=False), encoding="utf-8")
    log.info("wrote %s features=%d", out.name, len(feature_collection.get("features", [])))
    return out


def write_manifest(source: str, *, license: str, urls: list[str],
                   n_records: int, date_range: tuple[str | None, str | None],
                   outputs: list[str], extra: dict[str, Any] | None = None) -> Path:
    manifest = {
        "source": source,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "license": license,
        "urls": urls,
        "n_records": n_records,
        "date_range": {"start": date_range[0], "end": date_range[1]},
        "outputs": outputs,
        "extra": extra or {},
    }
    path = raw_dir(source) / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
