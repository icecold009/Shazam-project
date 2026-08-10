from __future__ import annotations

import sys
import tempfile
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import requests

from shazam_project import matcher
from shazam_project.config import AppConfig
from shazam_project.fingerprint import build_index, fingerprint_audio, match_local_index
from shazam_project.recorder import (
    AudioClip,
    AudioInputError,
    convert_with_ffmpeg,
    load_audio_file,
    normalize_audio,
    record_microphone,
    temporary_wav,
)


def _pcm_bytes(samples: np.ndarray, width: int) -> bytes:
    values = np.asarray(samples, dtype=np.float32)
    if width == 1:
        return np.round(np.clip(values, -1, 1) * 127 + 128).astype(np.uint8).tobytes()
    if width == 2:
        return np.round(np.clip(values, -1, 1) * 32767).astype("<i2").tobytes()
    if width == 4:
        return np.round(np.clip(values, -1, 1) * 2147483647).astype("<i4").tobytes()
    return b"\x00" * (len(values) * width)


def write_wav(path: Path, samples: np.ndarray, sample_rate: int = 8000, width: int = 2) -> Path:
    array = np.asarray(samples, dtype=np.float32)
    channels = 1 if array.ndim == 1 else array.shape[1]
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(_pcm_bytes(array.reshape(-1), width))
    return path


def config(**kwargs) -> AppConfig:
    values = dict(
        audd_api_token="",
        internal_sample_rate=16000,
        min_audio_seconds=1.0,
        max_audio_seconds=5.0,
        max_upload_bytes=10 * 1024 * 1024,
    )
    values.update(kwargs)
    return AppConfig(**values)


