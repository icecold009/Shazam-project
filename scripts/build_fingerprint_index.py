from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.evaluation import load_source_manifest, resolve_manifest_path
from scripts.record_benchmark import _decode_source
from shazam_project.fingerprint import build_index
from shazam_project.recorder import AudioClip, normalize_audio


def _load_source_clip(audio_path: str) -> AudioClip:
    path = Path(audio_path)
    samples, sample_rate = _decode_source(path)
    return normalize_audio(
        samples,
        sample_rate,
        source="source",
        target_sample_rate=44100,
        min_audio_seconds=0.0,
        max_audio_seconds=float("inf"),
        path=path,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a local constellation-hash fingerprint index."
    )
    parser.add_argument(
        "--sources",
        type=Path,
        required=True,
        help="CSV with track_id/source_id, source_audio_path, title, artist",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("evaluation/results/fingerprint-index.json")
    )
    args = parser.parse_args()

    rows = load_source_manifest(args.sources, require_audio=True)

    tracks: list[dict[str, str]] = []
    for row in rows:
        audio_path = resolve_manifest_path(args.sources, row["source_audio_path"]).resolve()
        tracks.append(
            {
                "track_id": row.get("track_id") or row.get("source_id", ""),
                "audio_path": str(audio_path),
                "title": row.get("title", ""),
                "artist": row.get("artist", ""),
                "album": row.get("album", ""),
                "genre": row.get("genre", ""),
                "era": row.get("era", ""),
            }
        )

    output = build_index(tracks, args.output, track_loader=_load_source_clip)
    print(f"Indexed {len(tracks)} tracks into {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
