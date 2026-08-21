from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.evaluation import load_clip_manifest, resolve_manifest_path
from shazam_project import matcher
from shazam_project.config import AppConfig, load_config
from shazam_project.recorder import AudioInputError, load_audio_file

BACKENDS: dict[str, tuple[str, Callable[..., dict[str, Any]], str]] = {
    "rapidapi": ("RapidAPI/Shazam", matcher.match_audio_shazam, "rapidapi_key"),
    "acoustid": ("AcoustID", matcher.match_audio_acoustid, "acoustid_api_key"),
    "audd": ("AudD", matcher._match_audio_audd, "audd_api_token"),
    "local": ("Local constellation-hash", matcher.match_audio_local, "fingerprint_index_path"),
}
BACKEND_VERSIONS = {
    "rapidapi": "matcher-rapidapi-v1",
    "acoustid": "matcher-acoustid-v1",
    "audd": "matcher-audd-v1",
    "local": "matcher-local-v1",
}
CACHE_SCHEMA_VERSION = 1
EXPECTED_SOURCE_COUNT = 30
EXPECTED_CLIP_LENGTHS = frozenset({4.0, 8.0, 15.0})
EXPECTED_CLIPS_PER_BACKEND = EXPECTED_SOURCE_COUNT * len(EXPECTED_CLIP_LENGTHS)
SAFE_RESULT_FIELDS = {
    "status",
    "error_code",
    "error",
    "title",
    "artist",
    "album",
    "score",
    "votes",
    "fingerprint_hashes",
    "matched_hashes",
    "offset_frames",
    "backend",
    "provider_id",
}


def _normalise(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join("".join(char if char.isalnum() else " " for char in text.lower()).split())


def _matches(actual: str | None, expected: str) -> bool:
    expected_values = [_normalise(item) for item in expected.split("|") if item.strip()]
    actual_value = _normalise(actual)
    return bool(actual_value and expected_values and actual_value in expected_values)


def _matches_identifier(actual: str | None, expected: str) -> bool:
    actual_value = str(actual or "").strip()
    expected_values = {item.strip() for item in expected.split("|") if item.strip()}
    return bool(actual_value and expected_values and actual_value in expected_values)


def _failure_reason(status: str, result: dict[str, Any]) -> str:
    del result
    if status == "not_configured":
        return "backend credentials are not configured"
    if status == "no_match":
        return "provider returned no match"
    if status == "invalid_audio":
        return "benchmark input could not be decoded"
    if status == "error":
        return "provider returned an error"
    return "returned metadata did not match the manifest ground truth"


def _configured(config: AppConfig, attribute: str) -> bool:
    value = getattr(config, attribute, "")
    if attribute == "fingerprint_index_path" and value:
        try:
            return Path(value).is_file()
        except (OSError, TypeError, ValueError):
            return False
    return bool(value)


def _provider_identifier(backend: str, result: dict[str, Any]) -> str | None:
    direct = result.get("provider_id")
    if direct:
        return str(direct)
    raw = result.get("result")
    if not isinstance(raw, dict):
        return None
    keys = {
        "rapidapi": ("key", "id", "track_id"),
        "acoustid": ("id", "recording_id"),
        "audd": ("song_id", "id", "track_id"),
        "local": ("track_id", "source_id", "id"),
    }[backend]
    for key in keys:
        if raw.get(key):
            return str(raw[key])
    return None


def _expected_provider_identifier(backend: str, row: dict[str, str]) -> str:
    return row.get(f"{backend}_id", "").strip()


def _safe_result(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key in SAFE_RESULT_FIELDS}


def _clip_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backend_settings(backend: str, config: AppConfig, timeout: int) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "timeout_seconds": int(timeout),
        "internal_sample_rate": int(config.internal_sample_rate),
        "min_audio_seconds": float(config.min_audio_seconds),
        "max_audio_seconds": float(config.max_audio_seconds),
    }
    if backend == "local" and config.fingerprint_index_path:
        index_path = Path(config.fingerprint_index_path)
        if index_path.is_file():
            settings["index_checksum"] = _clip_checksum(index_path)
    return settings


