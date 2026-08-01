<div align="center">

[![CI](https://github.com/icecold009/Audio-Recognition/actions/workflows/ci.yml/badge.svg)](https://github.com/icecold009/Audio-Recognition/actions/workflows/ci.yml)

<h1>Audio Recognition</h1>
<p>Identify songs from a microphone, WAV file, or supported browser upload.</p>

</div>

## Overview

The CLI and Flask API share one audio validation and normalization path. Inputs are decoded, downmixed to mono, resampled, duration-checked, and passed to the matcher. The matcher tries RapidAPI, AcoustID, AudD, and finally the optional local fingerprint index.

The FFT output is a diagnostic visualization only. It is not the song-identification algorithm. The local matcher uses spectrogram peaks and anchor/target constellation hash pairs, then votes on consistent time offsets.

## Supported audio contract

| Boundary | Contract |
|---|---|
| CLI file mode | WAV/PCM only by default; no FFmpeg conversion is performed. |
| Web upload | WAV, MP3, M4A, AAC, OGG, FLAC, and WEBM; non-WAV inputs are converted through FFmpeg. |
| Internal representation | Mono float32 samples in `[-1, 1]`, resampled to `INTERNAL_SAMPLE_RATE` (default 44,100 Hz), written as 16-bit PCM WAV for providers. |
| Duration | At least `MIN_AUDIO_SECONDS` (default 1 second) and at most `MAX_AUDIO_SECONDS` (default 30 seconds). |
| Upload size | At most `MAX_UPLOAD_BYTES` (default 10 MiB) at the web and file-loading boundary. |
| WAV widths | 8-bit, 16-bit, and 32-bit PCM. 24-bit PCM is rejected unless a future decoder explicitly supports it. |

Empty audio, malformed headers, invalid sample rates, unsupported widths, invalid sample values, duration violations, upload limits, FFmpeg timeouts, and FFmpeg conversion failures receive stable validation errors.

## Quickstart

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python main.py
```

Choose `mic` or `file` in the CLI. File mode expects a WAV path. The CLI writes `fft_output.png` as a diagnostic and then prints the normalized matcher result.

To run the Flask app:

```powershell
python web/app.py
# open http://127.0.0.1:5000
```

The web UI uses `/api/match` and `/api/status` from the same application. Install FFmpeg and put it on `PATH` before uploading non-WAV formats.

## Configuration

Copy `.env.example` to `.env` and set only the credentials you intend to use:

| Variable | Default | Purpose |
|---|---:|---|
| `RAPIDAPI_KEY` | empty | RapidAPI Shazam backend, first in fallback order |
| `ACOUSTID_API_KEY` | empty | AcoustID backend |
| `AUDD_API_TOKEN` | empty | AudD backend |
| `FP_CALC_PATH` | empty | Optional Chromaprint `fpcalc` executable |
| `FINGERPRINT_INDEX_PATH` | empty | Optional local constellation-hash index |
| `INTERNAL_SAMPLE_RATE` | `44100` | Normalized sample rate |
| `INTERNAL_SAMPLE_WIDTH` | `2` | Normalized PCM width in bytes |
| `MIN_AUDIO_SECONDS` | `1` | Minimum useful clip duration |
| `MAX_AUDIO_SECONDS` | `30` | Maximum clip duration |
| `MAX_UPLOAD_BYTES` | `10485760` | Maximum input file size |
| `FFMPEG_TIMEOUT_SECONDS` | `15` | Web conversion timeout |
| `INTERNAL_API_SECRET` | empty | Optional `X-API-Secret` protection for `/api/match` |

Provider order is always RapidAPI → AcoustID → AudD → local fingerprint index. Missing configuration, no-match responses, provider errors, timeouts, and rate limits continue to the next backend.

## API contract

`POST /api/match` accepts a multipart field named `file`. `GET /api/status` reports non-secret capability and limit information.

Every recognition response uses one of these statuses:

`matched` · `no_match` · `not_configured` · `invalid_audio` · `rate_limited` · `error`

Matched responses expose normalized song fields such as `title`, `artist`, `album`, and optional `image`. Diagnostics contain backend names, stable error codes, and safe messages only. Raw provider payloads, credentials, local paths, and stack traces are never returned.

Example:

```bash
curl -X POST http://127.0.0.1:5000/api/match -F "file=@song.wav"
```

```json
{
  "status": "matched",
  "backend": "rapidapi",
  "title": "Example Song",
  "artist": "Example Artist",
  "album": "Example Album",
  "attempts": [
    {"backend": "rapidapi", "status": "matched"}
  ]
}
```

## Testing

```powershell
python -m pytest -q
python -m coverage run --branch --source=shazam_project,web,scripts -m pytest -q
python -m coverage report --fail-under=50 --show-missing
```

The suite covers 8-bit, 16-bit, and 32-bit PCM, stereo downmixing, malformed and empty WAVs, invalid rates, 24-bit rejection, duration and size limits, FFmpeg failures/timeouts, temporary-file cleanup, matcher fallback, safe diagnostics, local matching, and Flask API behavior.

## Limitations

Recognition quality depends on provider availability, credentials, network conditions, and recording quality. The local index must be built from clean source tracks and is not a general-purpose music database. The repository does not claim a real-world benchmark score because a verified source-track evaluation set is not committed.

The committed FFT screenshot is illustrative diagnostics, not evidence of identification accuracy.

## Project structure

- `main.py` — interactive CLI
- `web/app.py` — Flask browser/API entrypoint
- `shazam_project/recorder.py` — shared decode, validation, normalization, FFmpeg, and WAV utilities
- `shazam_project/matcher.py` — provider fallback and public response contract
- `shazam_project/fingerprint.py` — spectrogram peak and constellation hash index
- `shazam_project/fft_analyze.py` — diagnostic visualization only
- `tests/` — unit and Flask integration tests

## License

[MIT](LICENSE)
