from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import tempfile
import wave

import numpy as np
from scipy.signal import resample_poly

from .config import AppConfig


SUPPORTED_WAV_SAMPLE_WIDTHS = frozenset({1, 2, 4})
DEFAULT_SAMPLE_RATE = 44100
DEFAULT_SAMPLE_WIDTH = 2
DEFAULT_MIN_AUDIO_SECONDS = 1.0
DEFAULT_MAX_AUDIO_SECONDS = 30.0
DEFAULT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class AudioInputError(ValueError):
    """Stable, user-safe validation error for an audio input."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class AudioClip:
    """Normalized mono floating-point audio used by every matcher backend."""

    samples: np.ndarray
    sample_rate: int
    source: str
    path: Path | None = None


def _to_mono_float32(samples: np.ndarray) -> np.ndarray:
    array = np.asarray(samples)
    if array.ndim == 1:
        mono = array
    elif array.ndim == 2:
        if array.shape[1] <= 0:
            raise AudioInputError("empty_audio", "Audio contains no channels.")
        # Audio buffers use frames x channels. Averaging prevents stereo sums
        # from exceeding the normalized range.
        mono = array.mean(axis=1)
    else:
        raise AudioInputError("invalid_shape", "Audio must contain one or two dimensions.")

    if mono.size == 0:
        raise AudioInputError("empty_audio", "Audio contains no samples.")
    try:
        mono = np.asarray(mono, dtype=np.float32).reshape(-1)
    except (TypeError, ValueError) as exc:
        raise AudioInputError("invalid_audio", "Audio samples are not numeric.") from exc
    if not np.all(np.isfinite(mono)):
        raise AudioInputError("invalid_audio", "Audio samples contain invalid values.")
    if np.any(np.abs(mono) > 1.00001):
        raise AudioInputError("invalid_audio", "Audio samples are outside the normalized range.")
    return np.clip(mono, -1.0, 1.0).astype(np.float32, copy=False)


def _resample(samples: np.ndarray, sample_rate: int, target_rate: int) -> np.ndarray:
    if sample_rate == target_rate:
        return samples
    gcd = int(np.gcd(sample_rate, target_rate))
    try:
        converted = resample_poly(samples, target_rate // gcd, sample_rate // gcd)
    except (ValueError, TypeError) as exc:
        raise AudioInputError("invalid_audio", "Audio could not be resampled.") from exc
    return np.asarray(converted, dtype=np.float32)


def normalize_audio(
    samples: np.ndarray,
    sample_rate: int,
    *,
    source: str,
    target_sample_rate: int = DEFAULT_SAMPLE_RATE,
    min_audio_seconds: float = DEFAULT_MIN_AUDIO_SECONDS,
    max_audio_seconds: float = DEFAULT_MAX_AUDIO_SECONDS,
    path: Path | None = None,
) -> AudioClip:
    """Validate, downmix, resample, and bound audio for all input paths."""
    try:
        rate = int(sample_rate)
    except (TypeError, ValueError) as exc:
        raise AudioInputError("invalid_sample_rate", "Audio sample rate is invalid.") from exc
    if rate <= 0:
        raise AudioInputError("invalid_sample_rate", "Audio sample rate must be greater than zero.")
    try:
        target_rate = int(target_sample_rate)
    except (TypeError, ValueError) as exc:
        raise AudioInputError("invalid_sample_rate", "Internal sample rate is invalid.") from exc
    if target_rate <= 0:
        raise AudioInputError("invalid_sample_rate", "Internal sample rate must be greater than zero.") from None

    mono = _to_mono_float32(np.asarray(samples))
    duration = mono.size / rate
    if duration < float(min_audio_seconds):
        raise AudioInputError("too_short", f"Audio must be at least {min_audio_seconds:g} seconds long.")
    if duration > float(max_audio_seconds):
        raise AudioInputError("too_long", f"Audio must be no longer than {max_audio_seconds:g} seconds.")

    normalized = _resample(mono, rate, target_rate)
    if normalized.size == 0:
        raise AudioInputError("empty_audio", "Audio contains no samples.")
    return AudioClip(
        samples=normalized,
        sample_rate=target_rate,
        source=source,
        path=path,
    )


def _pcm_bytes_to_float32(raw_audio: bytes, sample_width: int) -> np.ndarray:
    if sample_width not in SUPPORTED_WAV_SAMPLE_WIDTHS:
        raise AudioInputError(
            "unsupported_sample_width",
            f"Unsupported WAV sample width: {sample_width * 8}-bit PCM.",
        )
    if len(raw_audio) % sample_width:
        raise AudioInputError("malformed_wav", "WAV audio data is truncated.")
    if sample_width == 1:
        data = np.frombuffer(raw_audio, dtype=np.uint8).astype(np.float32)
        return (data - 128.0) / 128.0
    if sample_width == 2:
        data = np.frombuffer(raw_audio, dtype="<i2").astype(np.float32)
        return data / 32768.0
    data = np.frombuffer(raw_audio, dtype="<i4").astype(np.float32)
    return data / 2147483648.0


def load_audio_file(
    file_path: str | Path,
    *,
    config: AppConfig | None = None,
    max_bytes: int | None = None,
) -> AudioClip:
    """Load a WAV file and pass it through the shared normalization path."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")
    if path.suffix.lower() != ".wav":
        raise AudioInputError("unsupported_format", "CLI file mode accepts WAV files only.")

    byte_limit = max_bytes
    if byte_limit is None and config is not None:
        byte_limit = config.max_upload_bytes
    if byte_limit is not None and path.stat().st_size > byte_limit:
        raise AudioInputError("upload_too_large", "Audio file exceeds the upload size limit.")

    try:
        with wave.open(str(path), "rb") as audio_file:
            sample_rate = audio_file.getframerate()
            channels = audio_file.getnchannels()
            sample_width = audio_file.getsampwidth()
            frame_count = audio_file.getnframes()
            raw_audio = audio_file.readframes(frame_count)
    except (wave.Error, EOFError, OSError) as exc:
        raise AudioInputError("malformed_wav", "The WAV header or audio data is malformed.") from exc

    if sample_rate <= 0:
        raise AudioInputError("invalid_sample_rate", "Audio sample rate must be greater than zero.")
    if channels <= 0:
        raise AudioInputError("invalid_audio", "WAV must contain at least one channel.")
    expected_bytes = frame_count * channels * sample_width
    if len(raw_audio) != expected_bytes:
        raise AudioInputError("malformed_wav", "WAV audio data is truncated.")

    samples = _pcm_bytes_to_float32(raw_audio, sample_width)
    if channels > 1:
        try:
            samples = samples.reshape(-1, channels)
        except ValueError as exc:
            raise AudioInputError("malformed_wav", "WAV channel data is malformed.") from exc

    cfg = config or AppConfig(audd_api_token="")
    return normalize_audio(
        samples,
        sample_rate,
        source="file",
        target_sample_rate=cfg.internal_sample_rate,
        min_audio_seconds=cfg.min_audio_seconds,
        max_audio_seconds=cfg.max_audio_seconds,
        path=path,
    )


