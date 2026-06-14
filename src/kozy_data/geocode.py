"""Local street geocoding for gmina Kozy via named OSM highways.

Outage / news texts name streets ("Kozy ulica Cedrowa, Sadowa, Krańcowa…").
We already hold every named road as line geometry in ``osm_lines.parquet``, so a
street name can be resolved to a representative coordinate by a pure-local lookup
— no external geocoder, no rate limits.

The matcher scans known street names against the (diacritic-folded) text rather
than splitting on commas, which is robust to the messy "na odcinku od nr 12 do
nr 30" fragments Tauron embeds between street names.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache

import pandas as pd
from shapely import wkt

from .config import PROCESSED_DIR

log = logging.getLogger("kozy_data.geocode")

LINES_PATH = PROCESSED_DIR / "osm_lines.parquet"

# Polish diacritic folding + street-type prefixes to drop before matching.
_DIACRITICS = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
_PREFIX_RE = re.compile(r"\b(ul|ulica|al|aleja|os|osiedle|pl|plac)\.?\s+", re.IGNORECASE)


def normalize(text: str) -> str:
    """Lowercase, fold Polish diacritics, drop street-type prefixes/punctuation."""
    text = text.translate(_DIACRITICS).lower()
    text = _PREFIX_RE.sub(" ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=1)
def street_index() -> dict[str, tuple[float, float]]:
    """Map normalized street name -> representative (lat, lon).

    Built from named ``highway`` geometries in ``osm_lines.parquet``; a street
    split across several ways is averaged over all its vertices.
    """
    if not LINES_PATH.exists():
        log.warning("osm_lines.parquet missing — street index is empty")
        return {}
    df = pd.read_parquet(LINES_PATH)
    df = df[(df["category"] == "highway") & df["name"].notna()]
    sums: dict[str, list[float]] = {}
    for name, geom_wkt in zip(df["name"], df["geometry_wkt"]):
        key = normalize(str(name))
        if not key:
            continue
        coords = list(wkt.loads(geom_wkt).coords)  # (lon, lat) pairs
        lat = sum(c[1] for c in coords)
        lon = sum(c[0] for c in coords)
        acc = sums.setdefault(key, [0.0, 0.0, 0.0])
        acc[0] += lat
        acc[1] += lon
        acc[2] += len(coords)
    return {k: (v[0] / v[2], v[1] / v[2]) for k, v in sums.items() if v[2]}


def match_streets(text: str) -> list[dict[str, float | str]]:
    """Return [{street, lat, lon}, …] for known streets named in *text*.

    Matches whole-word occurrences of each indexed street name in the
    normalized text, so embedded house-number fragments are ignored.
    """
    if not text:
        return []
    haystack = f" {normalize(text)} "
    out = []
    for key, (lat, lon) in street_index().items():
        if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", haystack):
            out.append({"street": key, "lat": lat, "lon": lon})
    return out
