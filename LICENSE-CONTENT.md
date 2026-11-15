# Content licence

Flock Around and Find Out, by Rongawi Iwak Tempe Solutions (Edo Danilyan, Muhammad Noor Fakhruzzaman, Fauziyah, Arief Purnama Muharram, Muhammad Ikhsan), 2026.

## What is covered

The content we made is licensed under the [Creative Commons Attribution 4.0 International licence (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/). This includes:

- the text of the pitch, the documentation page, the README and the notes in `docs/`
- the music: the MIDI files and MP3 recordings in `assets/audio/`
- the data files we derived, in `assets/data/` (except those listed below)
- the recoloured map frames in `assets/eic-map/`, the gull artwork and the visual design

You may share and adapt this content for any purpose, including commercially, as long as you give credit, link to the licence and say if you changed anything. A suitable credit is:

> *Flock Around and Find Out* by Rongawi Iwak Tempe Solutions, CC BY 4.0

The code is under the MIT licence (see `LICENSE`).

## Third-party material

These parts are not ours and keep their own terms:

| What | Where | Source and terms |
| --- | --- | --- |
| NASA EIC imagery | `assets/eic-temperature/`, and the recoloured `assets/eic-map/` derived from it | NASA Scientific Visualization Studio, [GEOS-FP Near-Surface Temperature (SVS 5147)](https://svs.gsfc.nasa.gov/5147/). NASA imagery is generally not subject to copyright in the United States; see [NASA's media usage guidelines](https://www.nasa.gov/nasa-brand-center/images-and-media/). Use does not imply NASA endorsement. |
| Gull tracks | `assets/data/daily.json`, `stopovers.json`, `totals.json`, `stats.json` (derived) | Stienen, E.W.M. et al. (2026), [LBBG_ADULT](https://doi.org/10.5281/zenodo.22083084), INBO, CC0 1.0. |
| ERA5 wind | `assets/data/nico_wind_grid.json`, `era5_wind.json`, `era5_wind_grid.*` | ERA5 via the [Open-Meteo](https://open-meteo.com/) Historical Weather API, CC BY 4.0. Contains modified Copernicus Climate Change Service information 2024. Neither the European Commission nor ECMWF is responsible for any use of this information. |
| Country boundaries | `assets/data/countries-50m.json` | [Natural Earth](https://www.naturalearthdata.com/) (public domain), via world-atlas (ISC). |
| D3 and topojson-client | `assets/vendor/` | Mike Bostock, ISC licence. |
| Satoshi typeface | `assets/fonts/` | Indian Type Foundry, [ITF Free Font License](https://www.fontshare.com/fonts/satoshi) via Fontshare. |
| MetBrewer palette | colour values in `scripts/build_eic_layer.py` | [MetBrewer](https://github.com/BlakeRMills/MetBrewer) by Blake Robert Mills. |
| General MIDI soundfont | not included (`scripts/soundfont/`, ignored by git) | `mbgm.SF2`, © 1997 E-mu Systems. Used locally to render the audio; not redistributed and not covered by our licence. |
