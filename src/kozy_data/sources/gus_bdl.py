"""GUS Bank Danych Lokalnych (BDL) API: yearly statistics for gmina Kozy.

Resolves the BDL unit id for the gmina, then pulls a configurable set of
variables. Yearly observations are emitted with a Jan-1 ``timestamp`` plus the
gmina centroid as geo-reference.

Output: timestamp, lat, lon, unit_id, unit_name, variable_id, variable_name, value.
"""
from __future__ import annotations

import logging

import pandas as pd

from ..base import BaseDownloader, FetchResult
from .. import http

log = logging.getLogger("kozy_data.bdl")

BASE = "https://bdl.stat.gov.pl/api/v1"
# Sensible default variables (override via config/sources.yaml: gus_bdl.variable_ids).
# 72305 = ludność ogółem; 60559 = gęstość zaludnienia; 60562/60563 = obciążenie dem.;
# 60565-60567 = grupy wieku; 60572/60573 = pow. mieszkań.
DEFAULT_VARS = [72305, 60559, 60562, 60563, 60565, 60566, 60567, 60572, 60573]


class GusBdlDownloader(BaseDownloader):
    name = "gus_bdl"
    license = "GUS BDL (free reuse, attribution)"

    def _resolve_unit(self) -> dict | None:
        """Find the BDL unit whose id embeds the gmina TERYT (or matches by name)."""
        terc = self.aoi.teryt_gmina
        res = http.get_json(f"{BASE}/units/search", params={
            "name": self.aoi.name, "format": "json", "page-size": 100,
        }, timeout=60)
        results = res.get("results", res if isinstance(res, list) else [])
        # BDL unit ids embed the TERYT code; prefer an exact embed match.
        for u in results:
            uid = str(u.get("id", ""))
            if terc in uid:
                return u
        # fallback: lowest-level unit named exactly like the gmina
        named = [u for u in results if u.get("name", "").upper() == self.aoi.name.upper()]
        if named:
            named.sort(key=lambda u: u.get("level", 0), reverse=True)
            return named[0]
        return None

    def _variable_name(self, var_id: int) -> str:
        try:
            v = http.get_json(f"{BASE}/variables/{var_id}", params={"format": "json"},
                              timeout=60)
            parts = [v.get(k) for k in ("n1", "n2", "n3", "n4", "n5")]
            return " / ".join(p for p in parts if p) or str(var_id)
        except Exception:  # noqa: BLE001
            return str(var_id)

    def _fetch_variable(self, unit_id: str, var_id: int) -> list[dict]:
        data = http.get_json(f"{BASE}/data/by-unit/{unit_id}", params={
            "var-id": var_id, "format": "json", "page-size": 100,
        }, timeout=60)
        rows = []
        # response: {"results":[{"id":varId,"values":[{"year":"2020","val":...}]}]}
        for res in data.get("results", []):
            for val in res.get("values", []):
                if val.get("val") is None:
                    continue
                rows.append({"variable_id": var_id, "year": val.get("year"),
                             "value": val.get("val")})
        return rows

    def run(self, since=None) -> FetchResult:
        unit = self._resolve_unit()
        if not unit:
            return FetchResult(self.name, 0, note="could not resolve BDL unit id")
        unit_id = str(unit["id"])
        start_year = self.aoi.start_date.year
        var_ids = self.cfg.get("variable_ids") or DEFAULT_VARS
        rows = []
        for var_id in var_ids:
            try:
                vrows = self._fetch_variable(unit_id, var_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("BDL var %s failed: %s", var_id, exc)
                continue
            vname = self._variable_name(var_id)
            for r in vrows:
                rows.append({**r, "variable_name": vname})
        if not rows:
            return FetchResult(self.name, 0, note=f"unit {unit_id}, no variable data")
        df = pd.DataFrame(rows)
        df["year"] = pd.to_numeric(df["year"], errors="coerce")
        df = df[df["year"] >= start_year]
        df["timestamp"] = pd.to_datetime(df["year"].astype("Int64").astype(str)
                                         + "-01-01", utc=True, errors="coerce")
        df["unit_id"] = unit_id
        df["unit_name"] = unit.get("name")
        df["lat"] = self.aoi.centroid_lat
        df["lon"] = self.aoi.centroid_lon
        df["source"] = self.name
        df = df[["timestamp", "year", "lat", "lon", "unit_id", "unit_name",
                 "variable_id", "variable_name", "value", "source"]]
        return self.emit(df, "gus_bdl_stats", urls=[BASE],
                         note=f"unit {unit_id}, {len(var_ids)} vars")