def valid_samples(rate: int = 8000, seconds: float = 1.5) -> np.ndarray:
    count = int(rate * seconds)
    t = np.arange(count) / rate
    return (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


@pytest.mark.parametrize("width", [1, 2, 4])
def test_supported_pcm_widths_are_normalized(width, tmp_path):
    path = write_wav(tmp_path / f"valid-{width}.wav", valid_samples(), width=width)
    clip = load_audio_file(path, config=config())
    assert clip.sample_rate == 16000
    assert clip.samples.dtype == np.float32
    assert clip.samples.ndim == 1
    assert np.max(np.abs(clip.samples)) <= 1.0


def test_stereo_is_downmixed_without_shape_or_clipping_errors(tmp_path):
    left = valid_samples()
    stereo = np.column_stack((left, -left))
    path = write_wav(tmp_path / "stereo.wav", stereo)
    clip = load_audio_file(path, config=config())
    assert clip.samples.ndim == 1
    assert clip.samples.shape[0] == int(1.5 * 16000)
    assert np.max(np.abs(clip.samples)) < 0.01


def test_empty_audio_is_rejected(tmp_path):
    path = write_wav(tmp_path / "empty.wav", np.array([], dtype=np.float32))
    with pytest.raises(AudioInputError, match="no samples") as exc_info:
        load_audio_file(path, config=config())
    assert exc_info.value.code == "empty_audio"


def test_malformed_wav_header_is_rejected(tmp_path):
    path = tmp_path / "malformed.wav"
    path.write_bytes(b"not a wav")
    with pytest.raises(AudioInputError) as exc_info:
        load_audio_file(path, config=config())
    assert exc_info.value.code == "malformed_wav"


def test_zero_sample_rate_is_rejected(tmp_path):
    path = write_wav(tmp_path / "zero-rate.wav", valid_samples())
    raw = bytearray(path.read_bytes())
    raw[24:28] = (0).to_bytes(4, "little")
    path.write_bytes(raw)
    with pytest.raises(AudioInputError) as exc_info:
        load_audio_file(path, config=config())
    assert exc_info.value.code == "invalid_sample_rate"


def test_24_bit_pcm_is_rejected(tmp_path):
    path = write_wav(tmp_path / "24-bit.wav", valid_samples(), width=3)
    with pytest.raises(AudioInputError) as exc_info:
        load_audio_file(path, config=config())
    assert exc_info.value.code == "unsupported_sample_width"


def test_duration_and_size_limits_are_stable(tmp_path):
    short_path = write_wav(tmp_path / "short.wav", valid_samples(seconds=0.25))
    with pytest.raises(AudioInputError) as short_error:
        load_audio_file(short_path, config=config())
    assert short_error.value.code == "too_short"

    long_path = write_wav(tmp_path / "long.wav", valid_samples(seconds=5.2))
    with pytest.raises(AudioInputError) as long_error:
        load_audio_file(long_path, config=config())
    assert long_error.value.code == "too_long"

    with pytest.raises(AudioInputError) as size_error:
        load_audio_file(short_path, config=config(max_upload_bytes=8))
    assert size_error.value.code == "upload_too_large"


def test_invalid_shapes_and_rates_are_rejected():
    with pytest.raises(AudioInputError) as shape_error:
        normalize_audio(np.zeros((2, 2, 2)), 8000, source="test")
    assert shape_error.value.code == "invalid_shape"

    with pytest.raises(AudioInputError) as rate_error:
        normalize_audio(np.zeros(100), 0, source="test")
    assert rate_error.value.code == "invalid_sample_rate"

    with pytest.raises(AudioInputError) as nan_error:
        normalize_audio(np.array([np.nan] * 100), 8000, source="test")
    assert nan_error.value.code == "invalid_audio"


def test_temporary_wav_is_removed_on_exception():
    clip = AudioClip(valid_samples(16000), 16000, "test")
    with pytest.raises(RuntimeError):
        with temporary_wav(clip) as path:
            assert path.exists()
            raise RuntimeError("provider failed")
    assert not path.exists()


def test_ffmpeg_timeout_and_failure_are_stable(tmp_path):
    source = tmp_path / "input.mp3"
    output = tmp_path / "output.wav"
    source.write_bytes(b"audio")
    with (
        patch("shazam_project.recorder.shutil.which", return_value="ffmpeg"),
        patch("shazam_project.recorder.subprocess.run", side_effect=subprocess_timeout()),
    ):
        with pytest.raises(AudioInputError) as timeout_error:
            convert_with_ffmpeg(source, output, sample_rate=44100, timeout=1)
    assert timeout_error.value.code == "ffmpeg_timeout"

    failed = MagicMock(returncode=1)
    with (
        patch("shazam_project.recorder.shutil.which", return_value="ffmpeg"),
        patch("shazam_project.recorder.subprocess.run", return_value=failed),
    ):
        with pytest.raises(AudioInputError) as failure_error:
            convert_with_ffmpeg(source, output, sample_rate=44100, timeout=1)
    assert failure_error.value.code == "ffmpeg_conversion_failed"


def test_ffmpeg_conversion_applies_duration_and_size_bounds(tmp_path):
    source = tmp_path / "input.mp3"
    output = tmp_path / "output.wav"
    source.write_bytes(b"audio")

    def fake_run(command, **_kwargs):
        output.write_bytes(b"wav")
        return MagicMock(returncode=0)

    with (
        patch("shazam_project.recorder.shutil.which", return_value="ffmpeg"),
        patch("shazam_project.recorder.subprocess.run", side_effect=fake_run) as run,
    ):
        convert_with_ffmpeg(
            source,
            output,
            sample_rate=44100,
            timeout=1,
            max_duration_seconds=30,
            max_output_bytes=1024,
        )

    command = run.call_args.args[0]
    assert command[command.index("-t") + 1] == "31.0"
    assert command[command.index("-fs") + 1] == "1025"


def subprocess_timeout():
    import subprocess

    return subprocess.TimeoutExpired("ffmpeg", 1)


def test_public_matcher_statuses_and_safe_diagnostics():
    clip = AudioClip(valid_samples(16000), 16000, "test")
    result = matcher.match_audio(clip, config())
    assert result["status"] == "not_configured"
    assert {attempt["status"] for attempt in result["attempts"]} == {"not_configured"}
    assert "result" not in result


def test_matcher_passes_normalized_clip_to_every_provider():
    raw = np.column_stack((valid_samples(rate=8000), -valid_samples(rate=8000)))
    clip = AudioClip(raw, 8000, "unnormalized-stereo")
    captured = {}

    def provider(received_clip, _config, timeout):
        captured["clip"] = received_clip
        captured["timeout"] = timeout
        return {"status": "matched", "title": "Normalized Song"}

    with patch.object(matcher, "match_audio_shazam", side_effect=provider):
        result = matcher.match_audio(clip, config(), timeout=7)

    received = captured["clip"]
    assert result["status"] == "matched"
    assert received.sample_rate == 16000
    assert received.samples.ndim == 1
    assert received.samples.dtype == np.float32
    assert captured["timeout"] == 7


def test_provider_diagnostics_do_not_leak_paths_or_exception_details():
    clip = AudioClip(
        valid_samples(44100), 44100, "provider-test", path=Path(r"C:\private\source.wav")
    )
    cfg = config(rapidapi_key="KEY")
    leaked = r"C:\private\tmp\provider-secret: raw fpcalc stderr"

    with patch.object(matcher, "match_audio_shazam", side_effect=RuntimeError(leaked)):
        response = matcher.match_audio(clip, cfg)

    serialized = str(response)
    assert response["attempts"][0]["status"] == "error"
    assert response["attempts"][0]["error_code"] == "provider_error"
    assert "Recognition provider failed." in serialized
    assert leaked not in serialized
    assert "C:\\private" not in serialized


def test_local_provider_diagnostics_do_not_expose_index_paths():
    clip = AudioClip(valid_samples(16000), 16000, "local")
    cfg = config(fingerprint_index_path="C:\\private\\fingerprints\\index.json")
    local_result = {
        "status": "matched",
        "provider_id": "track-1",
        "result": {"audio_path": "C:\\private\\catalog\\track.wav"},
        "title": "Local Song",
    }

    with patch.object(matcher, "match_local_index", return_value=local_result):
        response = matcher.match_audio_local(clip, cfg)

    assert response["status"] == "matched"
    assert response["provider_id"] == "track-1"
    assert "result" not in response
    assert "C:\\private" not in str(response)


@pytest.mark.parametrize("duration, code", [(0.5, "too_short"), (6, "too_long")])
def test_record_microphone_rejects_configured_duration_limits_before_recording(duration, code):
    sounddevice = MagicMock()
    with patch.dict(sys.modules, {"sounddevice": sounddevice}):
        with pytest.raises(AudioInputError) as exc_info:
            record_microphone(duration, sample_rate=16000, config=config())
    assert exc_info.value.code == code
    sounddevice.rec.assert_not_called()


def test_matcher_fallback_order_continues_after_no_match_and_error():
    clip = AudioClip(valid_samples(16000), 16000, "test")
    cfg = config(rapidapi_key="rapid", acoustid_api_key="acoustic", fingerprint_index_path="index")
    with (
        patch.object(matcher, "match_audio_shazam", return_value={"status": "no_match"}) as rapid,
        patch.object(
            matcher,
            "match_audio_acoustid",
            return_value={"status": "error", "error_code": "timeout"},
        ) as acoustid,
        patch.object(
            matcher, "_match_audio_audd", return_value={"status": "matched", "title": "Song"}
        ) as audd,
    ):
        result = matcher.match_audio(clip, cfg)
    assert result["status"] == "matched"
    assert result["backend"] == "audd"
    assert [item["backend"] for item in result["attempts"]] == ["rapidapi", "acoustid", "audd"]
    rapid.assert_called_once()
    acoustid.assert_called_once()
    audd.assert_called_once()


def test_provider_temp_file_is_removed_on_timeout():
    clip = AudioClip(valid_samples(16000), 16000, "test")
    cfg = config(rapidapi_key="secret")
    captured: list[str] = []
    real_named_tempfile = tempfile.NamedTemporaryFile

    def capture_tempfile(*args, **kwargs):
        handle = real_named_tempfile(*args, **kwargs)
        captured.append(handle.name)
        return handle

    with (
        patch("shazam_project.recorder.tempfile.NamedTemporaryFile", side_effect=capture_tempfile),
        patch("shazam_project.matcher.requests.post", side_effect=requests.Timeout()),
    ):
        response = matcher.match_audio_shazam(clip, cfg)
    assert response["status"] == "error"
    assert captured and all(not Path(path).exists() for path in captured)


def test_local_fingerprint_index_matches_same_clip(tmp_path):
    sample_rate = 44100
    samples = valid_samples(sample_rate, 2.0) + 0.3 * np.sin(
        2 * np.pi * 880 * np.arange(int(sample_rate * 2.0)) / sample_rate
    )
    source = write_wav(tmp_path / "track.wav", samples, sample_rate=sample_rate)
    index = build_index(
        [
            {
                "track_id": "track-1",
                "audio_path": str(source),
                "title": "Test Song",
                "artist": "Tester",
            }
        ],
        tmp_path / "index.json",
    )
    clip = load_audio_file(source)
    result = match_local_index(clip, index)
    assert result["status"] == "matched"
    assert result["provider_id"] == "track-1"
    assert result["title"] == "Test Song"
    assert "audio_path" not in result
    assert fingerprint_audio(clip.samples, clip.sample_rate)
    empty_index = build_index([], tmp_path / "empty-index.json")
    no_match = match_local_index(clip, empty_index)
    assert no_match["status"] == "no_match"
    assert no_match["result"] is None
