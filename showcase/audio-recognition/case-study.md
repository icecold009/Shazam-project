# DIY Shazam — case study

## Project overview

DIY Shazam is an audio-recognition project with a command-line entry point and a Flask browser application. It accepts microphone input and audio files, normalizes them through a shared pipeline, and attempts recognition through configured external providers or a local constellation-hash index.

**Current status:** Source-verified on audit branch `codex/audio-recognition-p0-audit` at `6eb46cf`, with 193 local pytest tests, Ruff, and `git diff --check` passing. PR #11 is pushed, open, and draft; no current-branch CI status entries were reported by the connected GitHub check. No live demo URL, complete real-world benchmark, or credentialed provider result is currently available.

## Problem and audience

The project addresses a familiar user need—identify an unknown song—but treats it as an engineering problem rather than a single API call. Audio can arrive as a microphone recording or several file formats, and every route introduces failure modes: malformed data, unsupported formats, missing dependencies, provider timeouts, no matches, quota limits, and missing configuration.

The repository does not define a formal customer persona. For portfolio purposes, I frame the audience as developers or learners who want a transparent, self-hosted recognition workflow and a small educational implementation of audio fingerprinting. That audience framing is an interpretation, not a measured user claim.

## Goals and constraints

The implementation goals were to:

- provide one supported browser path through Flask;
- share audio normalization across CLI, browser, providers, and local matching;
- keep public response fields stable and safe;
- fall through between configured backends when a provider errors or returns no match;
- bound upload size, duration, FFmpeg work, and request quotas;
- make benchmark output reproducible without exposing credentials or committing audio.

The important constraints are external credentials, FFmpeg and `fpcalc` availability, microphone hardware, provider quotas, and legal source audio. The supported `.venv-pipeline` interpreter could not be launched without scoped elevation in this sandbox because its target Python process was inaccessible; the requested pytest command then completed successfully under that scoped launch.

## Solution and key user flow

The supported browser flow is:

1. The user opens the Flask-served page.
2. The user uploads a supported format or records through the browser microphone.
3. The server checks request size and rate limits before expensive upload processing.
4. Non-WAV web input is converted through bounded FFmpeg.
5. The shared loader validates the audio, downmixes it to mono float32, resamples it, and enforces duration limits.
6. The matcher tries RapidAPI/Shazam, AcoustID, AudD, and the local index according to configuration.
7. The UI renders a safe result, retry path, no-match state, rate-limit response, or configuration/error message.

The public response vocabulary is explicit: `matched`, `no_match`, `not_configured`, `invalid_audio`, `rate_limited`, and `error`. Provider payloads, credentials, local paths, and stack traces are not part of the browser response.

## Design and technical decisions

### One audio contract

`shazam_project/recorder.py` defines the shared `AudioClip` and normalization behavior. Input is validated, downmixed, resampled to the configured internal rate—44.1 kHz by default—and bounded by duration and upload-size limits. Provider temporary files use fixed 16-bit PCM WAV. This prevents each backend from silently interpreting a different input representation.

### Fallback instead of single-provider fragility

`shazam_project/matcher.py` records backend attempts and distinguishes no match, missing configuration, timeout, HTTP failure, malformed response, and fingerprint errors. Eligible failures fall through to the next configured backend. The public response is filtered to a safe schema while detailed diagnostics stay internal.

### Local fingerprinting as an educational contribution

The local backend in `shazam_project/fingerprint.py` extracts spectral peaks, creates paired hashes, and matches consistent time offsets against a JSON index. The repository explicitly labels the FFT plot as diagnostic only. The local method is a technical learning path, not a claim of production-scale Shazam equivalence.

### Bounded production behavior

`web/app.py`, `Dockerfile`, `render.yaml`, and the Supabase migration define a harder production boundary: `/healthz` is liveness, `/readyz` checks readiness prerequisites, `/api/status` exposes non-secret capability flags, quotas can fail closed, and the container runs as a non-root user with read-only filesystem expectations and a temporary filesystem.

### Reproducible evaluation before published numbers

The benchmark runner requires 30 source tracks, one 4-, 8-, and 15-second microphone clip per source, at least three recording conditions, stable identifiers where available, operator metadata, and all selected backends. It caches safe normalized results and refuses incomplete output through the README updater. This is intentionally conservative: no accuracy or latency number is published until the data exists.

## Implementation highlights

- Flask `web/` is the single supported browser application.
- The browser supports upload, recording, waveform visualization, light/dark theme, retry states, and optional session-only history.
- Web uploads support WAV, MP3, M4A, AAC, OGG, FLAC, and WEBM; FFmpeg conversion is bounded by duration and output-size limits.
- The current audit branch (`6eb46cf`) includes the `b0b8d79` audio-pipeline/browser-recovery hardening baseline plus the documentation and evaluation-gate reconciliation.
- CI runs Python tests on 3.10, 3.11, and 3.12, Ruff formatting/lint, branch coverage, dependency auditing, secret scanning, Render Blueprint validation, and a production-container smoke path.

