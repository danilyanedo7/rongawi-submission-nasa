# Flock Around and Find Out

*Bird migration, earth data, and questionable musical decisions*

Our submission to the NASA Space Apps Challenge 2026, [The Earth Information Jukebox](https://www.spaceappschallenge.org/2026/challenges/the-earth-information-jukebox/), by team **Rongawi Iwak Tempe Solutions**:

- Edo Danilyan
- Muhammad Noor Fakhruzzaman
- Fauziyah
- Arief Purnama Muharram
- Muhammad Ikhsan

A web page where you can follow three gulls on their autumn migration by watching, by listening, or both. The harmony comes from NASA Earth Information Center (EIC) temperature imagery, and the melody and rhythm come from the birds' GPS tracks. The full write-up is in `documentation.html` (the **Docs** button on the pitch).

## The story

Eric, Nico and Sanne are three lesser black-backed gulls from the colony at Zeebrugge, Belgium. All three left in autumn 2024, came back in spring 2025, and spent the winter in different places.

| Bird | Left | Back | Winter | Distance |
| --- | --- | --- | --- | --- |
| Eric | 22 Oct 2024 | 19 Mar 2025 | Inland Spain | 20,532 km |
| Nico | 27 Oct 2024 | 12 Apr 2025 | Dakhla | 15,646 km |
| Sanne | 11 Nov 2024 | 27 Mar 2025 | Dakhla | 20,532 km |

## Data

- **NASA EIC imagery:** [GEOS-FP Near-Surface Temperature (SVS 5147)](https://svs.gsfc.nasa.gov/5147/), 60 frames from 30 October to 14 November 2026. The EIC only keeps the latest two weeks, so the frames are played across each journey from departure to return, like a score. The map and the music always use the same frame.
- **Gull tracks:** INBO's open [LBBG_ADULT](https://doi.org/10.5281/zenodo.22083084) dataset (CC0), processed with the R scripts from our earlier migration essay. A stopover is at least two days within 20 km, and a tracking gap is a full day without a position.
- **Wind:** hourly ERA5 wind at 100 m from the [Open-Meteo archive](https://open-meteo.com/en/docs/historical-weather-api) for 30 October 2024, when Nico crossed the Bay of Biscay. It is only used in chapter 5.

## How the sound is made

1. For each moment, read the EIC colour under the bird and place it on NASA's colour scale (0 is the coldest colour, 1 the warmest).
2. `scripts/story_to_midi.py` turns each journey into a 30-second MIDI file: 26 one-second steps from departure to return, then a 4-second ending at home, in D minor pentatonic. The warmth under the bird sets the pad chord, latitude sets the flute melody (higher in the north), daily distance sets the marimba beat (up to four pulses per beat, a held note on rest days), distance from the colony sets the loudness of a low cello drone, and the melody is mirrored on the way back. `scripts/route_to_midi.py` composes the short wind example, where the wind sets the tempo and the pad interval.
3. `scripts/generate_audio.py` plays the MIDI through FluidSynth and `scripts/soundfont/mbgm.SF2`. Each instrument is saved as its own track, so every map layer can switch its sound on and off.

## Colour and type

- **Map:** MetBrewer Hiroshige, reversed, from `#1e466e` (cooler) to `#e76254` (warmer). The switch from blue to orange is placed at NASA's own orange, and the layer is drawn at 50% opacity.
- **Birds:** Eric `#a8201a`, Nico `#0a9396`, Sanne `#ec9a29`, always shown with their names.
- **Type:** Satoshi, stored in `assets/fonts/` so the page works offline.

## Accessibility

Every journey can be followed by ear, and each recording on the documentation page has a written description. The documentation page has proper headings, a skip link, a section menu, keyboard support, large high-contrast text in light and dark themes, and a text alternative for the route map. Nothing is shown by colour alone, and animations stop when the device is set to reduce motion.

## Run

```bash
quarto render
python3 -m http.server 4173 --directory _site   # open http://localhost:4173/
```

Press P to run the timed two-minute pitch. Use the arrow keys to change chapters, Space to play, 1, 2 or 3 to pick a bird, F for full screen, R to reset and O for the speaker notes.

## Rebuild the data and audio

```bash
brew install ffmpeg fluid-synth
python3 -m pip install -r requirements.txt
python3 scripts/fetch_wind_grid.py      # ERA5 wind for 30 October 2024
python3 scripts/build_era5_layer.py
python3 scripts/build_eic_layer.py      # recolour and reproject the EIC frames
python3 scripts/generate_audio.py       # MIDI, MP3 mixes and stems
python3 -m unittest discover -s tests
```

[Pitch notes](docs/pitch-notes.md) cover the timing of each chapter and the data choices.

## Tech stack

Python (NumPy, pandas, Pillow, mido), R (dplyr, sf), FluidSynth and FFmpeg for the audio, and Quarto, D3 and the Web Audio API for the web pages.

## Use of AI tools

The idea, the story and the design direction come from our team: following three gulls, pairing their journeys with NASA EIC imagery, the kettle example, and choosing which data drives each sound.

We used AI assistants, including Claude by Anthropic, as development tools. They helped us:

- **Reproducibility:** turn our one-off processing steps into scripts that rebuild the data, map layers and audio from the original sources, with step-by-step instructions.
- **Documentation:** write this README, the documentation page and the pitch notes.
- **Code:** write and refactor the web page, the Python build scripts, the MIDI composer and the tests. The team reviewed and ran all of it.

No images, audio or data were generated by AI. The music is made by our own mapping script and FluidSynth from NASA EIC imagery and GPS tracks, and the gull artwork was made by a team member.
