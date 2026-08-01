from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from scipy.ndimage import maximum_filter
from scipy.signal import resample_poly

from .recorder import AudioClip


INDEX_VERSION = 1


@dataclass(frozen=True)
class FingerprintConfig:
    sample_rate: int = 44100
    frame_size: int = 2048
    hop_size: int = 512
    min_frequency_hz: int = 300
    max_frequency_hz: int = 5000
    max_peaks_per_frame: int = 5
    fanout: int = 5
    target_delta_min_frames: int = 2
    target_delta_max_frames: int = 30
    offset_bin_frames: int = 2
    min_match_votes: int = 3


DEFAULT_CONFIG = FingerprintConfig()


def _resample(samples: np.ndarray, sample_rate: int, target_rate: int) -> np.ndarray:
    if sample_rate == target_rate:
        return np.asarray(samples, dtype=np.float32)
    gcd = int(np.gcd(sample_rate, target_rate))
    return np.asarray(
        resample_poly(samples, target_rate // gcd, sample_rate // gcd),
        dtype=np.float32,
    )


def extract_peaks(
    samples: np.ndarray,
    sample_rate: int,
    config: FingerprintConfig = DEFAULT_CONFIG,
) -> list[tuple[int, int]]:
    """Find spectrogram peaks as (time frame, frequency bin) landmarks."""
    array = np.asarray(samples, dtype=np.float32).reshape(-1)
    if array.size == 0 or sample_rate <= 0:
        return []
    frame_count = max(1, int(np.ceil(max(0, array.size - config.frame_size) / config.hop_size)) + 1)
    padded_size = config.frame_size + (frame_count - 1) * config.hop_size
    padded = np.pad(array, (0, max(0, padded_size - array.size)))
    frames = np.lib.stride_tricks.sliding_window_view(padded, config.frame_size)[:: config.hop_size]
    frames = frames[:frame_count]
    window = np.hanning(config.frame_size).astype(np.float32)
    spectrum = np.abs(np.fft.rfft(frames * window, axis=1))
    frequencies = np.fft.rfftfreq(config.frame_size, d=1.0 / sample_rate)
    frequency_mask = (frequencies >= config.min_frequency_hz) & (frequencies <= config.max_frequency_hz)
    if not np.any(frequency_mask):
        return []

    local_maxima = maximum_filter(spectrum, size=(3, 7), mode="nearest") == spectrum
    noise_floor = np.median(spectrum, axis=1, keepdims=True)
    candidates = local_maxima & (spectrum >= np.maximum(noise_floor * 1.5, 1e-8))
    candidates &= frequency_mask[None, :]
    peaks: list[tuple[int, int]] = []
    for time_frame in range(frame_count):
        bins = np.flatnonzero(candidates[time_frame])
        bins = bins[np.argsort(spectrum[time_frame, bins])[::-1]][: config.max_peaks_per_frame]
        peaks.extend((time_frame, int(frequency_bin)) for frequency_bin in bins)
    return peaks


def _hash_pair(anchor: tuple[int, int], target: tuple[int, int], delta_frames: int) -> str:
    payload = f"{anchor[1]}|{target[1]}|{delta_frames}".encode("ascii")
    return hashlib.sha1(payload).hexdigest()[:16]


def generate_hashes(
    peaks: Iterable[tuple[int, int]],
    config: FingerprintConfig = DEFAULT_CONFIG,
) -> list[tuple[str, int]]:
    ordered = sorted(peaks, key=lambda peak: (peak[0], peak[1]))
    hashes: list[tuple[str, int]] = []
    for anchor_index, anchor in enumerate(ordered):
        targets: list[tuple[int, tuple[int, int]]] = []
        for target in ordered[anchor_index + 1 :]:
            delta_frames = target[0] - anchor[0]
            if delta_frames < config.target_delta_min_frames:
                continue
            if delta_frames > config.target_delta_max_frames:
                break
            targets.append((delta_frames, target))
            if len(targets) >= config.fanout:
                break
        hashes.extend((_hash_pair(anchor, target, delta), anchor[0]) for delta, target in targets)
    return hashes


def fingerprint_audio(
    samples: np.ndarray,
    sample_rate: int,
    config: FingerprintConfig = DEFAULT_CONFIG,
) -> list[tuple[str, int]]:
    normalized = _resample(np.asarray(samples, dtype=np.float32).reshape(-1), sample_rate, config.sample_rate)
    return generate_hashes(extract_peaks(normalized, config.sample_rate, config), config)


def build_index(
    tracks: Iterable[dict[str, str]],
    output_path: str | Path,
    config: FingerprintConfig = DEFAULT_CONFIG,
) -> Path:
    """Build a JSON index from clean WAV tracks; source files stay local."""
    from .recorder import load_audio_file

    metadata: dict[str, dict[str, str]] = {}
    hash_index: dict[str, list[list[Any]]] = defaultdict(list)
    for track in tracks:
        track_id = track["track_id"]
        clip = load_audio_file(track["audio_path"])
        metadata[track_id] = {
            key: track.get(key, "")
            for key in ("title", "artist", "album", "genre", "era")
        }
        for fingerprint_hash, frame in fingerprint_audio(clip.samples, clip.sample_rate, config):
            hash_index[fingerprint_hash].append([track_id, frame])
    output = {
        "version": INDEX_VERSION,
        "config": asdict(config),
        "tracks": metadata,
        "hashes": dict(hash_index),
    }
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return path


def match_local_index(clip: AudioClip, index_path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(index_path).read_text(encoding="utf-8"))
    if data.get("version") != INDEX_VERSION:
        raise ValueError("Unsupported fingerprint index version")
    config = FingerprintConfig(**data.get("config", {}))
    hashes = fingerprint_audio(clip.samples, clip.sample_rate, config)
    if not hashes:
        return {"status": "no_match", "error_code": "no_landmarks"}

    votes: Counter[tuple[str, int]] = Counter()
    matched_hashes: Counter[str] = Counter()
    for fingerprint_hash, query_frame in hashes:
        for track_id, reference_frame in data.get("hashes", {}).get(fingerprint_hash, []):
            offset = int(reference_frame) - query_frame
            bucket = round(offset / config.offset_bin_frames)
            votes[(track_id, bucket)] += 1
            matched_hashes[track_id] += 1
    if not votes:
        return {"status": "no_match", "fingerprint_hashes": len(hashes), "matched_hashes": 0}

    (track_id, offset_bucket), vote_count = votes.most_common(1)[0]
    if vote_count < config.min_match_votes:
        return {
            "status": "no_match",
            "fingerprint_hashes": len(hashes),
            "matched_hashes": sum(matched_hashes.values()),
            "best_votes": vote_count,
        }
    track = data.get("tracks", {}).get(track_id)
    if not isinstance(track, dict):
        raise ValueError("Fingerprint index references an unknown track")
    confidence = vote_count / max(1, len(hashes))
    return {
        "status": "matched",
        "title": track.get("title", ""),
        "artist": track.get("artist", ""),
        "album": track.get("album", ""),
        "genre": track.get("genre", ""),
        "score": round(confidence, 4),
        "votes": vote_count,
        "fingerprint_hashes": len(hashes),
        "matched_hashes": sum(matched_hashes.values()),
        "offset_frames": offset_bucket * config.offset_bin_frames,
    }
