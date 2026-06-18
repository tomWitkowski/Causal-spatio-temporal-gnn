# Spatio-Temporal GNN — dane gminy Kozy

Pozyskiwanie danych **czasowo-przestrzennych** dla **gminy Kozy** (powiat bielski, woj. śląskie)
do eksperymentów ze spatio-temporal GNN.

## Instalacja

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Szybki start — pełne pobieranie danych

**Pierwsze uruchomienie** (pobiera wszystko od 2020):

```bash
# 1. Granica gminy + punkty OSM (statyczne, ~2 min)
python -m kozy_data fetch osm_overpass

# 2. Siatka 50 punktów z wysokościami (kilka sekund)
python -m kozy_data fetch spatial_grid

# 3. Pogoda ERA5 dla 50 punktów od 2020 (~45 min, 50×API calls)
python -m kozy_data fetch open_meteo --since 2020-01-01

# 4. Reszta źródeł (szybkie)
python -m kozy_data fetch gios_air
python -m kozy_data fetch gus_bdl
python -m kozy_data fetch imgw_meteo --since 2020-01-01
python -m kozy_data fetch imgw_hydro --since 2020-01-01
python -m kozy_data fetch imgw_warnings --since 2020-01-01
python -m kozy_data fetch meteoalarm
python -m kozy_data fetch rcb_alerts
python -m kozy_data fetch tauron_outages --since 2020-01-01
python -m kozy_data fetch kozy_news
```

**Aktualizacja** (dopisuje brakujące dane, bez --since):

```bash
python -m kozy_data fetch all
```

`fetch all` tylko **dopisuje brakujące dane**: szeregi czasowe (`open_meteo`,
`imgw_*`) wznawiają się od ostatniego zapisanego rekordu, a źródła zdarzeń
(`meteoalarm`, `rcb_alerts`, `tauron_outages`, `gios_air`, `kozy_news`) pobierają
bieżący stan i łączą go z istniejącym (dedup). Statyczne migawki (`osm_overpass`,
`spatial_grid`) są pomijane, jeśli już istnieją — wymuś odświeżenie przez `--since`.
Nie trzeba czyścić plików przed aktualizacją.

**Lista źródeł i ich stan:**

```bash
python -m kozy_data list
```

## Wyniki

```
data/processed/
  open_meteo_weather.parquet     # 33M+ wierszy, 50 pkt × 12 zmiennych, 2020–dziś
  imgw_meteo_daily.parquet       # PSZCZYNA, dzienne, 2020–dziś
  imgw_hydro_daily.parquet       # 4 wodowskazy, dzienne, 2023–dziś
  imgw_warnings.parquet          # ostrzeżenia + prognozy hydrologiczne, 2017–dziś
  gios_air_measurements.parquet  # jakość powietrza, stacje w promieniu 30 km
  gus_bdl_stats.parquet          # demografia gminy, roczne, 2020–2025
  osm_features.parquet           # 8274 węzłów mapy z elevation_m
  spatial_grid.parquet           # 50 punktów siatki z elevation_m
  meteoalarm_warnings.parquet    # ostrzeżenia pogodowe IMGW dla powiatu bielskiego (akumulowane)
  rcb_alerts.parquet             # alerty RCB istotne dla Kóz (akumulowane)
  tauron_outages.parquet         # przerwy w dostawie prądu w Kozach, 2020–dziś
  kozy_news.parquet              # aktualności z kozy.pl
  kozy_boundary.geojson          # granica gminy (AOI)
```

> `data/` jest w `.gitignore` — commitujemy kod, nie dane.

## Źródła danych

Każdy wiersz = jedno źródło (`sources/<nazwa>.py`). „Tryb" mówi, jak rośnie zbiór:
**archiwum** = pełna historia z plików IMGW/API; **akumulacja** = dane tylko
bieżące, dopisywane przy każdym uruchomieniu (brak archiwum u źródła).

| Źródło | Co dostarcza | Zakres | Tryb | Uwagi |
|--------|-------------|--------|------|-------|
| `open_meteo` | Pogoda ERA5 hourly (12 zmiennych) | 2020–dziś | archiwum | 50 pkt siatki, `elevation_m` |
| `imgw_meteo` | Meteo dzienne IMGW (PSZCZYNA) | 2020–dziś | archiwum | Najbliższa stacja klimat |
| `imgw_hydro` | Poziom wody / przepływ (4 wodowskazy) | 2023–dziś | archiwum | Wapienica, Biała, Soła |
| `imgw_warnings` | Ostrzeżenia + prognozy **hydrologiczne** | 2017–dziś | archiwum + akumulacja | Zlewnie Soły/Małej Wisły/Beskidów; PNZH (prognoza) tylko bieżąca |
| `meteoalarm` | Ostrzeżenia **pogodowe** IMGW (CAP) | od 1. uruchomienia | akumulacja | powiat bielski (PL2402); brak maszynowego archiwum |
| `rcb_alerts` | Alerty RCB istotne dla Kóz | od 1. uruchomienia | akumulacja | Filtr: śląskie/bielski/Kozy + drogi |
| `gios_air` | Jakość powietrza (PM2.5, PM10, NO2…) | ostatnie dni | akumulacja | API bez archiwum historycznego |
| `gus_bdl` | Demografia gminy (ludność, gęstość…) | 2020–2025 | archiwum | Roczne |
| `osm_overpass` | Mapa POI/drogi/budynki + elevation | statyczne | snapshot | 8274 węzłów |
| `spatial_grid` | 50 jednorodnych punktów siatki | statyczne | snapshot | elevation z Open-Meteo |
| `tauron_outages` | Przerwy w dostawie prądu w Kozach | 2020–dziś | archiwum + akumulacja | API `/waapi/outages` |
| `kozy_news` | Aktualności gminy | ostatnie ~15 | akumulacja | — |

### Ostrzeżenia i prognozy zagrożeń (hydro / pogoda / drogi)

| Typ | Ostrzeżenia (bieżące) | Historia | Gdzie |
|-----|----------------------|----------|-------|
| Hydrologiczne | ✅ | ✅ archiwum TXT od 2017 | `imgw_warnings` (+ prognoza PNZH) |
| Pogodowe | ✅ (CAP) | ❌ — archiwum IMGW to tylko PDF-y | `meteoalarm` |
| Drogowe | — | ❌ — prognoza „ZUK" publikowana tylko jako PDF | nieobsługiwane |

Maszynowo dostępna historia istnieje **tylko dla ostrzeżeń hydrologicznych**
(archiwum IMGW `ost_hydro/<rok>/<MM>.zip`, pliki tekstowe). Ostrzeżenia pogodowe i
prognozy zagrożeń drogowych IMGW udostępnia wyłącznie jako PDF-y — zbieramy je
więc na bieżąco (`meteoalarm`), bez wstecznego archiwum.

## Architektura

```
src/kozy_data/
  config.py   base.py   http.py   geo.py   io.py   __main__.py
  sources/    # jeden plik = jedno źródło (BaseDownloader.run)
config/
  kozy.yaml       # AOI, bbox, TERYT, centroid
  sources.yaml    # parametry i enabled per źródło
notebooks/
  explore_data.ipynb  # diagnostyka wszystkich danych
```

Każde źródło: `fetch → parse → concat z istniejącym → dedup → emit (Parquet + manifest)`.
Nowe źródło = plik w `sources/` + wpis w `REGISTRY` (`sources/__init__.py`) + sekcja w `sources.yaml`.
