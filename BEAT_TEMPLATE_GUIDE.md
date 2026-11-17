# Beat template guide

The app creates original, sample-free loops locally. Five defaults are generated on first backend startup:

| Beat | BPM | Default bars |
| --- | ---: | ---: |
| Neutral Creator Pulse | 100 | 8 |
| Energetic Pop Motion | 120 | 8 |
| Cinematic Drive | 96 | 8 |
| Hip-Hop Bounce | 90 | 8 |
| Electronic Momentum | 128 | 8 |

Each folder under `templates/assets/beats/<beat-id>/` contains a combined 48 kHz stereo PCM WAV, five stereo stem WAVs, and `metadata.json`. Metadata includes BPM, 4/4 time signature, bar count, mood/name, duration, asset path, stem paths, seed, complete generator settings, and ownership/licence statements.

## Generator settings

- `bpm`: 60–180.
- `bars`: 1–32; four beats per bar.
- `intensity`: transient and tonal energy from 0–1.
- `swing`: delay on off subdivisions from 0–0.45.
- `density`: percussion event probability and subdivision density from 0–1.
- `bass_level` and `percussion_level`: synthesis levels from 0–1.
- `seed`: integer controlling all random percussion choices and tonal variation.

The generator hashes normalized settings into filenames. Identical settings and seed produce identical samples and paths. It synthesizes kick pitch envelopes, noise percussion, simple bass oscillators, and a sparse atmospheric layer; it does not load recordings or reproduce an artist/song.

Generation runs as a background job. The dialog reports each synthesis/writing stage and **Stop generation** sets a cancellation checkpoint. Files are written in a temporary folder and promoted to the persistent library only after the master and all five stems succeed, so failure or cancellation does not leave a partially published beat.

## Beat analysis

**Analyze source audio** runs entirely inside the backend container:

1. FFmpeg extracts temporary mono PCM at 22.05 kHz.
2. librosa measures onset strength, estimates tempo, and tracks beat frames.
3. The strongest four-beat phase is labeled as the approximate downbeat grid.
4. Beat/downbeat timestamps and 512 normalized waveform peaks are cached.

Downbeats are heuristic, especially for speech-heavy or syncopated audio. Use manual BPM/offset correction or tap tempo when needed. Cached results are invalidated automatically when source size or modification time changes.

## Mixing and effects

The combined beat or its five stems can be looped under the source. Automatic ducking uses the source audio as a side-chain and does not imply speech transcription. Master processing optionally normalizes to -14 LUFS and always applies peak limiting.

Effects use the same BPM and offset in the timeline and FFmpeg. A downbeat trigger is every fourth beat; `every_n_beats` multiplies the beat period. Duration is milliseconds and should be shorter than the trigger interval. Reduced-flashing mode caps visual intensity and full-frame opacity.

The long-form preset includes deterministic `twinkling_stars` and `sparkle_burst` overlays. Their `seed` fixes star positions, while intensity controls star count/size and opacity controls visibility. `cinematic_bars` is included as an optional disabled effect. The editor’s Stars, Sparkle, and Bars shortcuts add safe starting configurations; all three are rendered by FFmpeg and represented in the live browser preview.

Use only owned/licensed uploaded music. Volume changes and ducking are mixing tools, not methods for avoiding copyright detection.
