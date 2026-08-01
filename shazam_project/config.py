from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class AppConfig:
    audd_api_token: str
    acoustid_api_key: str = ""
    fpcalc_path: str | None = None
    audio_seconds: int = 8
    fft_output_path: Path = Path("fft_output.png")
    rapidapi_key: str = ""
    internal_sample_rate: int = 44100
    internal_sample_width: int = 2
    min_audio_seconds: float = 1.0
    max_audio_seconds: float = 30.0
    max_upload_bytes: int = 10 * 1024 * 1024
    ffmpeg_timeout_seconds: int = 15
    fingerprint_index_path: str | None = None


def load_config(env_path: str | Path = ".env") -> AppConfig:
    path = Path(env_path)
    if path.exists():
        load_dotenv(dotenv_path=str(path))

    def _int(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return default

    def _float(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return default

    return AppConfig(
        audd_api_token=os.getenv("AUDD_API_TOKEN", "").strip(),
        acoustid_api_key=os.getenv("ACOUSTID_API_KEY", "").strip(),
        fpcalc_path=os.getenv("FP_CALC_PATH", None),
        rapidapi_key=os.getenv("RAPIDAPI_KEY", "").strip(),
        internal_sample_rate=_int("INTERNAL_SAMPLE_RATE", 44100),
        internal_sample_width=_int("INTERNAL_SAMPLE_WIDTH", 2),
        min_audio_seconds=_float("MIN_AUDIO_SECONDS", 1.0),
        max_audio_seconds=_float("MAX_AUDIO_SECONDS", 30.0),
        max_upload_bytes=_int("MAX_UPLOAD_BYTES", 10 * 1024 * 1024),
        ffmpeg_timeout_seconds=_int("FFMPEG_TIMEOUT_SECONDS", 15),
        fingerprint_index_path=os.getenv("FINGERPRINT_INDEX_PATH") or None,
    )


def missing_configuration(config: AppConfig) -> list[str]:
    missing: list[str] = []
    if not (config.audd_api_token or config.acoustid_api_key or config.rapidapi_key):
        missing.append("AUDD_API_TOKEN or ACOUSTID_API_KEY or RAPIDAPI_KEY")

    if config.acoustid_api_key and config.fpcalc_path:
        try:
            p = Path(config.fpcalc_path)
            if not p.exists():
                missing.append("FP_CALC_PATH (path does not exist)")
        except Exception:
            missing.append("FP_CALC_PATH (invalid)")
    return missing
