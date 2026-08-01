from __future__ import annotations

import base64
import json
import logging
import shutil
import subprocess
from typing import Any, Callable

import requests

from .config import AppConfig
from .fingerprint import match_local_index
from .recorder import AudioClip, AudioInputError, temporary_wav, normalize_audio


AUDD_ENDPOINT = "https://api.audd.io/"
ACOUSTID_ENDPOINT = "https://api.acoustid.org/v2/lookup"
RAPIDAPI_ENDPOINT = "https://shazam.p.rapidapi.com/songs/detect"
PUBLIC_STATUSES = frozenset(
    {"matched", "no_match", "not_configured", "invalid_audio", "rate_limited", "error"}
)

_ERROR_MESSAGES = {
    "timeout": "Recognition provider timed out.",
    "http_error": "Recognition provider returned an HTTP error.",
    "malformed_response": "Recognition provider returned an invalid response.",
    "provider_error": "Recognition provider failed.",
    "configuration_error": "Recognition provider is not configured correctly.",
    "fpcalc_error": "Acoustic fingerprint generation failed.",
    "fpcalc_output_error": "Acoustic fingerprint generation returned no fingerprint.",
    "request_error": "Recognition provider request failed.",
    "local_match_error": "Local fingerprint matching failed.",
}


def _error_response(error_code: str, status: str = "error") -> dict[str, Any]:
    return {
        "status": status,
        "error_code": error_code,
        "error": _ERROR_MESSAGES.get(error_code, "Recognition failed."),
    }


def _not_configured(backend: str) -> dict[str, Any]:
    return {
        "status": "not_configured",
        "error_code": f"{backend}_not_configured",
        "error": f"{backend} is not configured.",
    }


def _rate_limited() -> dict[str, Any]:
    return {"status": "rate_limited", "error_code": "provider_rate_limited", "error": "Recognition provider rate limit reached."}


def _attempt_summary(backend: str, response: dict[str, Any]) -> dict[str, Any]:
    summary = {"backend": backend, "status": response.get("status", "error")}
    if response.get("error_code"):
        summary["error_code"] = response["error_code"]
    if response.get("error"):
        summary["error"] = response["error"]
    return summary


def _normalize_public_match(response: dict[str, Any]) -> dict[str, Any]:
    """Drop raw provider payloads and local paths from the public contract."""
    allowed = {
        "status", "error_code", "error", "title", "artist", "album", "genre", "image",
        "score", "votes", "fingerprint_hashes", "matched_hashes", "offset_frames", "backend",
        "attempts",
    }
    return {key: value for key, value in response.items() if key in allowed and value is not None}


def _provider_candidates(config: AppConfig) -> list[tuple[str, Callable[..., dict[str, Any]]]]:
    return [
        ("rapidapi", match_audio_shazam),
        ("acoustid", match_audio_acoustid),
        ("audd", _match_audio_audd),
        ("local", match_audio_local),
    ]


def match_audio(clip: AudioClip, config: AppConfig, timeout: int = 15) -> dict[str, Any]:
    """Normalize once, then try RapidAPI, AcoustID, AudD, and local index."""
    try:
        normalized = normalize_audio(
            clip.samples,
            clip.sample_rate,
            source=clip.source,
            target_sample_rate=config.internal_sample_rate,
            min_audio_seconds=config.min_audio_seconds,
            max_audio_seconds=config.max_audio_seconds,
            path=clip.path,
        )
    except AudioInputError as exc:
        return {"status": "invalid_audio", "error_code": exc.code, "error": exc.message}

    attempts: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    for backend, provider in _provider_candidates(config):
        try:
            response = dict(provider(normalized, config, timeout=timeout))
        except AudioInputError as exc:
            response = {"status": "invalid_audio", "error_code": exc.code, "error": exc.message}
        except Exception:
            logging.exception("Unexpected %s matcher failure", backend)
            response = _error_response("provider_error")
        response["backend"] = backend
        response = _normalize_public_match(response)
        attempts.append(_attempt_summary(backend, response))
        responses.append(response)
        if response.get("status") == "matched":
            response["attempts"] = attempts
            return response

    statuses = {item.get("status") for item in responses}
    if "invalid_audio" in statuses:
        final_status = next(item for item in responses if item.get("status") == "invalid_audio")
    elif "no_match" in statuses:
        final_status = {"status": "no_match"}
    elif "rate_limited" in statuses:
        final_status = {"status": "rate_limited", "error": "Recognition provider rate limit reached."}
    elif "error" in statuses:
        final_status = {"status": "error", "error_code": "all_providers_failed", "error": "All recognition providers failed."}
    else:
        final_status = {"status": "not_configured", "error": "No recognition provider is configured."}
    final_status["attempts"] = attempts
    return final_status


