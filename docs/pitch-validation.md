# Testing

How we check that the pitch, the data and the audio are correct.

## Automated tests

Run them with:

```bash
python3 -m unittest discover -s tests
```

There are 18 tests in `tests/`:

- **EIC map** (`test_eic_map.py`): the recoloured palette runs from blue to orange, Europe reads cooler than the Middle East, and reprojection keeps the order of cool to warm values.
- **ERA5 wind** (`test_era5_layer.py`): wind is sampled correctly inside the grid and left empty outside it, only Nico's matching hours are used, and the packed grid keeps its hourly order.
- **Audio** (`test_generate_audio.py`): smoothing never fills tracking gaps, weather is never stretched past the last recorded hour, gap days stay silent but keep their place in the calendar, each composition fills its timeline exactly, the wind stem is silent wherever no wind was recorded, the soundfont renders each instrument track, a warmer colour under a bird gives it a higher chord, and the NASA image chords stay in their own stem apart from the GPS-track melody, beat and drone.

The soundfont test is skipped if FluidSynth is not installed.

## Build check

`quarto render` builds both pages (`index.html` and `documentation.html`) into `_site/`. `node --check` confirms the JavaScript files parse.

## What we check by hand before presenting

- All seven chapters, on a laptop screen and on a phone
- Play, pause, seeking and switching birds
- The kettle, each sound on its own, and all sounds together in chapter 5
- The two-minute timer (P) and the speaker notes (O)
- Fullscreen (F) on the projector

## Data limits

- The wind grid covers 21 hours on 30 October 2024 over the Bay of Biscay. Only Nico is inside it. Eric and Sanne have no wind data, so their wind stems are silent.
- Positions between daily GPS records are interpolated. They are not measured hourly fixes.
- The NASA EIC frames are from 2026 and the gull tracks are from 2024–25. The image colour is used as a relative score, not as the temperature each bird experienced.
