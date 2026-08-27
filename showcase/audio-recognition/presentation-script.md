# DIY Shazam — spoken presentation script

**Target length:** Approximately 2 minutes

**Status:** Evidence-backed script. Claims about merged-main CI remain separate from the current audit PR branch, which has local checks but no current-branch CI status entries.

This is DIY Shazam, a song-recognition project that accepts either a microphone recording or an audio file and routes it through multiple matching backends. The project is designed as a practical end-to-end workflow: input handling, normalization, provider fallback, safe errors, and evaluation all matter as much as the happy-path match.

The problem is that audio is messy. A user may upload a different format, provide a clip that is too short or too long, lose microphone access, or hit a provider that is unavailable or has no result. The application therefore makes those states explicit instead of treating every failure as a zero or a server crash.

The main journey is simple. In the Flask browser UI, the user uploads WAV, MP3, M4A, AAC, OGG, FLAC, or WEBM, or records directly from the microphone. Web formats that need it go through bounded FFmpeg conversion. The shared pipeline downmixes to mono float32, resamples to the configured internal rate, enforces size and duration limits, and then passes the normalized clip to a matcher dispatcher.

The dispatcher tries configured backends in order: RapidAPI/Shazam, AcoustID, AudD, and an educational local fingerprint index. The local path extracts spectral peaks, pairs them into hashes, and looks for consistent time offsets. FFT output is only a diagnostic visualization; it is not presented as the identification algorithm. Public responses stay deliberately small and use stable statuses such as `matched`, `no_match`, `not_configured`, `invalid_audio`, `rate_limited`, and `error`.

The engineering evidence is stronger than the recognition-quality evidence. Merged main has remote CI evidence showing 190 tests on each Python 3.10, 3.11, and 3.12 job, 81% branch coverage against a 70% gate, plus Ruff, dependency and secret scans, Render schema validation, and container smoke. The current audit branch `codex/audio-recognition-p0-audit` at `6eb46cf` also passes 193 local tests, Ruff, and `git diff --check`, but has not yet received a separate remote CI run. There is also no complete real-world benchmark yet: the repository still needs a legally reusable 30-track corpus, 90 microphone clips per backend, three recording conditions, provider configuration, and operator metadata before accuracy or latency can be claimed.

The main lesson is to make the evidence boundary visible. The next milestone is not another feature; it is a reproducible benchmark, browser smoke coverage, screenshot evidence, and a fresh CI run on this branch. Today, the honest status is a technically hardened, CI-validated project on merged main, with live-demo and real-world recognition quality still unverified.
