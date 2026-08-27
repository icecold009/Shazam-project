<div align="center">

[![CI](https://github.com/icecold009/audio-recognition/actions/workflows/ci.yml/badge.svg)](https://github.com/icecold009/audio-recognition/actions/workflows/ci.yml)
<br/>

**Identify any song from your microphone or an audio file.**  
Validated audio pipeline · Multi-backend matching · Flask web UI · Terminal output


</div>

***
<div align="center">
  <h1 style="margin:0;padding:0">Audio Recognition</h1>
  <p style="margin:4px 0 8px;color:#1E90FF">Identify songs from your microphone or an audio file with validated multi-backend matching</p>
</div>

## Overview
DIY Shazam captures audio from the CLI microphone/file path or the Flask browser UI, normalizes it through one bounded audio pipeline, and identifies tracks using RapidAPI/Shazam, AcoustID, AudD, or local spectrogram peaks and constellation hash pairs. FFT output is a diagnostic visualization only; it is not the recognition algorithm. Flask serves the complete browser UI and JSON API from one origin.

## Implemented

- Flask is the supported browser application; `web/app.py` serves the UI and JSON API from one origin.
- CLI and web inputs use the documented bounded normalization pipeline. The CLI accepts WAV/PCM; the web path converts the documented browser upload formats through FFmpeg.
- Provider dispatch uses RapidAPI/Shazam, AcoustID, AudD, and the local constellation-hash backend with stable public statuses and safe diagnostics.
- Production configuration includes Gunicorn, `/healthz`, `/readyz`, bounded uploads and FFmpeg work, atomic Supabase quota operations, trusted-proxy controls, and debug-off defaults outside explicit development mode.
- The current checkout has 193 passing Python tests. CI also defines Ruff, coverage, dependency-audit, and secret-scanning gates.

## Known limitations

The repository does not currently claim general recognition accuracy or latency. The real-world benchmark corpus, 90 microphone clips, provider comparison, credentialed smoke test, and browser-engine state matrix are still incomplete. Provider catalog coverage, noise, re-encoding, volume or pitch changes, partial clips, live performances, covers, remixes, and alternate releases can all change the result.

Supabase authentication, persistent user history, RLS-backed history, account deletion, user settings, and protected user routes are not implemented product features. History is session-only and optional in the browser UI.

## Planned

- Assemble and run the lawful 30-track / 90-clip benchmark, then import only generated results.
- Add browser/component coverage for recording, permission denial, unsupported `MediaRecorder`, cleanup, and all public result states.
- Complete browser-engine screenshots/recordings and credentialed provider smoke evidence.
- Evaluate the local constellation-hash contribution against provider baselines before changing the distinctiveness score.

## Evaluation evidence

- Reproducible benchmark entry points are documented in [`evaluation/README.md`](evaluation/README.md), but no complete result is present in this checkout.
- The committed FFT image at [`docs/screenshots/fft-output.png`](docs/screenshots/fft-output.png) is diagnostic output from `shazam_project.fft_analyze.analyze_audio`; the original capture command was not preserved in Git. Recreate it through the CLI's `python main.py` path after choosing `mic` or `file`.
- The current browser smoke check was run against `python web/app.py` on a local development port with debug disabled. It verified page load, status rendering, and the unsupported-upload error state; it did not use provider credentials or record microphone audio.

## Performance

| Metric | Result |
|--------|--------|
| Average recognition time (RapidAPI backend) | See the imported benchmark report below |
| Average recognition time (AudD backend) | See the imported benchmark report below |
| Test set accuracy | See the imported benchmark report below |
| Minimum audio duration | 1 s by default (configurable) |
| Maximum audio duration | 30 s by default (configurable) |
| Maximum upload size | 10 MiB by default (configurable) |
| Platforms tested | Not established by this branch's validation |

<!-- BENCHMARK_RESULTS:START -->
No complete real-world benchmark has been imported. Run the documented evaluation only after assembling a legally reusable corpus and supplying the operator metadata and provider configuration.
<!-- BENCHMARK_RESULTS:END -->

## Architecture
```mermaid
flowchart LR
  A[CLI / Flask Browser UI] --> B[Audio Input\n(mic or upload)]
  B --> C[Validate and normalize\nmono float32 / internal rate]
  C --> D[FFT diagnostic only]
  C --> E[Write 16-bit PCM WAV temp]
  E --> F{Matcher Backends}
  F -->|RapidAPI| G[Shazam]
  F -->|AcoustID| H[AcoustID]
  F -->|AudD| I[AudD]
  F -->|Local hashes| J[Peak/hash index]
  G & H & I & J --> K[Normalized Result]
  K --> L[Display (CLI) / JSON (Web)]
  classDef blue fill:#ffffff,stroke:#1E90FF,stroke-width:2px,color:#1E90FF;
  class A,B,C,D,E,F,G,H,I,J,K,L blue;
```
Theme: black / white / blue — white nodes with a professional DodgerBlue accent (#1E90FF). The browser UI is served directly by Flask; there is no separate browser bundle.

## Quickstart
### Windows PowerShell

1) Create and activate a venv:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
2) Install runtime dependencies and copy configuration:
```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```
3) Add provider values to `.env` if recognition is needed. Verify host tools when using
non-WAV uploads or AcoustID:
```powershell
ffmpeg -version
fpcalc -version
```
4) Run the Flask development server:
```powershell
python web/app.py
# open http://127.0.0.1:5000
```
5) Run CLI when terminal recognition is needed:
```powershell
python main.py
```

