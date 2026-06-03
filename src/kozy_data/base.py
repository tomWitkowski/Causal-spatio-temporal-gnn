"""BaseDownloader: the contract every data source implements."""
from __future__ import annotations

import datetime as dt
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from . import io
from .config import AOIConfig, load_aoi, source_config

log = logging.getLogger("kozy_data.base")


@dataclass
class FetchResult:
    """Outcome of a single source run."""

    source: str
    n_records: int
    outputs: list[str] = field(default_factory=list)
    date_range: tuple[str | None, str | None] = (None, None)
    note: str = ""

    def __str__(self) -> str:
        rng = f"{self.date_range[0]}..{self.date_range[1]}"
        return f"[{self.source}] records={self.n_records} range={rng} {self.note}".strip()


class BaseDownloader(ABC):
    """Subclasses set ``name``/``license`` and implement :meth:`run`.

    Conventions for normalized outputs (saved via :mod:`kozy_data.io`):
      * time series  -> columns include ``timestamp`` (UTC), plus ``lat``/``lon``
        (or station id), ``variable``, ``value``, ``unit``, ``source``.
      * point events -> columns include ``timestamp`` (or ``start``/``end``),
        ``lat``/``lon`` (or ``geometry`` for GeoJSON), ``category``, ``source``.
    """

    name: str = "base"
    license: str = "see source terms"

    def __init__(self, aoi: AOIConfig | None = None) -> None:
        self.aoi = aoi or load_aoi()
        self.cfg: dict[str, Any] = source_config(self.name)

    # --- helpers -------------------------------------------------------
    def default_start(self) -> dt.date:
        return self.aoi.start_date

    def date_window(self, since: str | dt.date | None) -> tuple[dt.date, dt.date]:
        start = (
            dt.date.fromisoformat(since) if isinstance(since, str)
            else (since or self.default_start())
        )
        return start, dt.date.today()

    def emit(self, df: pd.DataFrame, table_name: str, *, urls: list[str],
             time_col: str | None = "timestamp", note: str = "") -> FetchResult:
        """Save a table + manifest and build a FetchResult."""
        out = io.save_table(df, table_name)
        rng: tuple[str | None, str | None] = (None, None)
        if time_col and time_col in df.columns and len(df):
            ts = pd.to_datetime(df[time_col], errors="coerce", utc=True).dropna()
            if len(ts):
                rng = (ts.min().date().isoformat(), ts.max().date().isoformat())
        io.write_manifest(self.name, license=self.license, urls=urls,
                          n_records=len(df), date_range=rng,
                          outputs=[out.name])
        return FetchResult(self.name, len(df), [out.name], rng, note)

    # --- contract ------------------------------------------------------
    @abstractmethod
    def run(self, since: str | dt.date | None = None) -> FetchResult:
        """Fetch -> parse -> save. Returns a FetchResult summary."""
        raise NotImplementedError