def _cache_key(backend: str, clip_checksum: str, config: AppConfig, timeout: int) -> str:
    payload = {
        "schema": CACHE_SCHEMA_VERSION,
        "backend": backend,
        "backend_version": BACKEND_VERSIONS[backend],
        "clip_checksum": clip_checksum,
        "settings": _backend_settings(backend, config, timeout),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cache_file(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}.json"


def _atomic_write_text(path: Path, content: str) -> None:
    """Write benchmark artifacts without leaving truncated cache records."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _cache_state(cache_hits: int, cache_misses: int) -> str:
    if cache_hits and cache_misses:
        return "mixed"
    if cache_hits:
        return "warm"
    if cache_misses:
        return "cold"
    return "empty"


def _read_cache(cache_dir: Path, key: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_cache_file(cache_dir, key).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if data.get("cache_key") != key or not isinstance(data.get("result"), dict):
        return None
    return data["result"]


def _write_cache(
    cache_dir: Path,
    key: str,
    backend: str,
    checksum: str,
    config: AppConfig,
    timeout: int,
    result: dict[str, Any],
) -> None:
    payload = {
        "cache_schema": CACHE_SCHEMA_VERSION,
        "cache_key": key,
        "backend": backend,
        "backend_version": BACKEND_VERSIONS[backend],
        "clip_checksum": checksum,
        "settings": _backend_settings(backend, config, timeout),
        "result": _safe_result(result),
    }
    _atomic_write_text(
        _cache_file(cache_dir, key),
        json.dumps(payload, indent=2, sort_keys=True),
    )


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    if not 0 <= percentile <= 100:
        raise ValueError("percentile must be between 0 and 100")
    ordered = sorted(values)
    rank = max(1, int((percentile / 100) * len(ordered) + 0.999999))
    return round(ordered[rank - 1], 2)


def _run_backend(
    backend: str,
    clip_path: Path,
    row: dict[str, str],
    config: AppConfig,
    timeout: int,
) -> dict[str, Any]:
    display_name, function, credential_attribute = BACKENDS[backend]
    base: dict[str, Any] = {
        "backend": backend,
        "backend_name": display_name,
        "source_id": row["source_id"],
        "clip_id": row["clip_id"],
        "clip_length_s": float(row["clip_length_s"]),
        "recording_condition": row["recording_condition"],
    }

    if not _configured(config, credential_attribute):
        base.update(
            {
                "status": "not_configured",
                "correct": False,
                "identity_method": "not_attempted",
                "failure_reason": _failure_reason("not_configured", base),
            }
        )
        return base

    started = time.perf_counter()
    try:
        clip = load_audio_file(clip_path, config=config)
        result = function(clip, config, timeout=timeout)
        if not isinstance(result, dict):
            result = {"status": "error", "error_code": "invalid_provider_result"}
        status = result.get("status", "error")
    except AudioInputError as exc:
        result = {"status": "invalid_audio", "error_code": exc.code}
        status = "invalid_audio"
    except Exception:
        result = {"status": "error", "error_code": "benchmark_backend_error"}
        status = "error"

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    provider_id = _provider_identifier(backend, result)
    expected_id = _expected_provider_identifier(backend, row)
    identity_method = "not_applicable"
    correct = False
    if status == "matched":
        if expected_id:
            identity_method = "provider_identifier"
            correct = bool(provider_id and _matches_identifier(provider_id, expected_id))
        else:
            identity_method = "normalized_title_artist"
            correct = _matches(result.get("title"), row["expected_title"]) and _matches(
                result.get("artist"), row["expected_artist"]
            )

    base.update(
        {
            "status": status,
            "error_code": result.get("error_code", "") if status != "matched" else "",
            "correct": correct,
            "identity_method": identity_method,
            "provider_id": provider_id or "",
            "latency_ms": elapsed_ms,
            "returned_title": str(result.get("title") or ""),
            "returned_artist": str(result.get("artist") or ""),
        }
    )
    if not correct:
        base["failure_reason"] = _failure_reason(status, result)
    return base


def _group_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    configured_records = [record for record in records if record["status"] != "not_configured"]
    attempted = [
        record for record in configured_records if record.get("error_code") != "missing_clip"
    ]
    correct = [record for record in attempted if record["correct"]]
    denominator = len(attempted)
    latencies = [record["latency_ms"] for record in attempted if "latency_ms" in record]

    def count(predicate: Callable[[dict[str, Any]], bool]) -> int:
        return sum(predicate(record) for record in attempted)

    no_match = count(lambda record: record["status"] == "no_match")
    false_positive = count(lambda record: record["status"] == "matched" and not record["correct"])
    provider_error = count(lambda record: record["status"] == "error")
    timeout = count(lambda record: record.get("error_code") == "timeout")
    unusable = count(lambda record: record["status"] == "invalid_audio")
    missing = sum(record.get("error_code") == "missing_clip" for record in configured_records)

    def rate(numerator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    def input_rate(numerator: int) -> float | None:
        return round(numerator / len(configured_records), 4) if configured_records else None

    return {
        "attempted": denominator,
        "correct": len(correct),
        "accuracy": rate(len(correct)),
        "accuracy_numerator": len(correct),
        "accuracy_denominator": denominator,
        "no_match": no_match,
        "no_match_rate": rate(no_match),
        "false_positives": false_positive,
        "false_positive_rate": rate(false_positive),
        "provider_errors": provider_error,
        "provider_error_rate": rate(provider_error),
        "timeouts": timeout,
        "timeout_rate": rate(timeout),
        "unusable_inputs": unusable,
        "unusable_input_rate": rate(unusable),
        "missing_inputs": missing,
        "missing_input_rate": input_rate(missing),
        "not_configured": sum(record["status"] == "not_configured" for record in records),
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 2) if latencies else None,
            "median": round(statistics.median(latencies), 2) if latencies else None,
            "p95": _percentile(latencies, 95),
        },
    }


def _aggregate(records: list[dict[str, Any]], backend: str) -> dict[str, Any]:
    backend_records = [record for record in records if record["backend"] == backend]
    summary = _group_summary(backend_records)
    by_length: dict[str, dict[str, Any]] = {}
    for length in sorted({record["clip_length_s"] for record in backend_records}):
        subset = [record for record in backend_records if record["clip_length_s"] == length]
        by_length[str(length)] = _group_summary(subset)
        by_length[str(length)]["total_clips"] = len(subset)

    by_condition: dict[str, dict[str, Any]] = {}
    for condition in sorted(
        {record.get("recording_condition", "unknown") for record in backend_records}
    ):
        subset = [
            record
            for record in backend_records
            if record.get("recording_condition", "unknown") == condition
        ]
        by_condition[condition] = _group_summary(subset)
        by_condition[condition]["total_clips"] = len(subset)

    summary.update(
        {
            "total_clips": len(backend_records),
            "by_clip_length": by_length,
            "by_recording_condition": by_condition,
            "failures": [
                {
                    "source_id": record.get("source_id", record["clip_id"].split("_", 1)[0]),
                    "clip_id": record["clip_id"],
                    "clip_length_s": record["clip_length_s"],
                    "recording_condition": record.get("recording_condition", "unknown"),
                    "status": record["status"],
                    "error_code": record.get("error_code", ""),
                    "provider_id": record.get("provider_id", ""),
                    "identity_method": record.get("identity_method", ""),
                    "reason": record.get("failure_reason", "unknown failure"),
                }
                for record in backend_records
                if not record["correct"]
            ],
        }
    )
    return summary


def _corpus_shape_reasons(records: list[dict[str, Any]], selected_backends: list[str]) -> list[str]:
    reasons: list[str] = []
    for backend in selected_backends:
        backend_records = [record for record in records if record.get("backend") == backend]
        if len(backend_records) != EXPECTED_CLIPS_PER_BACKEND:
            reasons.append(
                f"{backend} must contain exactly {EXPECTED_CLIPS_PER_BACKEND} clip records"
            )

        source_ids = {record.get("source_id") for record in backend_records}
        if len(source_ids) != EXPECTED_SOURCE_COUNT or any(
            not isinstance(source_id, str) or not source_id for source_id in source_ids
        ):
            reasons.append(
                f"{backend} must contain exactly {EXPECTED_SOURCE_COUNT} distinct source IDs"
            )

        source_lengths: dict[str, list[Any]] = {}
        pairs: list[tuple[Any, Any]] = []
        for record in backend_records:
            source_id = record.get("source_id")
            length = record.get("clip_length_s")
            pairs.append((source_id, length))
            source_lengths.setdefault(source_id, []).append(length)
        if len(set(pairs)) != len(pairs):
            reasons.append(f"{backend} contains duplicate source/clip-length pairs")
        if any(set(lengths) != EXPECTED_CLIP_LENGTHS for lengths in source_lengths.values()):
            reasons.append(
                f"{backend} must contain one 4-second, one 8-second, and one 15-second clip per source"
            )

        conditions = {
            record.get("recording_condition")
            for record in backend_records
            if record.get("recording_condition")
        }
        if len(conditions) < 3:
            reasons.append(f"{backend} must contain at least three recording conditions")
    return reasons


def _hardware_summary() -> dict[str, str]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
    }


def _safe_operator_value(value: str) -> str:
    return " ".join(value.strip().split())[:120]


def _incomplete_reasons(
    selected_backends: list[str],
    summaries: dict[str, dict[str, Any]],
    clip_count: int,
    records: list[dict[str, Any]],
) -> list[str]:
    reasons = []
    if clip_count == 0:
        reasons.append("the clip manifest contains no clips")
    if set(selected_backends) != set(BACKENDS):
        reasons.append("all four backends were not selected")
    reasons.extend(_corpus_shape_reasons(records, selected_backends))
    for backend in selected_backends:
        if summaries[backend]["not_configured"]:
            reasons.append(f"{backend} credentials or local index are incomplete")
        if summaries[backend]["missing_inputs"]:
            reasons.append(f"{backend} has missing or unusable clips")
        if summaries[backend]["unusable_inputs"]:
            reasons.append(f"{backend} has unusable audio inputs")
        expected_records = (
            summaries[backend]["attempted"]
            + summaries[backend]["missing_inputs"]
            + summaries[backend]["not_configured"]
        )
        if summaries[backend]["total_clips"] != clip_count or expected_records != clip_count:
            reasons.append(f"{backend} did not produce one result per clip")
        if summaries[backend]["accuracy_denominator"] != summaries[backend]["attempted"]:
            reasons.append(f"{backend} has an inconsistent accuracy denominator")
    return reasons


def render_markdown(results: dict[str, Any]) -> str:
    metadata = results["metadata"]
    lines = [
        "# Benchmark results",
        "",
        f"- Generated UTC: `{metadata['generated_at_utc']}`",
        f"- Python: `{metadata['python_version']}`",
        f"- OS: `{metadata['os']}`",
        f"- Network region: `{metadata['network_region'] or 'not supplied'}`",
        f"- Provider plan: `{metadata['provider_plan'] or 'not supplied'}`",
        f"- Timeout: `{metadata['timeout_seconds']}s`",
        f"- Cache state: `{metadata['cache_state']}`",
        "",
        "Accuracy denominator is attempted clips; missing clips are visible as unusable inputs and excluded, while `not_configured` results are reported separately.",
        "Cache-hit latency is local cache-read time, not provider recognition time; mixed reports can contain both latency types.",
        "Stable provider identifiers are used when a matching manifest identifier is supplied; otherwise normalized title and artist are the documented fallback.",
        "",
        "| Backend | Clips | Attempted | Correct | Accuracy | No-match | False-positive | Provider error | Timeout | Unusable | Mean ms | Median ms | P95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for backend, summary in results["backend_summary"].items():
        latency = summary["latency_ms"]
        lines.append(
            f"| {backend} | {summary['total_clips']} | {summary['accuracy_denominator']} | "
            f"{summary['accuracy_numerator']} | {summary['accuracy']} | {summary['no_match_rate']} | "
            f"{summary['false_positive_rate']} | {summary['provider_error_rate']} | "
            f"{summary['timeout_rate']} | {summary['unusable_input_rate']} | "
            f"{latency['mean']} | {latency['median']} | {latency['p95']} |"
        )
    for backend, summary in results["backend_summary"].items():
        lines.extend(["", f"## {backend} by clip length"])
        for length, values in summary["by_clip_length"].items():
            lines.append(
                f"- `{length}s`: {values['accuracy_numerator']}/{values['accuracy_denominator']} "
                f"correct ({values['accuracy']})"
            )
        lines.extend(["", f"## {backend} by recording condition"])
        for condition, values in summary["by_recording_condition"].items():
            lines.append(
                f"- `{condition}`: {values['accuracy_numerator']}/{values['accuracy_denominator']} "
                f"correct ({values['accuracy']})"
            )
    return "\n".join(lines) + "\n"


def run(
    manifest_path: Path,
    output_path: Path,
    timeout: int,
    env_path: Path,
    *,
    backends: list[str] | None = None,
    cache_dir: Path | None = None,
    refresh_cache: bool = False,
    report_path: Path | None = None,
    network_region: str = "",
    provider_plan: str = "",
) -> dict[str, Any]:
    config = load_config(env_path)
    rows = load_clip_manifest(manifest_path, require_audio=False)
    selected_backends = backends or list(BACKENDS)
    invalid_backends = sorted(set(selected_backends) - set(BACKENDS))
    if invalid_backends:
        raise ValueError("Unknown backend: " + ", ".join(invalid_backends))
    cache_dir = cache_dir or output_path.parent / "cache"

    records: list[dict[str, Any]] = []
    cache_hits = 0
    cache_misses = 0
    for row in rows:
        clip_path = resolve_manifest_path(manifest_path, row["audio_path"])
        for backend in selected_backends:
            if not clip_path.is_file():
                record = {
                    "backend": backend,
                    "backend_name": BACKENDS[backend][0],
                    "source_id": row["source_id"],
                    "clip_id": row["clip_id"],
                    "clip_length_s": float(row["clip_length_s"]),
                    "recording_condition": row["recording_condition"],
                    "status": "invalid_audio",
                    "error_code": "missing_clip",
                    "correct": False,
                    "identity_method": "not_applicable",
                    "failure_reason": "benchmark clip file is missing",
                }
                records.append(record)
                continue

            if not _configured(config, BACKENDS[backend][2]):
                records.append(_run_backend(backend, clip_path, row, config, timeout))
                continue

            checksum = _clip_checksum(clip_path)
            key = _cache_key(backend, checksum, config, timeout)
            cache_started = time.perf_counter()
            cached = None if refresh_cache else _read_cache(cache_dir, key)
            if cached is not None:
                cache_hits += 1
                result = dict(cached)
                result["cache_hit"] = True
                result["cache_key"] = key
                result["latency_ms"] = round((time.perf_counter() - cache_started) * 1000, 2)
                records.append(
                    _run_backend_from_result(backend, row, result, cache_key=key, cache_hit=True)
                )
                continue

            cache_misses += 1
            record = _run_backend(backend, clip_path, row, config, timeout)
            records.append(record)
            result = {
                "status": record["status"],
                "error_code": record.get("error_code", ""),
                "title": record.get("returned_title", ""),
                "artist": record.get("returned_artist", ""),
                "provider_id": record.get("provider_id", ""),
            }
            if record["status"] not in {"not_configured", "invalid_audio"}:
                _write_cache(cache_dir, key, backend, checksum, config, timeout, result)

    summaries = {backend: _aggregate(records, backend) for backend in selected_backends}
    reasons = _incomplete_reasons(selected_backends, summaries, len(rows), records)
    cache_state = _cache_state(cache_hits, cache_misses)
    missing_clip_ids = {
        record["clip_id"] for record in records if record.get("error_code") == "missing_clip"
    }
    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "os": platform.platform(aliased=True),
        "python_version": platform.python_version(),
        "hardware": _hardware_summary(),
        "network_region": _safe_operator_value(network_region),
        "provider_plan": _safe_operator_value(provider_plan),
        "timeout_seconds": timeout,
        "cache_state": cache_state,
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "missing_clip_count": len(missing_clip_ids),
        "refresh_cache": refresh_cache,
        "selected_backends": selected_backends,
        "complete": not reasons,
        "incomplete_reasons": reasons,
    }
    output = {
        "schema_version": 2,
        "metadata": metadata,
        "clip_count": len(rows),
        "backend_summary": summaries,
        "records": records,
    }
    _atomic_write_text(output_path, json.dumps(output, indent=2, sort_keys=True))
    report_path = report_path or output_path.with_suffix(".md")
    _atomic_write_text(report_path, render_markdown(output))
    return output


def _run_backend_from_result(
    backend: str,
    row: dict[str, str],
    result: dict[str, Any],
    *,
    cache_key: str,
    cache_hit: bool,
) -> dict[str, Any]:
    provider_id = _provider_identifier(backend, result)
    expected_id = _expected_provider_identifier(backend, row)
    status = result.get("status", "error")
    if status == "matched" and expected_id:
        identity_method = "provider_identifier"
        correct = bool(provider_id and _matches_identifier(provider_id, expected_id))
    elif status == "matched":
        identity_method = "normalized_title_artist"
        correct = _matches(result.get("title"), row["expected_title"]) and _matches(
            result.get("artist"), row["expected_artist"]
        )
    else:
        identity_method = "not_applicable"
        correct = False
    return {
        "backend": backend,
        "backend_name": BACKENDS[backend][0],
        "source_id": row["source_id"],
        "clip_id": row["clip_id"],
        "clip_length_s": float(row["clip_length_s"]),
        "recording_condition": row["recording_condition"],
        "status": status,
        "error_code": result.get("error_code", ""),
        "correct": correct,
        "identity_method": identity_method,
        "provider_id": provider_id or "",
        "returned_title": str(result.get("title") or ""),
        "returned_artist": str(result.get("artist") or ""),
        "latency_ms": result.get("latency_ms", 0.0),
        "cache_key": cache_key,
        "cache_hit": cache_hit,
        **({"failure_reason": _failure_reason(status, result)} if not correct else {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run reproducible music-recognition evaluation without exposing credentials."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("evaluation/results/benchmark.json"))
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--cache-dir", type=Path, default=Path("evaluation/results/cache"))
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--backend", choices=["all", *BACKENDS], default="all")
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--network-region", default="", help="Operator-supplied region label")
    parser.add_argument("--provider-plan", default="", help="Operator-supplied plan label")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    selected = list(BACKENDS) if args.backend == "all" else [args.backend]
    run(
        args.manifest,
        args.output,
        args.timeout,
        args.env,
        backends=selected,
        cache_dir=args.cache_dir,
        refresh_cache=args.refresh_cache,
        report_path=args.report,
        network_region=args.network_region,
        provider_plan=args.provider_plan,
    )
    print(f"Wrote benchmark results to {args.output}")
    print(f"Wrote benchmark report to {args.report or args.output.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
