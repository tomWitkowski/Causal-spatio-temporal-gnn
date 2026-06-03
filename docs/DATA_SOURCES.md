# Katalog źródeł danych — gmina Kozy (powiat bielski)

Wszystkie dane są czasowo-przestrzenne (możliwe przypisanie do czasu i lokalizacji)
i ograniczone do gminy Kozy / powiatu bielskiego, zakres od **2020-01-01**.

Tier: **A** = pełny dostęp programistyczny z historią ≥2020 · **B** = dostęp
programistyczny, historia ograniczona/roczna · **C** = scraping / zbieranie „od teraz".

AOI: centroid `49.845 N, 19.142 E`, TERYT gminy `2402072`, powiat `2402`, woj. `24`.

---

## Pogoda / meteo

### 1. Open-Meteo Archive (ERA5) — tier A ✅ zaimplementowane
- Endpoint: `https://archive-api.open-meteo.com/v1/archive`
- Godzinowe dane reanalizy ERA5 dla współrzędnych centroidu, od 1940. Bez klucza.
- Opóźnienie ~5–6 dni. Licencja: CC-BY 4.0 (Open-Meteo / Copernicus).
- Moduł: `sources/open_meteo.py` → `open_meteo_weather.parquet`.

### 2. IMGW dane pomiarowo-obserwacyjne — tier A ✅ zaimplementowane
- Baza: `https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne/`
  - `dane_meteorologiczne/dobowe/{klimat|synop|opad}/<rok>/<rok>_<mm>_k.zip`
- CSV cp1250, bez nagłówka. Najbliższe stacje: Bielsko-Biała, Pszczyna, Żywiec.
- Moduł: `sources/imgw_meteo.py` → `imgw_meteo_daily.parquet`.

### 3. IMGW synop (bieżące) — tier C
- `https://danepubliczne.imgw.pl/api/data/synop` (+ `/format/csv|xml`). Tylko teraz —
  do zbierania przyrostowego.

---

## Hydrologia

### 4. IMGW ostrzeżenia hydrologiczne (archiwum) — tier A ✅ (w `imgw_warnings`)
- `https://danepubliczne.imgw.pl/data/arch/ost_hydro` — miesięczne/roczne archiwa.

### 5. IMGW dane hydrologiczne (stany/przepływy) — tier B ⏳ szkielet
- `.../dane_pomiarowo_obserwacyjne/dane_hydrologiczne/dobowe/codz/<rok>/...`
- Wodowskazy na Sole i Białej Białskiej. Moduł: `sources/imgw_hydro.py`.
- Mapa stacji: `https://hydro.imgw.pl/`.

---

## Jakość powietrza

### 6. GIOŚ — tier A (API) ✅ / archiwum ⏳
- API REST: `https://api.gios.gov.pl/pjp-api/rest/...`
  (`station/findAll`, `station/sensors/{id}`, `data/getData/{sensorId}`),
  v1: `.../pjp-api/v1/rest/...`. API daje dane bieżące (okno ~kilku dni).
- **Pełna historia 2020→**: pliki roczne „Bank danych pomiarowych":
  `https://powietrze.gios.gov.pl/pjp/archives` (XLSX per rok/zanieczyszczenie,
  ogólnopolskie — filtrować po kodzie stacji Bielsko-Biała). TODO w module.
- Moduł: `sources/gios_air.py` → `gios_air_measurements.parquet`.

---

## Alerty / katastrofy / zdarzenia

### 7. IMGW ostrzeżenia meteorologiczne (archiwum) — tier A ✅ zaimplementowane
- `https://danepubliczne.imgw.pl/data/arch/ost_meteo` — filtr po powiecie `2402`.
- Moduł: `sources/imgw_warnings.py` → `imgw_warnings.parquet` (meteo+hydro).

### 8. Alerty RCB — tier C ✅ best-effort
- Brak publicznego API. `https://www.gov.pl/web/rcb` + `https://archiwum.rcb.gov.pl`.
- Alert RCB kierowany na poziomie powiatu (od 2018). Moduł: `sources/rcb_alerts.py`.

