from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys
import tempfile
import time

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask, jsonify, render_template, request
from supabase import Client, create_client

from shazam_project import matcher
from shazam_project.config import AppConfig, load_config
from shazam_project.recorder import AudioInputError, convert_with_ffmpeg, load_audio_file


app = Flask(__name__, template_folder="templates", static_folder="static")

SUPPORTED_WEB_FORMATS = ("wav", "mp3", "m4a", "aac", "ogg", "flac", "webm")
SUPPORTED_WEB_SUFFIXES = {f".{extension}" for extension in SUPPORTED_WEB_FORMATS}

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
supabase: Client | None = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "15"))
MONTHLY_LIMIT = int(os.getenv("MONTHLY_LIMIT", "475"))
INTERNAL_API_SECRET = os.getenv("INTERNAL_API_SECRET", "")
COOLDOWN_SECONDS = 30
last_request_by_ip: dict[str, float] = {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _today_key() -> str:
    return "daily:" + _utc_now().strftime("%Y-%m-%d")


def _month_key() -> str:
    return "monthly:" + _utc_now().strftime("%Y-%m")


def _get_count(key: str) -> int:
    if supabase is None:
        return 0
    try:
        response = supabase.table("api_usage").select("call_count").eq("key", key).execute()
        if response.data and isinstance(response.data[0], dict):
            return int(response.data[0].get("call_count", 0))
    except Exception:
        pass
    return 0


def _increment_count(key: str) -> None:
    if supabase is None:
        return
    try:
        supabase.rpc("increment_api_usage", {"p_key": key}).execute()
    except Exception:
        pass


TRUSTED_PROXIES = {"127.0.0.1", "::1"}


def _get_client_ip() -> str:
    if request.remote_addr in TRUSTED_PROXIES:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    return request.remote_addr or "unknown"


def _check_rate_limits(ip: str) -> dict:
    now = time.time()
    last_seen = last_request_by_ip.get(ip)
    if last_seen is not None and COOLDOWN_SECONDS - (now - last_seen) > 0:
        return {
            "blocked": True,
            "status_code": 429,
            "payload": {
                "status": "rate_limited",
                "error_code": "cooldown",
                "error": "Please wait before trying again.",
            },
        }

    today = _today_key()
    month = _month_key()
    if _get_count(today) >= DAILY_LIMIT:
        return {
            "blocked": True,
            "status_code": 429,
            "payload": {"status": "rate_limited", "error_code": "daily_limit", "error": "Daily recognition limit reached."},
        }
    if _get_count(month) >= MONTHLY_LIMIT:
        return {
            "blocked": True,
            "status_code": 429,
            "payload": {"status": "rate_limited", "error_code": "monthly_limit", "error": "Monthly recognition limit reached."},
        }
    return {"blocked": False, "today": today, "month": month, "now": now}


def _record_request(ip: str, today: str, month: str, now: float) -> None:
    last_request_by_ip[ip] = now
    _increment_count(today)
    _increment_count(month)


def _file_size(upload) -> int | None:
    stream = getattr(upload, "stream", None)
    if stream is None or not hasattr(stream, "seek"):
        return None
    try:
        current = stream.tell()
        stream.seek(0, os.SEEK_END)
        size = int(stream.tell())
        stream.seek(current)
        return size
    except (OSError, ValueError):
        return None


def _load_web_upload(upload, config: AppConfig):
    filename = (getattr(upload, "filename", None) or "upload.wav").strip()
    suffix = Path(filename).suffix.lower() or ".wav"
    if suffix not in SUPPORTED_WEB_SUFFIXES:
        raise AudioInputError("unsupported_format", "This web upload format is not supported.")
    size = _file_size(upload)
    if size is not None and size > config.max_upload_bytes:
        raise AudioInputError("upload_too_large", "Audio file exceeds the upload size limit.")

    input_handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    input_handle.close()
    converted_path: Path | None = None
    try:
        upload.save(input_handle.name)
        input_path = Path(input_handle.name)
        if input_path.stat().st_size > config.max_upload_bytes:
            raise AudioInputError("upload_too_large", "Audio file exceeds the upload size limit.")
        wav_path = input_path
        if suffix != ".wav":
            converted_handle = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            converted_handle.close()
            converted_path = Path(converted_handle.name)
            convert_with_ffmpeg(
                input_path,
                converted_path,
                sample_rate=config.internal_sample_rate,
                timeout=config.ffmpeg_timeout_seconds,
            )
            wav_path = converted_path
        return load_audio_file(wav_path, config=config, max_bytes=config.max_upload_bytes)
    finally:
        try:
            Path(input_handle.name).unlink(missing_ok=True)
        except OSError:
            pass
        if converted_path is not None:
            try:
                converted_path.unlink(missing_ok=True)
            except OSError:
                pass


def _audio_error_response(exc: AudioInputError):
    status_code = 413 if exc.code == "upload_too_large" else 400
    return jsonify({"status": "invalid_audio", "error_code": exc.code, "error": exc.message}), status_code


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/match", methods=["POST"])
def api_match():
    if INTERNAL_API_SECRET and request.headers.get("X-API-Secret") != INTERNAL_API_SECRET:
        return jsonify({"status": "error", "error_code": "unauthorized", "error": "Unauthorized."}), 401
    if "file" not in request.files:
        return jsonify({"status": "invalid_audio", "error_code": "missing_upload", "error": "No audio file was uploaded."}), 400

    config = load_config()
    try:
        clip = _load_web_upload(request.files["file"], config)
    except AudioInputError as exc:
        return _audio_error_response(exc)
    except Exception:
        return jsonify({"status": "error", "error_code": "upload_failed", "error": "Audio upload could not be processed."}), 400

    client_ip = _get_client_ip()
    limit_check = _check_rate_limits(client_ip)
    if limit_check["blocked"]:
        return jsonify(limit_check["payload"]), limit_check["status_code"]

    _record_request(client_ip, limit_check["today"], limit_check["month"], limit_check["now"])
    try:
        return jsonify(matcher.match_audio(clip, config))
    except Exception:
        return jsonify({"status": "error", "error_code": "internal_error", "error": "Recognition failed."}), 500


@app.route("/api/status", methods=["GET"])
def api_status():
    config = load_config()
    return jsonify(
        {
            "rapidapi_configured": bool(config.rapidapi_key),
            "acoustid_configured": bool(config.acoustid_api_key),
            "audd_configured": bool(config.audd_api_token),
            "local_index_configured": bool(config.fingerprint_index_path),
            "fpcalc_on_path": bool(config.fpcalc_path or shutil.which("fpcalc")),
            "ffmpeg_on_path": bool(shutil.which("ffmpeg")),
            "supported_formats": list(SUPPORTED_WEB_FORMATS),
            "internal_sample_rate": config.internal_sample_rate,
            "internal_sample_width": config.internal_sample_width,
            "min_audio_seconds": config.min_audio_seconds,
            "max_audio_seconds": config.max_audio_seconds,
            "max_upload_bytes": config.max_upload_bytes,
            "daily_used": _get_count(_today_key()),
            "daily_limit": DAILY_LIMIT,
            "monthly_used": _get_count(_month_key()),
            "monthly_limit": MONTHLY_LIMIT,
            "cooldown_seconds": COOLDOWN_SECONDS,
        }
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=os.getenv("FLASK_DEBUG", "0") == "1")