## Validation and results

### Verified in repository source

- The supported routes are `/`, `/healthz`, `/readyz`, `/api/match`, and `/api/status`.
- The response status vocabulary and audio contract are documented and covered by Python tests.
- The repository contains a reproducible benchmark command and refuses incomplete README imports.
- The checked-in FFT image is a real project artifact, but it is a diagnostic spectrum and not evidence of recognition quality.

### Verified locally on the current audit branch

- `.venv-pipeline\Scripts\python.exe -m pytest -q` completed with `193 passed in 9.56s`; the initial non-elevated launcher failed before test collection because it could not create the configured Python process.
- `.venv-pipeline\Scripts\ruff.exe check .` passed, and `git diff --check` produced no output.
- A local development HTTP smoke returned 200 for `/` and `/healthz`, exposed upload and record controls, returned non-secret `/api/status`, returned the stable `invalid_audio` response for a malformed upload, and returned `/readyz` 503 because no recognition backend is configured.
- `sounddevice.query_devices()` listed 33 host devices, including input and output devices. No microphone recording or provider call was made.

### Verified remotely on merged `main`

The merged [PR #8](https://github.com/icecold009/Audio-Recognition/pull/8) records GitHub Actions run `30738959312`: 190 tests passed in each Python 3.10, 3.11, and 3.12 matrix job; branch coverage reached 81% against a 70% threshold; Ruff, `pip-audit`, Gitleaks, Render schema validation, production image build, and external container smoke passed. The smoke path exercised `/`, `/healthz`, the expected production `/readyz` and `/api/status` failures without configuration, malformed upload rejection, and a mocked successful recognition response.

### Not yet verified

- The current branch has not received a remote CI run; local checks are not equivalent to the full hosted matrix.
- No complete real-world corpus has been recorded.
- No credentialed provider comparison, recognition accuracy, p95 latency, catalog coverage, or live deployment smoke is claimed.
- The in-app browser could not reach the elevated local listener, so no browser-engine/permission screenshot or real browser microphone capture is claimed. Source and mocked tests cover the recording fallback and cleanup paths.

## Challenges and tradeoffs

The project favors explicit boundaries over optimistic demos. That means the README intentionally shows no benchmark number until the corpus and metadata are complete. The same choice makes the setup heavier: legal source provenance, microphone recording, external provider quotas, FFmpeg, `fpcalc`, and operator metadata all become prerequisites.

The local matcher is valuable because it is inspectable and can run without a provider call, but it is not a substitute for a measured production catalog. The provider fallback improves resilience, but it also makes performance and correctness backend-dependent. Session history is convenient, but it remains browser-session state rather than a persistent account feature.

## What I learned

The main lesson is that “it returned a song once” is not a useful quality claim. A trustworthy showcase needs traceable input data, exact denominators, failure categories, latency methodology, and clear separation between source behavior, CI evidence, live verification, and planned work. I also learned that the audio contract should be centralized early; otherwise each provider becomes a separate interpretation of the same recording.

## Future improvements

1. Assemble and document the legal 30-source corpus and 90 clips per backend.
2. Run the benchmark with reviewed credentials and publish generated results only after the completeness gate passes.
3. Add browser-engine smoke coverage for recording, upload, permission denial, unsupported `MediaRecorder`, result states, and resource cleanup.
4. Capture a clean screenshot set from the supported Flask app and attach commands/captions to each image.
5. Make the supported pipeline interpreter launch without elevation in the developer environment and run the full release gate on the current branch.
6. Verify a deployed instance separately if a live URL becomes available.

## Technologies used

Python, Flask, NumPy, SciPy, SoundFile, SoundDevice, Requests, Matplotlib, FFmpeg, Chromaprint/fpcalc, Gunicorn, Supabase/Postgres quota RPCs, Docker, Render Blueprint, pytest, coverage.py, Ruff, pip-audit, and Gitleaks.

## Evidence sources

- [`README.md`](../../README.md)
- [`TODO.md`](../../TODO.md)
- [`evaluation/README.md`](../../evaluation/README.md)
- [`web/app.py`](../../web/app.py)
- [`web/templates/index.html`](../../web/templates/index.html)
- [`web/static/app.js`](../../web/static/app.js)
- [`shazam_project/recorder.py`](../../shazam_project/recorder.py)
- [`shazam_project/matcher.py`](../../shazam_project/matcher.py)
- [`shazam_project/fingerprint.py`](../../shazam_project/fingerprint.py)
- [`Dockerfile`](../../Dockerfile)
- [`render.yaml`](../../render.yaml)
