from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
from scipy.ndimage import maximum_filter

from .recorder import AudioClip, load_audio_file

INDEX_VERSION = 1


@dataclass(frozen=True)
class FingerprintConfig:
    """Parameters for a compact constellation-map fingerprint index."""

    sample_rate: int = 44100
    frame_size: int = 4096
    hop_size: int = 512
    min_frequency_hz: int = 300
    max_frequency_hz: int = 5000
    max_peaks_per_frame: int = 5
    target_delta_min_frames: int = 43
    target_delta_max_frames: int = 172
    fanout: int = 5
    offset_bin_frames: int = 4
    min_match_votes: int = 4


DEFAULT_CONFIG = FingerprintConfig()


@dataclass(frozen=True)
class SpectralPeak:
    time_frame: int
    frequency_bin: int
    magnitude: float


def _resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return np.asarray(samples, dtype=np.float32).reshape(-1)
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be greater than zero")

    source = np.asarray(samples, dtype=np.float32).reshape(-1)
    if source.size == 0:
        return source
    target_size = max(1, round(source.size * target_rate / source_rate))
    source_positions = np.linspace(0.0, 1.0, source.size, endpoint=False)
    target_positions = np.linspace(0.0, 1.0, target_size, endpoint=False)
    return np.interp(target_positions, source_positions, source).astype(np.float32)


def extract_peaks(
    samples: np.ndarray,
    sample_rate: int,
    config: FingerprintConfig = DEFAULT_CONFIG,
) -> list[SpectralPeak]:
    """Extract local maxima from a short-time magnitude spectrum."""
    array = np.asarray(samples, dtype=np.float32).reshape(-1)
    if array.size == 0:
        return []
    if sample_rate <= 0:
        raise ValueError("sample_rate must be greater than zero")
    if config.frame_size <= 0 or config.hop_size <= 0:
        raise ValueError("frame_size and hop_size must be greater than zero")

    if array.size < config.frame_size:
        frame_count = 1
    else:
        frame_count = 1 + (array.size - config.frame_size) // config.hop_size
    padded_size = (frame_count - 1) * config.hop_size + config.frame_size
    padded = np.pad(array, (0, max(0, padded_size - array.size)))

    frames = np.lib.stride_tricks.sliding_window_view(padded, config.frame_size)[:: config.hop_size]
    frames = frames[:frame_count]
    window = np.hanning(config.frame_size).astype(np.float32)
    spectrum = np.abs(np.fft.rfft(frames * window, axis=1))
    frequencies = np.fft.rfftfreq(config.frame_size, d=1.0 / sample_rate)
    frequency_mask = (frequencies >= config.min_frequency_hz) & (
        frequencies <= config.max_frequency_hz
    )
    if not np.any(frequency_mask):
        return []

    local_maxima = maximum_filter(spectrum, size=(3, 7), mode="nearest") == spectrum
    noise_floor = np.median(spectrum, axis=1, keepdims=True)
    candidates = local_maxima & (spectrum >= np.maximum(noise_floor * 1.5, 1e-8))
    candidates &= frequency_mask[None, :]

    peaks: list[SpectralPeak] = []
    for time_frame in range(frame_count):
        bins = np.flatnonzero(candidates[time_frame])
        if bins.size == 0:
            continue
        bins = bins[np.argsort(spectrum[time_frame, bins])[::-1]][: config.max_peaks_per_frame]
        peaks.extend(
            SpectralPeak(
                time_frame=time_frame,
                frequency_bin=int(frequency_bin),
                magnitude=float(spectrum[time_frame, frequency_bin]),
            )
            for frequency_bin in bins
        )
    return peaks


def _hash_pair(anchor: SpectralPeak, target: SpectralPeak, delta_frames: int) -> str:
    payload = f"{anchor.frequency_bin}|{target.frequency_bin}|{delta_frames}".encode("ascii")
    return hashlib.sha1(payload).hexdigest()[:16]


