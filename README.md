# Causal Spatio-Temporal GNN — dane gminy Kozy

Pozyskiwanie danych **czasowo-przestrzennych** dla **gminy Kozy** (powiat bielski,
woj. śląskie) do eksperymentów ze spatio-temporal GNN.

Ten etap dostarcza **katalog źródeł** ([docs/DATA_SOURCES.md](docs/DATA_SOURCES.md))
oraz **działające pobieranie** danych od 2020 r., zapisywane jako Parquet/GeoJSON
z metadanymi geo + czas. Struktura grafu (węzły) zostanie zaprojektowana w kolejnym
kroku — tutaj zbieramy dane surowe, znormalizowane do jednolitego schematu.

## Instalacja

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .            # rdzeń (requests, pandas, pyarrow, shapely, bs4)
pip install -e ".[geo]"     # opcjonalnie: geopandas/owslib (źródło GUGiK)
pip install -e ".[dev]"     # testy
```

## Użycie

```bash
python -m kozy_data list                         # lista źródeł + stan enabled
python -m kozy_data fetch open_meteo --since 2020-01-01
python -m kozy_data fetch all --since 2020-01-01  # wszystkie włączone źródła
python scripts/fetch_all.py --since 2020-01-01    # to samo, wrapper
```

Wyniki:
- `data/processed/*.parquet` — znormalizowane tabele (szeregi czasowe / zdarzenia),
- `data/processed/kozy_boundary.geojson` — granica gminy (AOI), pobierana z OSM,
- `data/raw/<źródło>/manifest.json` — proweniencja (URL, licencja, liczba rekordów,
  zakres dat, `fetched_at`).

> `data/` jest w `.gitignore` — commitujemy kod i `docs/`, nie surowe dane.

## Schemat znormalizowany

- **Szeregi czasowe** (pogoda, powietrze, statystyka): `timestamp` (UTC), `lat`,
  `lon` (lub `station`), `variable`, `value`, `unit`, `source`.
- **Zdarzenia punktowe** (ostrzeżenia, alerty, news): `timestamp` (lub `start`/`end`),
  `lat`, `lon`, `category`, opis, `source`.

## Architektura

```
src/kozy_data/
  config.py   base.py   http.py   geo.py   io.py   __main__.py (CLI)
  sources/    # jeden moduł = jedno źródło (BaseDownloader)
config/        # kozy.yaml (AOI) + sources.yaml (parametry/enabled)
scripts/       # fetch_all.py
tests/         # smoke testy (bez sieci)
docs/          # DATA_SOURCES.md — katalog źródeł
```

Każde źródło dziedziczy po `BaseDownloader` i implementuje `run(since)`:
`fetch → parse → zapis (Parquet/GeoJSON) + manifest`. Nowe źródło = nowy moduł
w `sources/` + wpis w `REGISTRY` (`sources/__init__.py`) + sekcja w `sources.yaml`.

## Status źródeł

Tier **A** (pełne pobieranie >2020, włączone): `open_meteo`, `gios_air`, `gus_bdl`,
`osm_overpass`, `imgw_warnings`, `imgw_meteo`.
Tier **C** (scrapery best-effort, włączone): `rcb_alerts`, `kozy_news`.
Tier **B/C** (szkielety, wyłączone — gotowe do implementacji): `imgw_hydro`,
`psp_events`, `sewik_accidents`, `gugik`, `tauron_outages`.

Pełny opis i endpointy: [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).
