from __future__ import annotations

import copy
import csv
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from scripts import benchmark, build_fingerprint_index, evaluation, record_benchmark
from scripts.evaluation import ManifestValidationError, load_clip_manifest, load_source_manifest
from scripts.update_readme import update_readme, validate_complete_results
from shazam_project.config import AppConfig


def _wav(path: Path, seconds: float = 1.5, sample_rate: int = 8000) -> Path:
    samples = np.zeros(int(seconds * sample_rate), dtype=np.float32)
    sf.write(path, samples, sample_rate, subtype="PCM_16")
    return path


def _sources_csv(path: Path, *, complete: bool = True, duplicate: bool = False) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    source = path / "source.wav"
    source.write_bytes(b"source")
    rows = [
        {
            "source_id": "track-001",
            "source_audio_path": "source.wav",
            "title": "Example Song",
            "artist": "Example Artist",
            "genre": "pop",
            "era": "2010s",
            "recording_condition": "speaker_mic_room",
            "provenance_or_license_note": "Operator-owned source",
        }
    ]
    if duplicate:
        rows.append(dict(rows[0]))
    csv_path = path / "sources.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    if not complete:
        text = csv_path.read_text(encoding="utf-8").replace(",Operator-owned source", ",")
        csv_path.write_text(text, encoding="utf-8")
    return csv_path


def _write_clip_manifest(path: Path, rows: list[dict[str, str]]) -> Path:
    manifest = path / "manifest.csv"
    with manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return manifest


def _clip_manifest(path: Path, clip: Path, *, provider_id: str = "") -> Path:
    row = {
        "source_id": "track-001",
        "clip_id": "track-001_4s",
        "audio_path": clip.name,
        "expected_title": "Example Song",
        "expected_artist": "Example Artist",
        "genre": "pop",
        "era": "2010s",
        "recording_condition": "speaker_mic_room",
        "clip_length_s": "4",
    }
    if provider_id:
        row["rapidapi_id"] = provider_id
    return _write_clip_manifest(path, [row])


def test_source_manifest_requires_all_provenance_fields_and_existing_audio(tmp_path):
    valid = _sources_csv(tmp_path)
    assert load_source_manifest(valid)[0]["recording_condition"] == "speaker_mic_room"

    incomplete = _sources_csv(tmp_path / "incomplete", complete=False)
    with pytest.raises(ManifestValidationError, match="provenance_or_license_note"):
        load_source_manifest(incomplete)


def test_source_index_loader_does_not_apply_query_duration_limit(monkeypatch, tmp_path):
    samples = np.zeros(31 * 8000, dtype=np.float32)
    monkeypatch.setattr(
        build_fingerprint_index,
        "_decode_source",
        lambda _path: (samples, 8000),
    )

    clip = build_fingerprint_index._load_source_clip(tmp_path / "source.mp3")

    assert clip.sample_rate == 44100
    assert clip.samples.size / clip.sample_rate == pytest.approx(31.0, abs=0.01)


def test_source_manifest_rejects_duplicate_unstable_ids(tmp_path):
    path = tmp_path / "duplicate"
    path.mkdir()
    manifest = _sources_csv(path, duplicate=True)
    with pytest.raises(ManifestValidationError, match="duplicates source_id"):
        load_source_manifest(manifest)


def test_recording_resume_preserves_verified_clip(monkeypatch, tmp_path):
    output_dir = tmp_path / "clips"
    output_dir.mkdir()
    existing = _wav(output_dir / "track-001_4s.wav", seconds=4)
    before = existing.read_bytes()
    source = {
        "source_id": "track-001",
        "source_audio_path": str(tmp_path / "source.wav"),
        "_resolved_source_path": str(tmp_path / "source.wav"),
        "title": "Song",
        "artist": "Artist",
        "genre": "pop",
        "era": "2010s",
        "recording_condition": "room",
        "provenance_or_license_note": "owned",
    }
    _wav(tmp_path / "source.wav", seconds=15)
    monkeypatch.setattr(
        record_benchmark,
        "_decode_source",
        lambda _path: (np.zeros(15 * 8000, dtype=np.float32), 8000),
    )
    monkeypatch.setattr(
        record_benchmark,
        "sd",
        SimpleNamespace(
            playrec=lambda *args, **kwargs: np.zeros(15 * 8000, dtype=np.float32),
            stop=lambda: None,
        ),
    )

    record_benchmark._record_source(
        source,
        output_dir,
        1,
        2,
        pending_lengths=[8],
        manifest_path=tmp_path / "manifest.csv",
    )

    assert existing.read_bytes() == before
    assert (output_dir / "track-001_8s.wav").is_file()