def _match_audio_audd(clip: AudioClip, config: AppConfig, timeout: int = 15) -> dict[str, Any]:
    if not config.audd_api_token:
        return _not_configured("audd")
    with temporary_wav(clip) as path:
        try:
            with path.open("rb") as audio_file:
                response = requests.post(
                    AUDD_ENDPOINT,
                    files={"file": audio_file},
                    data={"api_token": config.audd_api_token},
                    timeout=timeout,
                )
            if response.status_code == 429:
                return _rate_limited()
            if response.status_code != 200:
                return _error_response("http_error")
            body = response.json()
            result = body.get("result")
            if not isinstance(result, dict):
                return {"status": "no_match"}
            return {
                "status": "matched",
                "title": result.get("title") or result.get("song") or "",
                "artist": result.get("artist") or "",
                "album": result.get("album") or "",
                "image": result.get("album_cover"),
            }
        except requests.Timeout:
            return _error_response("timeout")
        except (requests.RequestException, OSError):
            return _error_response("request_error")
        except (TypeError, ValueError, json.JSONDecodeError):
            return _error_response("malformed_response")
        except Exception:
            logging.exception("Unexpected AudD response failure")
            return _error_response("provider_error")


def match_audio_acoustid(clip: AudioClip, config: AppConfig, timeout: int = 15) -> dict[str, Any]:
    """Generate a Chromaprint fingerprint and query AcoustID."""
    if not config.acoustid_api_key:
        return _not_configured("acoustid")
    fpcalc = config.fpcalc_path or shutil.which("fpcalc")
    if not fpcalc:
        return _not_configured("acoustid")
    with temporary_wav(clip) as path:
        try:
            process = subprocess.run(
                [fpcalc, str(path)], capture_output=True, text=True, timeout=timeout, check=False
            )
            if process.returncode != 0:
                return _error_response("fpcalc_error")
            fingerprint = None
            duration = None
            for line in process.stdout.splitlines():
                if line.startswith("FINGERPRINT="):
                    fingerprint = line.split("=", 1)[1].strip()
                elif line.startswith("DURATION="):
                    duration = line.split("=", 1)[1].strip()
            if not fingerprint or not duration:
                try:
                    parsed = json.loads(process.stdout)
                    fingerprint = fingerprint or parsed.get("fingerprint")
                    duration = duration or str(parsed.get("duration"))
                except (TypeError, ValueError):
                    pass
            if not fingerprint or not duration:
                return _error_response("fpcalc_output_error")
            response = requests.get(
                ACOUSTID_ENDPOINT,
                params={
                    "client": config.acoustid_api_key,
                    "fingerprint": fingerprint,
                    "duration": duration,
                    "format": "json",
                    "meta": "recordings+releasegroups+artists",
                },
                timeout=timeout,
            )
            if response.status_code == 429:
                return _rate_limited()
            if response.status_code != 200:
                return _error_response("http_error")
            body = response.json()
            results = body.get("results") or []
            if not results:
                return {"status": "no_match"}
            best = results[0]
            recordings = best.get("recordings") or []
            recording = recordings[0] if recordings else {}
            artists = recording.get("artists") or []
            releases = recording.get("releasegroups") or []
            return {
                "status": "matched",
                "title": recording.get("title", ""),
                "artist": ", ".join(item.get("name", "") for item in artists if item.get("name")),
                "album": releases[0].get("title", "") if releases else "",
            }
        except subprocess.TimeoutExpired:
            return _error_response("timeout")
        except requests.Timeout:
            return _error_response("timeout")
        except requests.RequestException:
            return _error_response("request_error")
        except (TypeError, ValueError, json.JSONDecodeError):
            return _error_response("malformed_response")
        except Exception:
            logging.exception("Unexpected AcoustID matcher failure")
            return _error_response("provider_error")


def match_audio_shazam(clip: AudioClip, config: AppConfig, timeout: int = 15) -> dict[str, Any]:
    if not config.rapidapi_key:
        return _not_configured("rapidapi")
    with temporary_wav(clip) as path:
        try:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            response = requests.post(
                RAPIDAPI_ENDPOINT,
                headers={
                    "content-type": "text/plain",
                    "X-RapidAPI-Key": config.rapidapi_key,
                    "X-RapidAPI-Host": "shazam.p.rapidapi.com",
                },
                data=encoded,
                timeout=timeout,
            )
            if response.status_code == 429:
                return _rate_limited()
            if response.status_code != 200:
                return _error_response("http_error")
            body = response.json()
            track = body.get("track")
            if not isinstance(track, dict):
                return {"status": "no_match"}
            images = track.get("images") or {}
            album = ""
            for section in track.get("sections") or []:
                if section.get("type") == "SONG":
                    for metadata in section.get("metadata") or []:
                        if metadata.get("title") == "Album":
                            album = metadata.get("text", "")
            return {
                "status": "matched",
                "title": track.get("title", ""),
                "artist": track.get("subtitle", ""),
                "album": album,
                "image": images.get("coverarthq") or images.get("coverart"),
            }
        except requests.Timeout:
            return _error_response("timeout")
        except requests.RequestException:
            return _error_response("request_error")
        except (TypeError, ValueError, json.JSONDecodeError):
            return _error_response("malformed_response")
        except Exception:
            logging.exception("Unexpected RapidAPI matcher failure")
            return _error_response("provider_error")


def match_audio_local(clip: AudioClip, config: AppConfig, timeout: int = 15) -> dict[str, Any]:
    del timeout
    if not config.fingerprint_index_path:
        return _not_configured("local")
    try:
        return match_local_index(clip, config.fingerprint_index_path)
    except Exception:
        logging.exception("Unexpected local matcher failure")
        return _error_response("local_match_error")