### macOS/Linux

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python web/app.py
```

Install host tools outside Docker when needed: macOS uses `brew install ffmpeg chromaprint`;
Debian/Ubuntu uses `sudo apt-get install ffmpeg libchromaprint-tools`. These commands are
only for local host setup; the production image installs the same runtime tools itself.

Run the CLI with `python main.py`. On macOS/Linux, a production-style local WSGI process is:

```bash
APP_ENV=production PORT=8000 gunicorn --config gunicorn.conf.py web.app:app
```

Production mode requires the server-only Supabase quota configuration and at least one
recognition backend. It fails closed with HTTP 503 when those requirements are unavailable.

### Docker

The reproducible production image installs Python, Gunicorn, FFmpeg, Chromaprint/fpcalc,
audio libraries, and curl for health checks. It runs as UID/GID `10001`, uses Gunicorn, and
reads the platform-provided `PORT`.

```powershell
Copy-Item .env.example .env
docker compose up --build
```

The compose service uses Gunicorn with a read-only root filesystem and a bounded 64 MiB
`/tmp` tmpfs. Its default `APP_ENV=development` keeps the local quickstart usable without
Supabase; configure `.env` and set `APP_ENV=production` when testing the fail-closed
production path. A direct production start is:

```powershell
docker build -t audio-recognition .
docker run --rm -p 5000:5000 --env-file .env audio-recognition
```

The same commands work from macOS/Linux after replacing `Copy-Item` with `cp`.

The canonical Flask development command remains:

```powershell
python web/app.py
# open http://127.0.0.1:5000
```

## Project Structure
Core source modules now live under `shazam_project/`:

- `shazam_project/config.py`
- `shazam_project/recorder.py`
- `shazam_project/fft_analyze.py`
- `shazam_project/matcher.py`
- `shazam_project/display.py`

Entrypoints remain:

- `main.py` (CLI)
- `web/app.py` (canonical Flask browser app and API)
- `web/templates/index.html` and `web/static/` (same-origin browser assets)

## Configuration
Supported env vars (see `shazam_project.config.load_config()`): `AUDD_API_TOKEN`, `ACOUSTID_API_KEY`, `FP_CALC_PATH`, `RAPIDAPI_KEY`, and optional `LOCAL_FINGERPRINT_INDEX` (with `FINGERPRINT_INDEX_PATH` accepted as a legacy alias). The shared audio contract is controlled by `INTERNAL_SAMPLE_RATE`, `MIN_AUDIO_SECONDS`, `MAX_AUDIO_SECONDS`, `MAX_UPLOAD_BYTES`, and `FFMPEG_TIMEOUT_SECONDS`; provider WAVs are always fixed 16-bit PCM. Matcher order is RapidAPI → AcoustID → AudD → local fingerprint index.

## Web UI
`python web/app.py` serves `/`, `/static/*`, `/api/match`, and `/api/status` from the same origin. CLI file mode accepts WAV/PCM files. Web uploads support WAV, MP3, M4A, AAC, OGG, FLAC, and WEBM; non-WAV web uploads require FFmpeg on `PATH` and are converted before decoding. The browser also supports microphone recording, manual stop, waveform visualization, loading/error/no-match states, light/dark theme persistence, and session-only recognition history.

The durations are intentionally different by path: CLI microphone mode defaults to 8 seconds and accepts an interactive override; the RapidAPI/Shazam adapter sends at most the first 5 seconds of the normalized clip; browser recording auto-stops after 10 seconds but can be stopped manually; and the reproducible benchmark uses separate 4-second, 8-second, and 15-second microphone clips.

Every input is downmixed to mono float32 samples in `[-1, 1]` and resampled to 44,100 Hz by default. Provider adapters receive temporary mono 16-bit PCM WAV files. Inputs shorter than 1 second, longer than 30 seconds, or larger than 10 MiB are rejected by default; all three limits are configurable.

The deployment endpoints are:

- `/healthz` is a dependency-free process liveness check and returns HTTP 200 when Flask is serving.
- `/readyz` checks production configuration, writable temporary storage, FFmpeg, fpcalc when
  AcoustID is enabled, Supabase quota availability, and at least one recognition backend. It
  returns HTTP 503 with stable check names when not ready; it never returns secrets, paths,
  exception text, or database details.
- `/api/status` reports non-secret backend/tool flags, quota mode, limits, and audio settings.

Gunicorn defaults to two workers, a 45-second request timeout, a 15-second graceful shutdown
window, and a five-second keep-alive. Provider requests and FFmpeg conversion remain bounded
by their existing 15-second timeouts. `MAX_UPLOAD_BYTES`, duration limits, Flask's request
limit, and temporary-file cleanup bound upload and conversion resource use.

## Testing
Run the Python tests and coverage locally:
```powershell
python -m pytest -q
python -m coverage run --branch --source=shazam_project,web,scripts -m pytest -q
python -m coverage report --fail-under=70 --show-missing
ruff format --check .
ruff check .
pip-audit -r requirements.txt -r requirements-dev.txt --progress-spinner off
```
`tests/` covers configuration loading and backend combinations, mocked microphone failures and cleanup, normalized audio, mocked provider flows and fallback, Flask routes, safe incomplete metadata rendering contracts, rate limits, FFmpeg failures, Supabase failures, and a generated WAV end-to-end test. The generated WAV is created in memory and contains no third-party recording.

GitHub Actions runs pytest on every push and pull request across Python 3.10, 3.11, and 3.12, Ruff formatting and lint, a Python 3.12 branch-coverage gate at 70%, `pip-audit`, and Gitleaks secret scanning. The coverage job publishes `coverage.xml` as an artifact. Provider network calls, Supabase credentials, `fpcalc`, FFmpeg, and microphone hardware are mocked or tested through stable failure paths in CI; they are excluded from the release gate because they require external credentials or host devices.

Direct Python dependencies use reviewed major-compatible ranges in `requirements.txt` and `requirements-dev.txt`. To update one, review its release notes and Python 3.10–3.12 compatibility, edit its range, install from both requirement files, then run the full pytest, coverage, Ruff, compile, diff, and `pip-audit` checks. Do not add credentials or resolve updates from a developer's private environment.

For the real-world comparison, see [`evaluation/README.md`](evaluation/README.md). It validates a source catalog, records resumable speaker-to-microphone clips at 4, 8, and 15 seconds, caches deterministic backend results without credentials, builds the local landmark-hash index from clean source tracks, and compares the local backend against all three provider backends.

## Limitations

Recognition is not guaranteed outside the happy path. The main failure modes are:

- **Background noise and recording quality:** speech, room echo, speaker distortion, very low volume, clipping, or music mixed with other sounds can hide the spectral peaks used by fingerprinting.
- **Catalog coverage:** a provider can only return tracks in its database, while the local matcher can only identify tracks present in its local fingerprint index. A `no_match` result does not prove that the audio is invalid.
- **Language and regional catalog differences:** the fingerprinting itself is not English-specific, but provider metadata and catalog coverage vary by language, region, release, and recording availability.
- **Live, cover, remix, and alternate versions:** crowd noise, changed instrumentation, tempo or pitch, medleys, and different arrangements may fail to match or may be returned as the closest studio recording rather than the exact performance.

The evaluation dataset is designed to measure these cases separately. Until that dataset is recorded and run through all configured backends, the README does not claim a general accuracy percentage.

## Production rate limits

Production quota enforcement uses the exposed-but-restricted `public.check_api_quota` preflight and `public.consume_api_quota` RPCs created by [`supabase/migrations/20260801145213_production_rate_limits.sql`](supabase/migrations/20260801145213_production_rate_limits.sql). The preflight is read-only and runs before upload saving; the final operation locks one HMAC-keyed usage row and checks cooldown, daily, and monthly limits before incrementing both counters atomically after valid audio decoding. Both functions are `SECURITY INVOKER`, use an explicit safe search path, and are executable only by `service_role`. The `public.api_usage` table has RLS enabled, no public policies, and no grants to `anon` or `authenticated`; the private schema is not exposed.

Required server-only configuration:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY` — never put this value in JavaScript, HTML, API responses, logs, or screenshots.
- `CLIENT_ID_HMAC_SECRET` — a separate secret used to derive the stored client identifier; raw IP addresses are never stored.

The local development quickstart uses `APP_ENV=development`, so local Flask matching works without Supabase and uses a bounded, expiring in-memory limiter. To exercise production behavior, set `APP_ENV=production` and provide all three server-only values above; missing configuration or a quota-service failure returns HTTP 503 rather than assuming zero usage. `APP_ENV=production` should be configured separately in the deployment environment, never copied blindly into a local `.env`.

The migration workflow is:

```powershell
supabase start
supabase db reset
supabase db advisors --local --type all --fail-on warn
supabase migration list --local
```

For a disposable linked development project, verify with `supabase link --project-ref <project-ref>`, `supabase db push --dry-run`, `supabase db push`, `supabase db advisors --linked --type all --fail-on warn`, and `supabase migration list --linked`. Never run `supabase db reset --linked` against production. The migration was created with `supabase migration new production_rate_limits`.

`/api/status` reports the quota mode, configured daily/monthly limits, cooldown, and whether production-grade quotas are enabled; it never reports client hashes or usage rows.

`INTERNAL_API_SECRET`, when configured, prevents the current browser UI from calling `/api/match` unless a deliberate server-side authentication design supplies `X-API-Secret`; the secret is never placed in JavaScript. An allowed `Origin` or `Referer` can never authenticate a request. Forwarded client addresses are ignored unless both `TRUSTED_PROXY_COUNT` and an allowlisted `TRUSTED_PROXY_IPS` chain are configured; every trusted proxy hop is validated when more than one hop is configured. Flask debug mode is enabled only when `APP_ENV=development`.

## Notes & Tips
- Record in a quiet space and keep the mic near the audio source.
- For AcoustID, install Chromaprint (`fpcalc`): macOS `brew install chromaprint`, Debian/Ubuntu `apt install libchromaprint-tools`.
- FFmpeg is required for non-WAV web uploads and is not required for WAV uploads.

## Contributing
Pull requests are welcome. For major changes, open an issue first.
Run `python -m pytest -q` and `python -m coverage report` before submitting.

***

## Example Output

```
Listen via microphone or load a file? (mic/file): mic

Recording for 8 seconds...
Recognition uses the normalized recording and configured matcher backends.

Song:    Blinding Lights
Artist:  The Weeknd

[Album art opens in image viewer]
```

FFT spectrum for a sample clip:

![FFT spectrum output](docs/screenshots/fft-output.png)

This image is diagnostic output from `shazam_project.fft_analyze.analyze_audio`; the canonical browser page is served by `python web/app.py` at `/`.

***

## Web API Reference

| Endpoint       | Method | Description                                           |
|----------------|--------|-------------------------------------------------------|
| `/api/match`   | POST   | Upload an audio file for recognition. Returns JSON.  |
| `/api/status`  | GET    | Reports configured backends, ffmpeg, fpcalc status.  |
| `/healthz`     | GET    | Dependency-free process liveness check.             |
| `/readyz`      | GET    | Configuration and dependency readiness check.      |

**Example — cURL:**
```bash
curl -X POST http://localhost:5000/api/match \
  -F "file=@song.wav"
```

**Example — Response:**
```json
{
  "status": "matched",
  "title": "Blinding Lights",
  "artist": "The Weeknd",
  "album": "After Hours",
  "image": "https://..."
}
```

Public `status` is one of: `matched` · `no_match` · `not_configured` · `invalid_audio` · `rate_limited` · `error`. Provider attempts contain only backend/status/error codes and generic safe messages; raw provider payloads, credentials, local paths, and stack traces are not public response data.

***

## Notes

- Record in a **quiet environment** for best accuracy
- CLI mic mode requires a working input device; the web UI uses browser microphone
- File mode (CLI) accepts WAV/PCM only; the web UI supports the documented formats and requires FFmpeg for non-WAV uploads.
- Windows, macOS, and Linux support has not been independently verified by this branch's evidence.

***

## Roadmap

- [x] Add CI — run pytest and coverage on every push and pull request
- [ ] CLI flags: `--mode`, `--duration`, `--file` for unattended/scripted use
- [ ] Local match history saved as JSON
- [ ] `--no-open-image` flag for headless environments
- [ ] Structured logging in `shazam_project/matcher.py` for easier debugging
- [x] Flask integration tests for the browser entry point, static assets, API status, and upload outcomes

***

## License

[MIT](LICENSE) — free to use and modify.