def test_recording_resume_rebuilds_verified_rows_from_current_source_metadata(
    monkeypatch, tmp_path
):
    output_dir = tmp_path / "clips"
    output_dir.mkdir()
    source_audio = _wav(tmp_path / "source.wav", seconds=15)
    clip_bytes = {}
    for length in record_benchmark.CLIP_LENGTHS:
        clip = _wav(output_dir / f"track-001_{length}s.wav", seconds=length)
        clip_bytes[length] = clip.read_bytes()

    sources = tmp_path / "sources.csv"
    source_row = {
        "source_id": "track-001",
        "source_audio_path": source_audio.name,
        "title": "Current Song",
        "artist": "Current Artist",
        "genre": "jazz",
        "era": "2020s",
        "recording_condition": "near",
        "provenance_or_license_note": "Current provenance",
        "local_id": "current-local-id",
    }
    with sources.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(source_row))
        writer.writeheader()
        writer.writerow(source_row)

    manifest = tmp_path / "manifest.csv"
    stale_rows = [
        {
            "source_id": "track-001",
            "clip_id": f"track-001_{length}s",
            "audio_path": f"clips/track-001_{length}s.wav",
            "expected_title": "Old Song",
            "expected_artist": "Old Artist",
            "genre": "rock",
            "era": "1990s",
            "recording_condition": "room",
            "provenance_or_license_note": "Old provenance",
            "clip_length_s": str(length),
            "local_id": "old-local-id",
        }
        for length in record_benchmark.CLIP_LENGTHS
    ]
    _write_clip_manifest(tmp_path, stale_rows)

    monkeypatch.setattr(record_benchmark, "sd", SimpleNamespace())
    monkeypatch.setattr(
        record_benchmark.sys,
        "argv",
        [
            "record_benchmark.py",
            "--sources",
            str(sources),
            "--output-dir",
            str(output_dir),
            "--manifest",
            str(manifest),
            "--input-device",
            "1",
            "--output-device",
            "2",
            "--yes",
        ],
    )

    assert record_benchmark.main() == 0

    rows = load_clip_manifest(manifest)
    assert len(rows) == len(record_benchmark.CLIP_LENGTHS)
    assert {row["expected_title"] for row in rows} == {"Current Song"}
    assert {row["expected_artist"] for row in rows} == {"Current Artist"}
    assert {row["genre"] for row in rows} == {"jazz"}
    assert {row["era"] for row in rows} == {"2020s"}
    assert {row["recording_condition"] for row in rows} == {"near"}
    assert {row["provenance_or_license_note"] for row in rows} == {"Current provenance"}
    assert {row["local_id"] for row in rows} == {"current-local-id"}
    for length in record_benchmark.CLIP_LENGTHS:
        assert (output_dir / f"track-001_{length}s.wav").read_bytes() == clip_bytes[length]


def test_relative_manifest_path_supports_nested_and_outside_output_layouts(tmp_path):
    manifest = tmp_path / "evaluation" / "manifest.csv"
    nested = tmp_path / "evaluation" / "clips" / "nested" / "track.wav"
    sibling = tmp_path / "recordings" / "track.wav"

    assert (
        evaluation.relative_manifest_path(manifest, tmp_path / "evaluation" / "clips" / "track.wav")
        == "clips/track.wav"
    )
    assert evaluation.relative_manifest_path(manifest, nested) == "clips/nested/track.wav"
    assert evaluation.relative_manifest_path(manifest, sibling) == "../recordings/track.wav"