### 9. PSP / SWD-ST — tier B ⏳ szkielet
- `dane.gov.pl` zbiory „Statystyki zdarzeń SWD PSP" (np. dataset 2080 dla 2020).
- Pożary i miejscowe zagrożenia wg powiatu/roku. Moduł: `sources/psp_events.py`.
- API CKAN: `https://api.dane.gov.pl/1.4`.

### 10. Wypadki drogowe (SEWIK / POBR) — tier B ⏳ szkielet
- `https://obserwatoriumbrd.pl/mapa-wypadkow/` (eksport tabel, od 2010, ze współrz.),
  `https://sewik.pl/` (od 2018). Przycinać do bbox gminy. Moduł: `sources/sewik_accidents.py`.

### 11. Tauron Dystrybucja — wyłączenia prądu — tier C ⏳ szkielet
- `https://www.tauron-dystrybucja.pl/wylaczenia` — wyszukiwarka wg gminy/ulicy.
  Bez historii → zbieranie od teraz. Moduł: `sources/tauron_outages.py`.

---

## Dane przestrzenne / obiekty (kontekst węzłów, statyczne)

### 12. OpenStreetMap (Overpass) — tier A ✅ zaimplementowane
- `https://overpass-api.de/api/interpreter`. Granica gminy (relacja `admin_level=7`,
  `teryt:terc=2402072`) + POI/obiekty (amenity, shop, highway, building, waterway,
  landuse, natural). Licencja: ODbL. Moduł: `sources/osm_overpass.py`
  → `kozy_boundary.geojson` (AOI) + `osm_features.parquet`.

### 13. GUGiK (PRG / BDOT10k / NMT / ortofoto) — tier B ⏳ szkielet
- PRG (granice): WFS `https://integracja.gugik.gov.pl/eziudp`.
- BDOT10k: usługi pobierania per powiat (GML). NMT/ortofoto: GeoTIFF.
- Wymaga extra `geo` (geopandas/owslib). Moduł: `sources/gugik.py`.

### 14. Geoportal Kozy / e-mapa.net — tier C
- `https://kozy.e-mapa.net/`, `https://www.geoportal2.pl/pl/g/slaskie/bielski/kozy/`
  — MPZP, działki ewidencyjne (WMS/WFS).

---

## Statystyka / zdarzenia dyskretne

### 15. GUS Bank Danych Lokalnych (BDL) — tier A ✅ zaimplementowane
- API: `https://bdl.stat.gov.pl/api/v1/` (`units/search`, `variables/{id}`,
  `data/by-unit/{unitId}?var-id=...`). Roczne dane dla gminy Kozy.
- Moduł: `sources/gus_bdl.py` → `gus_bdl_stats.parquet`. Zmienne konfigurowalne
  w `sources.yaml` (`gus_bdl.variable_ids`).

### 16. dane.gov.pl — tier B
- Krajowy portal otwartych danych, API CKAN `https://api.dane.gov.pl/1.4`
  (zbiory powiatowe/gminne, m.in. PSP, wypadki, środowisko).

### 17. Lokalne wydarzenia / ogłoszenia — tier C ✅ (`kozy_news`) / ⏳ (BIP, PKW)
- `https://kozy.pl/aktualnosci/` — aktualności gminy (zdarzenia z datami).
  Moduł: `sources/kozy_news.py`.
- `https://bip.kozy.pl/` — ogłoszenia urzędowe (TODO scraper).
- PKW — wyniki wyborów jako zdarzenia geo+czas (TODO).

---

## Inne mniej oczywiste źródła (na przyszłość)
- **Copernicus / Sentinel** (Sentinel-2 NDVI, Sentinel-5P zanieczyszczenia) — rastry
  środowiskowe dla AOI (Copernicus Data Space Ecosystem API).
- **MZ / dane.gov.pl COVID-19** — dane powiatowe 2020–2022 (zdarzenia czasowe).
- **Wikidata / Wikipedia** — atrybuty statyczne i współrzędne obiektów w Kozach.
- **WIOŚ Katowice** — komunikaty środowiskowe regionalne.