def record_microphone(
    duration_seconds: int = 8,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    *,
    config: AppConfig | None = None,
) -> AudioClip:
    try:
        import sounddevice as sd
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError("Microphone recording requires the 'sounddevice' package") from exc

    if duration_seconds <= 0:
        raise AudioInputError("invalid_duration", "Recording duration must be greater than zero.")
    if sample_rate <= 0:
        raise AudioInputError("invalid_sample_rate", "Audio sample rate must be greater than zero.")

    frame_count = int(duration_seconds * sample_rate)
    print(f"Recording {duration_seconds} seconds from microphone...")
    audio_data = sd.rec(frame_count, samplerate=sample_rate, channels=1, dtype="float32")
    sd.wait()
    cfg = config or AppConfig(audd_api_token="")
    return normalize_audio(
        audio_data,
        sample_rate,
        source="microphone",
        target_sample_rate=cfg.internal_sample_rate,
        min_audio_seconds=cfg.min_audio_seconds,
        max_audio_seconds=cfg.max_audio_seconds,
    )


def write_wav(clip: AudioClip, path: str | Path, *, sample_width: int = DEFAULT_SAMPLE_WIDTH) -> Path:
    """Write normalized mono PCM without allowing integer overflow."""
    if sample_width not in SUPPORTED_WAV_SAMPLE_WIDTHS:
        raise AudioInputError("unsupported_sample_width", "Unsupported internal sample width.")
    samples = _to_mono_float32(clip.samples)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(int(clip.sample_rate))
        if sample_width == 1:
            payload = np.round(np.clip(samples, -1, 1) * 127.0 + 128.0).astype(np.uint8)
        elif sample_width == 2:
            payload = np.round(np.clip(samples, -1, 1) * 32767.0).astype("<i2")
        else:
            payload = np.round(np.clip(samples, -1, 1) * 2147483647.0).astype("<i4")
        wav_file.writeframes(payload.tobytes())
    return output


@contextmanager
def temporary_wav(clip: AudioClip):
    """Yield a normalized temporary WAV and remove it on every exit path."""
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    handle.close()
    path = Path(handle.name)
    try:
        write_wav(clip, path)
        yield path
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def convert_with_ffmpeg(
    input_path: str | Path,
    output_path: str | Path,
    *,
    sample_rate: int,
    timeout: int,
) -> Path:
    """Convert a documented web format to normalized PCM WAV."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AudioInputError("ffmpeg_unavailable", "FFmpeg is required for this audio format.")
    if timeout <= 0:
        raise AudioInputError("ffmpeg_timeout", "FFmpeg conversion timed out.")
    command = [
        ffmpeg,
        "-nostdin",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-sample_fmt",
        "s16",
        str(output_path),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioInputError("ffmpeg_timeout", "FFmpeg conversion timed out.") from exc
    except OSError as exc:
        raise AudioInputError("ffmpeg_unavailable", "FFmpeg could not be started.") from exc
    if completed.returncode != 0:
        raise AudioInputError("ffmpeg_conversion_failed", "FFmpeg could not decode this audio file.")
    if not Path(output_path).exists() or Path(output_path).stat().st_size == 0:
        raise AudioInputError("ffmpeg_conversion_failed", "FFmpeg produced no audio output.")
    return Path(output_path)
