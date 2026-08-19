from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.benchmark import BACKENDS, _aggregate, _corpus_shape_reasons, render_markdown

START_MARKER = "<!-- BENCHMARK_RESULTS:START -->"
END_MARKER = "<!-- BENCHMARK_RESULTS:END -->"


def validate_complete_results(results: dict[str, Any]) -> None:
    metadata = results.get("metadata")
    summaries = results.get("backend_summary")
    records = results.get("records")
    clip_count = results.get("clip_count")
    if (
        not isinstance(metadata, dict)
        or not isinstance(summaries, dict)
        or type(clip_count) is not int
        or not isinstance(records, list)
    ):
        raise ValueError("Benchmark results are incomplete: required sections are missing.")
    if clip_count <= 0:
        raise ValueError("Benchmark results are incomplete: clip_count must be greater than zero.")
    if not metadata.get("network_region") or not metadata.get("provider_plan"):
        raise ValueError("Benchmark results are incomplete: operator metadata is missing.")
    if set(summaries) != set(BACKENDS):
        raise ValueError("Benchmark results are incomplete: all backends are required.")
    selected_backends = metadata.get("selected_backends")
    if (
        not isinstance(selected_backends, list)
        or len(selected_backends) != len(BACKENDS)
        or set(selected_backends) != set(BACKENDS)
    ):
        raise ValueError("Benchmark results are incomplete: all backends are required.")
    if metadata.get("incomplete_reasons") != []:
        raise ValueError("Benchmark results are incomplete; README was not changed.")
    if metadata.get("complete") is not True:
        raise ValueError("Benchmark results are incomplete; README was not changed.")
    if type(metadata.get("missing_clip_count", 0)) is not int:
        raise ValueError("Benchmark results are incomplete: missing clip count is invalid.")
    if metadata.get("missing_clip_count", 0) != 0:
        raise ValueError("Benchmark results are incomplete: clips are missing.")
    if len(records) != clip_count * len(BACKENDS) or any(
        not isinstance(record, dict) for record in records
    ):
        raise ValueError("Benchmark results are incomplete: record count is inconsistent.")
    corpus_reasons = _corpus_shape_reasons(records, list(BACKENDS))
    if corpus_reasons:
        raise ValueError("Benchmark results are incomplete: " + "; ".join(corpus_reasons) + ".")
    if any(record.get("status") == "invalid_audio" for record in records):
        raise ValueError("Benchmark results are incomplete: unusable audio inputs are present.")

    expected_clip_ids: set[str] | None = None
    for backend in BACKENDS:
        summary = summaries[backend]
        if not isinstance(summary, dict):
            raise ValueError(f"Benchmark results are incomplete for {backend}; summary is missing.")
        count_fields = (
            "total_clips",
            "attempted",
            "correct",
            "accuracy_numerator",
            "accuracy_denominator",
            "not_configured",
            "missing_inputs",
        )
        if any(type(summary.get(field)) is not int for field in count_fields):
            raise ValueError(f"Benchmark results are incomplete for {backend}; counts are invalid.")
        if any(summary[field] < 0 for field in count_fields):
            raise ValueError(f"Benchmark results are incomplete for {backend}; counts are invalid.")
        backend_records = [record for record in records if record.get("backend") == backend]
        clip_ids = {record.get("clip_id") for record in backend_records}
        if (
            len(backend_records) != clip_count
            or len(clip_ids) != clip_count
            or any(not isinstance(clip_id, str) or not clip_id for clip_id in clip_ids)
        ):
            raise ValueError(
                f"Benchmark results are incomplete for {backend}; record count is inconsistent."
            )
        if expected_clip_ids is None:
            expected_clip_ids = clip_ids
        elif clip_ids != expected_clip_ids:
            raise ValueError(
                f"Benchmark results are incomplete for {backend}; clip records do not match."
            )
        if any(record.get("error_code") == "missing_clip" for record in backend_records):
            raise ValueError(f"Benchmark results are incomplete for {backend}; clips are missing.")
        if summary.get("total_clips") != clip_count:
            raise ValueError(
                f"Benchmark results are incomplete for {backend}; total clip count is inconsistent."
            )
        if summary.get("not_configured") or summary.get("missing_inputs"):
            raise ValueError(
                f"Benchmark results are incomplete for {backend}; clips or credentials are missing."
            )
        if summary.get("attempted") != clip_count:
            raise ValueError(
                f"Benchmark results are incomplete for {backend}; attempted count is inconsistent."
            )
        if summary.get("accuracy_denominator") != summary.get("attempted"):
            raise ValueError(
                f"Benchmark results are incomplete for {backend}; accuracy denominator is inconsistent."
            )
        if summary.get("accuracy_numerator") != summary.get("correct"):
            raise ValueError(
                f"Benchmark results are incomplete for {backend}; accuracy numerator is inconsistent."
            )
        if summary["correct"] > summary["attempted"]:
            raise ValueError(
                f"Benchmark results are incomplete for {backend}; accuracy counts are inconsistent."
            )
        try:
            calculated = _aggregate(records, backend)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Benchmark results are incomplete for {backend}; records are invalid."
            ) from exc
        if summary != calculated:
            raise ValueError(
                f"Benchmark results are incomplete for {backend}; summary does not match records."
            )


def update_readme(readme_path: Path, results: dict[str, Any]) -> None:
    validate_complete_results(results)
    text = readme_path.read_text(encoding="utf-8")
    if START_MARKER not in text or END_MARKER not in text:
        raise ValueError("README benchmark markers are missing; README was not changed.")
    start = text.index(START_MARKER) + len(START_MARKER)
    end = text.index(END_MARKER, start)
    replacement = "\n\n" + render_markdown(results).rstrip() + "\n\n"
    readme_path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import complete benchmark results into README.md."
    )
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    args = parser.parse_args()
    try:
        results = json.loads(args.results.read_text(encoding="utf-8"))
        update_readme(args.readme, results)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Updated benchmark results in {args.readme}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