def test_relative_manifest_path_rejects_unrepresentable_cross_drive_path(monkeypatch, tmp_path):
    def incompatible_drives(*_args):
        raise ValueError("path is on another drive")

    monkeypatch.setattr(evaluation.os.path, "relpath", incompatible_drives)
    with pytest.raises(ManifestValidationError, match="same drive"):
        evaluation.relative_manifest_path(
            tmp_path / "manifest.csv", tmp_path / "clips" / "track.wav"
        )


def test_local_benchmark_uses_stable_identifier_and_preserves_it_in_cache(monkeypatch, tmp_path):
    clips = {}
    for index, name in enumerate(("correct.wav", "false-positive.wav", "fallback.wav"), start=1):
        path = tmp_path / name
        sf.write(
            path,
            np.full(12_000, index / 100, dtype=np.float32),
            8_000,
            subtype="PCM_16",
        )
        clips[name] = path
    manifest = _write_clip_manifest(
        tmp_path,
        [
            {
                "source_id": "correct-source",
                "clip_id": "correct-source_4s",
                "audio_path": clips["correct.wav"].name,
                "expected_title": "Correct Song",
                "expected_artist": "Correct Artist",
                "genre": "pop",
                "era": "2020s",
                "recording_condition": "room",
                "clip_length_s": "4",
                "local_id": "local-correct",
            },
            {
                "source_id": "false-source",
                "clip_id": "false-source_4s",
                "audio_path": clips["false-positive.wav"].name,
                "expected_title": "False Positive Song",
                "expected_artist": "False Positive Artist",
                "genre": "pop",
                "era": "2020s",
                "recording_condition": "room",
                "clip_length_s": "4",
                "local_id": "local-expected",
            },
            {
                "source_id": "fallback-source",
                "clip_id": "fallback-source_4s",
                "audio_path": clips["fallback.wav"].name,
                "expected_title": "Fallback Song",
                "expected_artist": "Fallback Artist",
                "genre": "pop",
                "era": "2020s",
                "recording_condition": "room",
                "clip_length_s": "4",
            },
        ],
    )
    index = tmp_path / "fingerprint-index.json"
    index.write_text("{}", encoding="utf-8")
    calls = 0

    def fake_local(clip, _config, timeout):
        nonlocal calls
        calls += 1
        assert timeout == 15
        result = {
            "correct.wav": {"provider_id": "local-correct", "title": "Wrong", "artist": "Wrong"},
            "false-positive.wav": {
                "provider_id": "local-other",
                "title": "False Positive Song",
                "artist": "False Positive Artist",
            },
            "fallback.wav": {"title": "Fallback Song", "artist": "Fallback Artist"},
        }[clip.path.name]
        return {"status": "matched", **result}

    monkeypatch.setattr(
        benchmark,
        "load_config",
        lambda _path: AppConfig(audd_api_token="", fingerprint_index_path=str(index)),
    )
    monkeypatch.setitem(
        benchmark.BACKENDS,
        "local",
        ("Local constellation-hash", fake_local, "fingerprint_index_path"),
    )

    first = benchmark.run(
        manifest,
        tmp_path / "first.json",
        15,
        tmp_path / ".env",
        backends=["local"],
        cache_dir=tmp_path / "cache",
    )
    second = benchmark.run(
        manifest,
        tmp_path / "second.json",
        15,
        tmp_path / ".env",
        backends=["local"],
        cache_dir=tmp_path / "cache",
    )

    first_summary = first["backend_summary"]["local"]
    second_summary = second["backend_summary"]["local"]
    assert calls == 3
    assert first_summary["correct"] == 2
    assert first_summary["false_positives"] == 1
    assert first["records"][0]["identity_method"] == "provider_identifier"
    assert first["records"][1]["correct"] is False
    assert first["records"][1]["provider_id"] == "local-other"
    assert first["records"][2]["identity_method"] == "normalized_title_artist"
    assert first["metadata"]["cache_state"] == "cold"
    assert second["metadata"]["cache_state"] == "warm"
    assert second_summary["accuracy"] == first_summary["accuracy"]
    assert [record["provider_id"] for record in second["records"]] == [
        "local-correct",
        "local-other",
        "",
    ]


