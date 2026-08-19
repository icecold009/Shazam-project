# Reproducible real-world recognition benchmark

This evaluation is deliberately gated before any recording or provider call. Source audio and microphone clips remain local and ignored by Git. Only manifests, scripts, aggregate JSON, and Markdown reports may be committed, and generated results are also ignored until an operator deliberately reviews them.

The target corpus is 30 legally reusable or user-owned source tracks, with one 4-second, one 8-second, and one 15-second speaker-to-microphone clip per track. Record at least three conditions and document genre, era, title, artist, and provenance/license for every source row.

## 1. Prepare and validate the source catalog

Copy the example and edit it with real local paths. Do not add the source files or the completed `sources.csv` to Git.

PowerShell:

```powershell
Copy-Item evaluation/sources.example.csv evaluation/sources.csv
.\.venv\Scripts\python.exe -c "from scripts.evaluation import load_source_manifest; print('Validated', len(load_source_manifest('evaluation/sources.csv')), 'source rows')"
```

Bash:

```bash
cp evaluation/sources.example.csv evaluation/sources.csv
.venv/bin/python -c 'from scripts.evaluation import load_source_manifest; print("Validated", len(load_source_manifest("evaluation/sources.csv")), "source rows")'
```

Each row must have a stable `source_id`, existing source audio path, title, artist, genre, era, `recording_condition`, and `provenance_or_license_note`. Optional `rapidapi_id`, `acoustid_id`, `audd_id`, and `local_id` values are used when stable provider identifiers are available. Normalized title/artist matching is the documented fallback when no expected provider identifier is supplied.

The validator must pass before recording. It does not copy, upload, or call a provider.

## 2. Record the corpus with resume protection

Inspect devices first:

PowerShell:

```powershell
.\.venv\Scripts\python.exe -c "import sounddevice as sd; print(sd.query_devices())"
```

Bash:

```bash
.venv/bin/python -c 'import sounddevice as sd; print(sd.query_devices())'
```

Record only after confirming the source license, physical playback setup, input device, and output device. The recorder writes only missing or unreadable clips and never replaces a readable verified clip. If the process is interrupted, rerun the same command; it skips verified files and updates the ignored manifest after each source.

PowerShell:

```powershell
.\.venv\Scripts\python.exe scripts/record_benchmark.py `
  --sources evaluation/sources.csv `
  --input-device 1 `
  --output-device 4 `
  --manifest evaluation/manifest.csv `
  --output-dir evaluation/clips
```

Bash:

```bash
.venv/bin/python scripts/record_benchmark.py \
  --sources evaluation/sources.csv \
  --input-device 1 \
  --output-device 4 \
  --manifest evaluation/manifest.csv \
  --output-dir evaluation/clips
```

Use `--yes` only after checking the room and devices. Never commit `evaluation/clips/`, `evaluation/manifest.csv`, source audio, or recordings.

## 3. Build the local index without provider calls

PowerShell:

```powershell
.\.venv\Scripts\python.exe scripts/build_fingerprint_index.py `
  --sources evaluation/sources.csv `
  --output evaluation/results/fingerprint-index.json
```

Bash:

```bash
.venv/bin/python scripts/build_fingerprint_index.py \
  --sources evaluation/sources.csv \
  --output evaluation/results/fingerprint-index.json
```

Set `LOCAL_FINGERPRINT_INDEX` to the ignored local index only if testing the application dispatcher. The index builder itself makes no provider calls.

## 4. Run the benchmark with deterministic caching

The runner never prints or stores credentials or authorization headers. Cache keys include the backend name, explicit backend version, clip SHA-256, and relevant non-secret settings. Use the same cache for a warm rerun; use `--refresh-cache` only when intentionally repeating provider calls.

Run one backend first without credentials to verify the incomplete-configuration path:

```powershell
.\.venv\Scripts\python.exe scripts/benchmark.py `
  --manifest evaluation/manifest.csv `
  --backend local `
  --network-region "operator-region" `
  --provider-plan "local-only" `
  --output evaluation/results/local.json `
  --report evaluation/results/local.md
```

Run all four backends only after credentials and quotas are intentionally configured:

```powershell
.\.venv\Scripts\python.exe scripts/benchmark.py `
  --manifest evaluation/manifest.csv `
  --backend all `
  --timeout 15 `
  --network-region "operator-region" `
  --provider-plan "provider-plan-label" `
  --output evaluation/results/benchmark.json `
  --report evaluation/results/benchmark.md
```

Bash uses the same arguments with `.venv/bin/python` and backslashes for line continuation. Provider calls are paid/external operations; do not run this command until the corpus, credentials, plan, and quota budget have been reviewed.

The JSON and Markdown outputs include top-1 numerator/denominator and accuracy, breakdowns by clip length and recording condition, no-match, false-positive, provider-error, timeout, and unusable-input rates, mean/median/p95 latency, stable identifier method, UTC date, OS, Python version, hardware summary, operator network region, provider plan, timeout, and `cold`, `warm`, `mixed`, or `empty` cache state. Accuracy denominators are attempted clips; missing clips are reported as missing inputs and excluded, while `not_configured` is reported separately and never treated as a zero score. Cache-hit latency measures the local cache read, not provider recognition time; a mixed report can therefore contain both cache-read and provider-call latency.

## 5. Import only complete results into the README

The updater refuses incomplete credentials, missing backends, missing clips, missing operator metadata, or missing result sections. It reads only generated values and never fabricates a percentage.

PowerShell:

```powershell
.\.venv\Scripts\python.exe scripts/update_readme.py `
  --results evaluation/results/benchmark.json `
  --readme README.md
```

Bash:

```bash
.venv/bin/python scripts/update_readme.py \
  --results evaluation/results/benchmark.json \
  --readme README.md
```

Review the diff, JSON, and Markdown report before committing. The command must not be used for partial, synthetic, credential-incomplete, or provider-free runs.
