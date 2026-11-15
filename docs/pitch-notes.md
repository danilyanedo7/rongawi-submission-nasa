# Flock Around and Find Out: two-minute pitch

The pitch is `index.qmd`, an eight-chapter story told over a live map. The longer write-up is `documentation.qmd`.

| Time | Chapter | What happens on screen |
| --- | --- | --- |
| 0:00–0:10 | Can you feel a migration? | The three routes draw in. |
| 0:10–0:22 | Where they went | The map shows the routes without sound. |
| 0:22–0:40 | Listening to a kettle | A six-second kettle heats up: the hiss grows, then the whistle rises to the boil. Leave room to hear it. |
| 0:40–0:52 | Why we used sound | Three reasons for using sound. |
| 0:52–1:14 | What each sound means | Five seconds of movement, five of the NASA image, six of Nico's wind, then six with all three together. |
| 1:14–1:40 | Pick a bird and press play | Eric for eight seconds, Nico for nine, Sanne for nine. |
| 1:40–1:50 | Distance flown each day | Daily distance for each bird, on the same scale. |
| 1:50–2:00 | Other data we could play | Ideas for fire detections, rainfall and spacecraft paths. |

Press P to run the timed pitch and O for the speaker notes. The notes are in `assets/js/app.js`. The timer follows elapsed time, so a slow frame never pushes the chapters out of sync. There is no recorded narration: the presenter speaks over the sound.

## Design

- **Map:** a full-screen dark map, with a solid dark panel behind the story text so it stays readable over the imagery.
- **Type:** Satoshi is the only typeface. It is stored in `assets/fonts/` so the page works offline on a projector.
- **Colour:** yellow marks the opening question and chapter progress. Each bird keeps one colour throughout: Eric red (`#a8201a`), Nico teal (`#0a9396`) and Sanne gold (`#ec9a29`). The gulls have white outlines so they stand out on the map.
- **NASA layer:** redrawn with the MetBrewer Hiroshige palette, reversed, so cooler is blue and warmer is orange. The switch from blue to orange sits at NASA's own orange, so Europe reads cooler than the Middle East and the Sahara. The audio still uses the original NASA colour values.
- **Gulls:** the gull artwork is by Mhdikhsan. Wing speed follows daily distance. It is a visual cue, not a measured wingbeat.
- **Motion:** movement follows the recorded route over time. Animation can be paused, and decorative motion stops when the device is set to reduce motion.
- **Mobile:** the chapter dots become a dropdown and the story moves into a bottom sheet.

## Data and sound

The journeys are composed by Ruzza's `scripts/story_to_midi.py`: 26 seconds from departure to return, then a 4-second ending at home, in D minor pentatonic. We feed it our own data for each bird: the EIC colour under the bird sets the pad chord, the image brightness its loudness, latitude the flute melody, daily distance the marimba pulses and rests, distance from the colony the loudness of a low cello drone, and longitude the stereo position. The melody is mirrored on the way back. Ranges are shared by all three birds so they can be compared. `scripts/route_to_midi.py`, also by Ruzza, composes the short wind example, where wind sets the tempo and the pad interval. Both are rendered through FluidSynth with the `mbgm.SF2` General MIDI soundfont.

`scripts/generate_audio.py` reads every recorded day between each bird's departure and return, using the actual dates, and lines them up with the first 26 seconds of each recording. During the 4-second ending the gull stays at the colony. Weather is only interpolated between neighbouring dated records; it is never stretched across a whole journey.

The EIC colour mapping is a relative scale based on the image palette. It does not recover measured degrees. The EIC frames are dated 2026, while the bird tracks are from 2024–25. The birds leave between 22 October and 11 November 2024, the same time of year as the 30 October to 14 November EIC frames.

The wind grid is `assets/data/nico_wind_grid.json`, fetched by `scripts/fetch_wind_grid.py`. It holds 21 hourly 100-metre wind grids for 30 October 2024 over the Bay of Biscay, from the Open-Meteo archive (ERA5 model). That was Nico's longest day (793 km), crossing the bay with a tailwind. Wind is sampled at positions interpolated between daily GPS records. Nico stays inside the grid for all 21 hours, 00:00 to 20:00 UTC. Eric and Sanne are outside it.

The wind example squeezes those 21 hours into eight seconds. Wind speed sets the tempo, and the wind relative to Nico's heading sets the pad interval, which forms the wind sound. Pitch and pulse rate stay at the values Nico's full journey has on that day. The full journeys have no wind, because one day's wind would otherwise be stretched across the whole trip, so their wind stems are silent. Outside the recorded hours the wind readout says "No data here".

A tracking gap is a break of more than 24 hours between GPS fixes. The solar-powered tags can sleep for up to 22 hours a day in late autumn while still recording every day, so a shorter threshold would wrongly mark recorded days as missing. None of the three journeys has a gap between departure and return.

The shared score is `assets/data/journey_score.json`. The stereo MP3 stems start together on the Web Audio clock. Switching sounds on and off uses short volume fades, and switching birds crossfades while keeping the current position. All birds share one volume level so they can be compared fairly. The MIDI for each bird is also saved in `assets/audio/*.mid`.