def test_benchmark_cache_is_deterministic_and_does_not_store_credentials(monkeypatch, tmp_path):
    clip = _wav(tmp_path / "clip.wav")
    manifest = _clip_manifest(tmp_path, clip)
    output = tmp_path / "result.json"
    cache_dir = tmp_path / "cache"
    calls = 0

    def fake_provider(_clip, _config, timeout):
        nonlocal calls
        calls += 1
        assert timeout == 11
        return {
            "status": "matched",
            "title": "Example Song",
            "artist": "Example Artist",
            "result": {"key": "stable-provider-id"},
        }

    monkeypatch.setattr(
        benchmark,
        "load_config",
        lambda _path: AppConfig(audd_api_token="", rapidapi_key="private-key"),
    )
    monkeypatch.setitem(
        benchmark.BACKENDS,
        "rapidapi",
        ("RapidAPI/Shazam", fake_provider, "rapidapi_key"),
    )
    first = benchmark.run(
        manifest,
        output,
        11,
        tmp_path / ".env",
        backends=["rapidapi"],
        cache_dir=cache_dir,
    )
    second = benchmark.run(
        manifest,
        output,
        11,
        tmp_path / ".env",
        backends=["rapidapi"],
        cache_dir=cache_dir,
    )

    assert calls == 1
    assert first["metadata"]["cache_state"] == "cold"
    assert second["metadata"]["cache_state"] == "warm"
    assert second["records"][0]["cache_hit"] is True
    cache_text = next(cache_dir.glob("*.json")).read_text(encoding="utf-8")
    assert "private-key" not in cache_text
    assert "Authorization" not in cache_text


@pytest.mark.parametrize(
    ("hits", "misses", "expected"),
    [(0, 0, "empty"), (0, 2, "cold"), (2, 0, "warm"), (2, 1, "mixed")],
)
def test_cache_state_distinguishes_empty_cold_warm_and_mixed(hits, misses, expected):
    assert benchmark._cache_state(hits, misses) == expected


def test_refresh_cache_calls_backend_again(monkeypatch, tmp_path):
    clip = _wav(tmp_path / "clip.wav")
    manifest = _clip_manifest(tmp_path, clip)
    calls = 0

    def fake_provider(_clip, _config, timeout):
        nonlocal calls
        calls += 1
        return {"status": "no_match", "result": None}

    monkeypatch.setattr(
        benchmark,
        "load_config",
        lambda _path: AppConfig(audd_api_token="", rapidapi_key="key"),
    )
    monkeypatch.setitem(
        benchmark.BACKENDS,
        "rapidapi",
        ("RapidAPI/Shazam", fake_provider, "rapidapi_key"),
    )
    kwargs = {
        "backends": ["rapidapi"],
        "cache_dir": tmp_path / "cache",
        "refresh_cache": True,
    }
    benchmark.run(manifest, tmp_path / "one.json", 15, tmp_path / ".env", **kwargs)
    benchmark.run(manifest, tmp_path / "two.json", 15, tmp_path / ".env", **kwargs)
    assert calls == 2


