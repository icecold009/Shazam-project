from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from shazam_project.fingerprint import build_index, fingerprint_audio, match_local_index
from shazam_project.recorder import AudioClip, load_audio_file


def _write_synthetic_track(path: Path, frequencies: list[int], duration: int = 15) -> None:
    sample_rate = 44100
    samples: list[np.ndarray] = []
    segment_length = sample_rate
    for index in range(duration):
        time_axis = np.arange(segment_length, dtype=np.float32) / sample_rate
        frequency = frequencies[index % len(frequencies)]
        segment = 0.45 * np.sin(2 * np.pi * frequency * time_axis)
        segment += 0.2 * np.sin(2 * np.pi * (frequency * 2) * time_axis)
        samples.append(segment)
    audio = np.concatenate(samples)
    pcm = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_audio_extracts_landmark_hashes(self):
        sample_rate = 44100
        time_axis = np.arange(sample_rate * 4, dtype=np.float32) / sample_rate
        samples = np.sin(2 * np.pi * 440 * time_axis) + 0.4 * np.sin(2 * np.pi * 880 * time_axis)
        hashes = fingerprint_audio(samples, sample_rate)
        self.assertGreater(len(hashes), 0)

    def test_local_index_matches_the_correct_track(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            track_a = root / "track-a.wav"
            track_b = root / "track-b.wav"
            index = root / "index.json"
            _write_synthetic_track(track_a, [440, 554, 659, 784])
            _write_synthetic_track(track_b, [330, 392, 494, 587])

            build_index(
                [
                    {
                        "track_id": "a",
                        "audio_path": str(track_a),
                        "title": "Track A",
                        "artist": "Artist A",
                    },
                    {
                        "track_id": "b",
                        "audio_path": str(track_b),
                        "title": "Track B",
                        "artist": "Artist B",
                    },
                ],
                index,
            )

            result = match_local_index(load_audio_file(track_a), index)

            self.assertEqual(result["status"], "matched")
            self.assertEqual(result["title"], "Track A")
            self.assertEqual(result["artist"], "Artist A")
            self.assertGreater(result["votes"], 0)

    def test_index_builder_accepts_a_source_track_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "index.json"
            track_path = root / "source.mp3"
            track_path.write_bytes(b"source audio")
            clip = AudioClip(
                np.zeros(44100 * 4, dtype=np.float32),
                44100,
                "source",
                track_path,
            )

            build_index(
                [{"track_id": "source", "audio_path": str(track_path)}],
                index,
                track_loader=lambda _path: clip,
            )

            self.assertTrue(index.is_file())


if __name__ == "__main__":
    unittest.main()
