"""kozy_data: spatio-temporal data acquisition for gmina Kozy (powiat bielski).

Each data source lives in :mod:`kozy_data.sources` and subclasses
:class:`kozy_data.base.BaseDownloader`, producing normalized Parquet/GeoJSON
files with time + geo metadata under ``data/``.
"""

__version__ = "0.1.0"