def generate_hashes(
    peaks: Iterable[SpectralPeak],
    config: FingerprintConfig = DEFAULT_CONFIG,
) -> list[tuple[str, int]]:
    """Create anchor/target landmark hashes and retain the anchor frame."""
    ordered = sorted(peaks, key=lambda peak: (peak.time_frame, peak.frequency_bin))
    hashes: list[tuple[str, int]] = []
    for anchor_index, anchor in enumerate(ordered):
        targets: list[tuple[int, SpectralPeak]] = []
        for target in ordered[anchor_index + 1 :]:
            delta_frames = target.time_frame - anchor.time_frame
            if delta_frames < config.target_delta_min_frames:
                continue
            if delta_frames > config.target_delta_max_frames:
                break
            targets.append((delta_frames, target))
            if len(targets) >= config.fanout:
                break
        hashes.extend(
            (_hash_pair(anchor, target, delta_frames), anchor.time_frame)
            for delta_frames, target in targets
        )
    return hashes


def fingerprint_audio(
    samples: np.ndarray,
    sample_rate: int,
    config: FingerprintConfig = DEFAULT_CONFIG,
) -> list[tuple[str, int]]:
    normalized = _resample(samples, sample_rate, config.sample_rate)
    peaks = extract_peaks(normalized, config.sample_rate, config)
    return generate_hashes(peaks, config)


def build_index(
    tracks: Iterable[dict[str, str]],
    output_path: str | Path,
    config: FingerprintConfig = DEFAULT_CONFIG,
    track_loader: Callable[[str], AudioClip] | None = None,
) -> Path:
    """Build a JSON fingerprint index from clean source tracks."""
    track_metadata: dict[str, dict[str, str]] = {}
    hash_index: dict[str, list[list[Any]]] = defaultdict(list)
    loader = track_loader or load_audio_file

    for track in tracks:
        track_id = track["track_id"]
        clip = loader(track["audio_path"])
        hashes = fingerprint_audio(clip.samples, clip.sample_rate, config)
        track_metadata[track_id] = {
            "title": track.get("title", ""),
            "artist": track.get("artist", ""),
            "album": track.get("album", ""),
            "genre": track.get("genre", ""),
            "era": track.get("era", ""),
            "audio_path": str(track["audio_path"]),
        }
        for fingerprint_hash, time_frame in hashes:
            hash_index[fingerprint_hash].append([track_id, time_frame])

    output = {
        "version": INDEX_VERSION,
        "config": asdict(config),
        "tracks": track_metadata,
        "hashes": dict(hash_index),
    }
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return path


def _load_index(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("version") != INDEX_VERSION:
        raise ValueError(f"Unsupported fingerprint index version: {data.get('version')}")
    if not isinstance(data.get("config"), dict) or not isinstance(data.get("tracks"), dict):
        raise ValueError("Fingerprint index is missing required sections")
    return data


def match_local_index(
    clip: AudioClip,
    index_path: str | Path,
) -> dict[str, Any]:
    """Match a query clip by hash collisions and time-offset consensus."""
    data = _load_index(index_path)
    config = FingerprintConfig(**data["config"])
    query_hashes = fingerprint_audio(clip.samples, clip.sample_rate, config)
    if not query_hashes:
        return {
            "status": "no_match",
            "result": None,
        }

    votes: Counter[tuple[str, int]] = Counter()
    matched_hashes: Counter[str] = Counter()
    for fingerprint_hash, query_frame in query_hashes:
        for track_id, reference_frame in data.get("hashes", {}).get(fingerprint_hash, []):
            offset = int(reference_frame) - query_frame
            offset_bucket = round(offset / config.offset_bin_frames)
            votes[(track_id, offset_bucket)] += 1
            matched_hashes[track_id] += 1

    if not votes:
        return {
            "status": "no_match",
            "result": None,
            "fingerprint_hashes": len(query_hashes),
            "matched_hashes": 0,
        }

    (track_id, offset_bucket), vote_count = votes.most_common(1)[0]
    if vote_count < config.min_match_votes:
        return {
            "status": "no_match",
            "result": None,
            "fingerprint_hashes": len(query_hashes),
            "matched_hashes": sum(matched_hashes.values()),
            "best_votes": vote_count,
        }

    track = data["tracks"].get(track_id)
    if not track:
        return {"status": "error", "error": f"Index references unknown track: {track_id}"}

    confidence = vote_count / max(1, len(query_hashes))
    return {
        "status": "matched",
        "provider_id": str(track_id),
        "result": track,
        "title": track.get("title", ""),
        "artist": track.get("artist", ""),
        "album": track.get("album", ""),
        "image": None,
        "score": round(confidence, 4),
        "votes": vote_count,
        "fingerprint_hashes": len(query_hashes),
        "matched_hashes": sum(matched_hashes.values()),
        "offset_frames": offset_bucket * config.offset_bin_frames,
    }