def test_incomplete_credentials_never_call_backend_or_claim_complete(monkeypatch, tmp_path):
    clip = _wav(tmp_path / "clip.wav")
    manifest = _clip_manifest(tmp_path, clip)
    called = False

    def forbidden_provider(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider call should not occur")

    monkeypatch.setattr(benchmark, "load_config", lambda _path: AppConfig(audd_api_token=""))
    monkeypatch.setitem(
        benchmark.BACKENDS,
        "rapidapi",
        ("RapidAPI/Shazam", forbidden_provider, "rapidapi_key"),
    )
    results = benchmark.run(
        manifest,
        tmp_path / "result.json",
        15,
        tmp_path / ".env",
        backends=["rapidapi"],
        cache_dir=tmp_path / "cache",
    )
    assert called is False
    assert results["metadata"]["complete"] is False
    assert results["records"][0]["status"] == "not_configured"
    assert not (tmp_path / "cache").exists()


def _configured_rapidapi(monkeypatch, provider):
    monkeypatch.setattr(
        benchmark,
        "load_config",
        lambda _path: AppConfig(audd_api_token="", rapidapi_key="key"),
    )
    monkeypatch.setitem(
        benchmark.BACKENDS,
        "rapidapi",
        ("RapidAPI/Shazam", provider, "rapidapi_key"),
    )


def test_one_missing_clip_is_visible_and_excluded_from_accuracy(monkeypatch, tmp_path):
    manifest = _write_clip_manifest(
        tmp_path,
        [
            {
                "source_id": "track-001",
                "clip_id": "track-001_4s",
                "audio_path": "missing.wav",
                "expected_title": "Example Song",
                "expected_artist": "Example Artist",
                "genre": "pop",
                "era": "2010s",
                "recording_condition": "room",
                "clip_length_s": "4",
            }
        ],
    )
    called = False

    def forbidden_provider(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("missing clips must not call providers")

    _configured_rapidapi(monkeypatch, forbidden_provider)
    results = benchmark.run(
        manifest,
        tmp_path / "result.json",
        15,
        tmp_path / ".env",
        backends=["rapidapi"],
        cache_dir=tmp_path / "cache",
    )

    summary = results["backend_summary"]["rapidapi"]
    assert called is False
    assert results["metadata"]["complete"] is False
    assert results["metadata"]["missing_clip_count"] == 1
    assert results["metadata"]["cache_state"] == "empty"
    assert summary["total_clips"] == 1
    assert summary["attempted"] == 0
    assert summary["accuracy_denominator"] == 0
    assert summary["missing_inputs"] == 1
    assert results["records"][0]["error_code"] == "missing_clip"
    assert results["records"][0]["status"] == "invalid_audio"
    readme = tmp_path / "readme.md"
    readme.write_text(
        "before\n<!-- BENCHMARK_RESULTS:START -->\nold\n<!-- BENCHMARK_RESULTS:END -->\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="incomplete"):
        update_readme(readme, results)
    assert "old" in readme.read_text(encoding="utf-8")


def test_all_missing_clips_mark_benchmark_incomplete_without_cache_operations(
    monkeypatch, tmp_path
):
    rows = []
    for length in ("4", "8"):
        rows.append(
            {
                "source_id": "track-001",
                "clip_id": f"track-001_{length}s",
                "audio_path": f"missing-{length}.wav",
                "expected_title": "Example Song",
                "expected_artist": "Example Artist",
                "genre": "pop",
                "era": "2010s",
                "recording_condition": "room",
                "clip_length_s": length,
            }
        )
    manifest = _write_clip_manifest(tmp_path, rows)
    _configured_rapidapi(monkeypatch, lambda *_args, **_kwargs: {"status": "matched"})

    results = benchmark.run(
        manifest,
        tmp_path / "result.json",
        15,
        tmp_path / ".env",
        backends=["rapidapi"],
        cache_dir=tmp_path / "cache",
    )

    summary = results["backend_summary"]["rapidapi"]
    assert results["metadata"]["complete"] is False
    assert results["metadata"]["missing_clip_count"] == 2
    assert results["metadata"]["cache_state"] == "empty"
    assert summary["attempted"] == 0
    assert summary["accuracy_denominator"] == 0
    assert summary["missing_inputs"] == 2
    assert not (tmp_path / "cache").exists()


def test_mixed_present_and_missing_clips_excludes_only_missing_clip_from_denominator(
    monkeypatch, tmp_path
):
    present = _wav(tmp_path / "present.wav")
    manifest = _write_clip_manifest(
        tmp_path,
        [
            {
                "source_id": "track-001",
                "clip_id": "track-001_4s",
                "audio_path": present.name,
                "expected_title": "Example Song",
                "expected_artist": "Example Artist",
                "genre": "pop",
                "era": "2010s",
                "recording_condition": "room",
                "clip_length_s": "4",
            },
            {
                "source_id": "track-001",
                "clip_id": "track-001_8s",
                "audio_path": "missing.wav",
                "expected_title": "Example Song",
                "expected_artist": "Example Artist",
                "genre": "pop",
                "era": "2010s",
                "recording_condition": "room",
                "clip_length_s": "8",
            },
        ],
    )
    _configured_rapidapi(
        monkeypatch,
        lambda *_args, **_kwargs: {
            "status": "matched",
            "title": "Example Song",
            "artist": "Example Artist",
        },
    )

    results = benchmark.run(
        manifest,
        tmp_path / "result.json",
        15,
        tmp_path / ".env",
        backends=["rapidapi"],
        cache_dir=tmp_path / "cache",
    )

    summary = results["backend_summary"]["rapidapi"]
    assert results["metadata"]["complete"] is False
    assert summary["total_clips"] == 2
    assert summary["attempted"] == 1
    assert summary["correct"] == 1
    assert summary["accuracy_denominator"] == 1
    assert summary["missing_inputs"] == 1
    assert results["metadata"]["cache_state"] == "cold"


def test_stable_identifier_wins_and_title_artist_is_explicit_fallback(monkeypatch, tmp_path):
    clip = _wav(tmp_path / "clip.wav")
    manifest = _clip_manifest(tmp_path, clip, provider_id="stable-id")
    config = AppConfig(audd_api_token="", rapidapi_key="key")
    monkeypatch.setitem(
        benchmark.BACKENDS,
        "rapidapi",
        (
            "RapidAPI/Shazam",
            lambda *_args, **_kwargs: {
                "status": "matched",
                "title": "Wrong title",
                "artist": "Wrong artist",
                "result": {"key": "stable-id"},
            },
            "rapidapi_key",
        ),
    )
    row = benchmark.load_clip_manifest(manifest, require_audio=False)[0]
    result = benchmark._run_backend("rapidapi", clip, row, config, 15)
    assert result["correct"] is True
    assert result["identity_method"] == "provider_identifier"


def test_aggregate_reports_rates_conditions_and_percentiles():
    records = [
        {
            "backend": "rapidapi",
            "source_id": "one",
            "clip_id": "one_4s",
            "clip_length_s": 4.0,
            "recording_condition": "near",
            "status": "matched",
            "correct": True,
            "latency_ms": 100.0,
        },
        {
            "backend": "rapidapi",
            "source_id": "one",
            "clip_id": "one_8s",
            "clip_length_s": 8.0,
            "recording_condition": "room",
            "status": "matched",
            "correct": False,
            "latency_ms": 200.0,
        },
        {
            "backend": "rapidapi",
            "source_id": "one",
            "clip_id": "one_15s",
            "clip_length_s": 15.0,
            "recording_condition": "noise",
            "status": "error",
            "error_code": "timeout",
            "correct": False,
            "latency_ms": 300.0,
        },
        {
            "backend": "rapidapi",
            "source_id": "one",
            "clip_id": "one_bad",
            "clip_length_s": 4.0,
            "recording_condition": "near",
            "status": "invalid_audio",
            "correct": False,
            "error_code": "malformed_wav",
            "latency_ms": 50.0,
        },
    ]
    summary = benchmark._aggregate(records, "rapidapi")
    assert summary["accuracy_numerator"] == 1
    assert summary["accuracy_denominator"] == 4
    assert summary["false_positives"] == 1
    assert summary["timeouts"] == 1
    assert summary["unusable_inputs"] == 1
    assert summary["latency_ms"]["p95"] == 300.0
    assert summary["by_recording_condition"]["near"]["accuracy_numerator"] == 1


def _complete_results() -> dict:
    records = []
    lengths_and_conditions = ((4.0, "room"), (8.0, "near"), (15.0, "noise"))
    for source_number in range(1, 31):
        source_id = f"source-{source_number:02d}"
        for clip_length, condition in lengths_and_conditions:
            for backend in benchmark.BACKENDS:
                records.append(
                    {
                        "backend": backend,
                        "source_id": source_id,
                        "clip_id": f"{source_id}_{clip_length:g}s",
                        "clip_length_s": clip_length,
                        "recording_condition": condition,
                        "status": "matched",
                        "correct": True,
                        "identity_method": "provider_identifier",
                        "provider_id": f"{backend}-{source_id}",
                        "returned_title": "Synthetic Song",
                        "returned_artist": "Synthetic Artist",
                        "latency_ms": 10.0 + source_number,
                    }
                )
    return {
        "schema_version": 2,
        "metadata": {
            "complete": True,
            "generated_at_utc": "2026-01-01T00:00:00+00:00",
            "python_version": "3.12",
            "os": "test",
            "network_region": "EU",
            "provider_plan": "reviewed-plan",
            "timeout_seconds": 15,
            "cache_state": "cold",
            "cache_hits": 0,
            "cache_misses": 360,
            "missing_clip_count": 0,
            "selected_backends": list(benchmark.BACKENDS),
            "incomplete_reasons": [],
        },
        "clip_count": 90,
        "backend_summary": {
            backend: benchmark._aggregate(records, backend) for backend in benchmark.BACKENDS
        },
        "records": records,
    }


def _replace_records(results: dict, records: list[dict], clip_count: int) -> dict:
    results["records"] = records
    results["clip_count"] = clip_count
    results["backend_summary"] = {
        backend: benchmark._aggregate(records, backend) for backend in benchmark.BACKENDS
    }
    return results


def test_readme_update_refuses_incomplete_results_without_writing(tmp_path):
    readme = tmp_path / "readme.md"
    original = "before\n<!-- BENCHMARK_RESULTS:START -->\nold\n<!-- BENCHMARK_RESULTS:END -->\n"
    readme.write_text(original, encoding="utf-8")
    incomplete = {"metadata": {"complete": False}, "backend_summary": {}, "clip_count": 1}
    with pytest.raises(ValueError, match="incomplete"):
        validate_complete_results(incomplete)
    with pytest.raises(ValueError, match="incomplete"):
        update_readme(readme, incomplete)
    assert readme.read_text(encoding="utf-8") == original


def test_validator_checks_counts_even_when_metadata_claims_complete():
    results = {
        "metadata": {"complete": True, "incomplete_reasons": []},
        "clip_count": 0,
        "backend_summary": {backend: {} for backend in benchmark.BACKENDS},
        "records": [],
    }

    with pytest.raises(ValueError, match="clip_count must be greater than zero"):
        validate_complete_results(results)


def test_validator_rejects_inconsistent_denominator_even_when_metadata_claims_complete():
    results = _complete_results()
    results["backend_summary"]["rapidapi"]["accuracy_denominator"] = 0

    with pytest.raises(ValueError, match="accuracy denominator is inconsistent"):
        validate_complete_results(results)


def test_readme_update_imports_only_generated_complete_metrics(tmp_path):
    readme = tmp_path / "readme.md"
    readme.write_text(
        "before\n<!-- BENCHMARK_RESULTS:START -->\nold\n<!-- BENCHMARK_RESULTS:END -->\n",
        encoding="utf-8",
    )
    results = _complete_results()

    update_readme(readme, results)

    updated = readme.read_text(encoding="utf-8")
    assert "Benchmark results" in updated
    assert "reviewed-plan" in updated
    assert "\nold\n" not in updated


@pytest.mark.parametrize(
    "shape",
    ["one_clip", "missing_length", "duplicate_pair", "few_conditions"],
)
def test_readme_import_refuses_incomplete_corpus_shapes(tmp_path, shape):
    results = copy.deepcopy(_complete_results())
    if shape == "one_clip":
        records = [
            record
            for record in results["records"]
            if record["source_id"] == "source-01" and record["clip_length_s"] == 4.0
        ]
        _replace_records(results, records, 1)
    elif shape == "missing_length":
        records = [
            record
            for record in results["records"]
            if not (record["source_id"] == "source-01" and record["clip_length_s"] == 15.0)
        ]
        _replace_records(results, records, 89)
    elif shape == "duplicate_pair":
        for record in results["records"]:
            if record["source_id"] == "source-02" and record["clip_length_s"] == 4.0:
                record["source_id"] = "source-01"
        _replace_records(results, results["records"], 90)
    else:
        for record in results["records"]:
            record["recording_condition"] = "room"
        _replace_records(results, results["records"], 90)

    readme = tmp_path / f"{shape}.md"
    original = "before\n<!-- BENCHMARK_RESULTS:START -->\nold\n<!-- BENCHMARK_RESULTS:END -->\n"
    readme.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete"):
        update_readme(readme, results)
    assert readme.read_text(encoding="utf-8") == original


def test_readme_import_refuses_malformed_audio_even_with_complete_shape(tmp_path):
    results = copy.deepcopy(_complete_results())
    target = results["records"][0]
    target.update(
        {
            "status": "invalid_audio",
            "error_code": "malformed_wav",
            "correct": False,
            "failure_reason": "benchmark input could not be decoded",
        }
    )
    _replace_records(results, results["records"], 90)

    readme = tmp_path / "malformed.md"
    readme.write_text(
        "before\n<!-- BENCHMARK_RESULTS:START -->\nold\n<!-- BENCHMARK_RESULTS:END -->\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unusable audio"):
        update_readme(readme, results)


@pytest.mark.parametrize("mutation", ["accuracy", "error_rate", "latency", "breakdown", "failures"])
def test_readme_import_refuses_altered_derived_summary_values(tmp_path, mutation):
    results = copy.deepcopy(_complete_results())
    summary = results["backend_summary"]["rapidapi"]
    if mutation == "accuracy":
        summary["accuracy"] = 0.5
    elif mutation == "error_rate":
        summary["provider_error_rate"] = 0.5
    elif mutation == "latency":
        summary["latency_ms"]["mean"] = 999.0
    elif mutation == "breakdown":
        summary["by_clip_length"]["4.0"]["accuracy"] = 0.5
    else:
        summary["failures"] = [{"clip_id": "fabricated"}]

    readme = tmp_path / f"{mutation}.md"
    readme.write_text(
        "before\n<!-- BENCHMARK_RESULTS:START -->\nold\n<!-- BENCHMARK_RESULTS:END -->\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="summary does not match records"):
        update_readme(readme, results)


def test_synthetic_complete_corpus_passes_readme_import_gate(tmp_path):
    results = _complete_results()
    validate_complete_results(results)


def test_report_generation_contains_metadata_and_metrics(tmp_path):
    clip = _wav(tmp_path / "clip.wav")
    manifest = _clip_manifest(tmp_path, clip)
    results = benchmark.run(
        manifest,
        tmp_path / "result.json",
        15,
        tmp_path / ".env",
        backends=["rapidapi"],
        cache_dir=tmp_path / "cache",
        network_region="EU",
        provider_plan="test-plan",
    )
    report = (tmp_path / "result.md").read_text(encoding="utf-8")
    assert "Generated UTC" in report
    assert "P95 ms" in report
    assert "speaker_mic_room" in report
    assert results["metadata"]["network_region"] == "EU"


def test_render_markdown_has_one_section_per_backend_and_breakdown(tmp_path):
    records = []
    for backend in benchmark.BACKENDS:
        for clip_length, condition in ((4.0, "near"), (8.0, "room")):
            records.append(
                {
                    "backend": backend,
                    "source_id": "track-001",
                    "clip_id": f"track-001_{backend}_{clip_length:g}s",
                    "clip_length_s": clip_length,
                    "recording_condition": condition,
                    "status": "matched",
                    "correct": True,
                    "latency_ms": 10.0,
                }
            )
    results = {
        "metadata": {
            "generated_at_utc": "2026-01-01T00:00:00+00:00",
            "python_version": "3.12",
            "os": "test",
            "network_region": "EU",
            "provider_plan": "test-plan",
            "timeout_seconds": 15,
            "cache_state": "cold",
        },
        "backend_summary": {
            backend: benchmark._aggregate(records, backend) for backend in benchmark.BACKENDS
        },
    }

    report = benchmark.render_markdown(results)

    for backend in benchmark.BACKENDS:
        assert report.count(f"## {backend} by clip length") == 1
        assert report.count(f"## {backend} by recording condition") == 1
