from __future__ import annotations

from io import BytesIO
from pathlib import Path
import tempfile
from unittest.mock import patch

import pytest

from web import app as web_app
from shazam_project.config import AppConfig
from shazam_project.recorder import AudioInputError

from .test_audio_pipeline import config, valid_samples, write_wav


@pytest.fixture(autouse=True)
def clear_rate_limit_state():
    web_app.last_request_by_ip.clear()
    yield
    web_app.last_request_by_ip.clear()


def _upload(tmp_path, filename="clip.wav"):
    path = write_wav(tmp_path / filename, valid_samples(), sample_rate=8000)
    return {"file": (BytesIO(path.read_bytes()), filename)}


def test_status_documents_shared_audio_contract(monkeypatch):
    cfg = config()
    monkeypatch.setattr(web_app, "load_config", lambda: cfg)
    response = web_app.app.test_client().get("/api/status")
    body = response.get_json()
    assert response.status_code == 200
    assert body["internal_sample_rate"] == 16000
    assert body["internal_sample_width"] == 2
    assert body["min_audio_seconds"] == 1.0
    assert "mp3" in body["supported_formats"]


def test_valid_wav_uses_shared_loader_and_returns_contract(monkeypatch, tmp_path):
    cfg = config()
    monkeypatch.setattr(web_app, "load_config", lambda: cfg)
    monkeypatch.setattr(
        web_app.matcher,
        "match_audio",
        lambda clip, config: {"status": "matched", "title": "Song", "artist": "Artist", "backend": "local"},
    )
    web_app.last_request_by_ip.clear()
    response = web_app.app.test_client().post("/api/match", data=_upload(tmp_path), content_type="multipart/form-data")
    assert response.status_code == 200
    assert response.get_json() == {"backend": "local", "status": "matched", "title": "Song", "artist": "Artist"}


def test_missing_and_malformed_uploads_are_invalid_audio(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "load_config", lambda: config())
    client = web_app.app.test_client()
    missing = client.post("/api/match")
    assert missing.status_code == 400
    assert missing.get_json()["status"] == "invalid_audio"
    assert missing.get_json()["error_code"] == "missing_upload"

    malformed = client.post(
        "/api/match",
        data={"file": (BytesIO(b"not a wav"), "clip.wav")},
        content_type="multipart/form-data",
    )
    assert malformed.status_code == 400
    assert malformed.get_json()["error_code"] == "malformed_wav"


def test_oversized_upload_is_rejected_before_decoding(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "load_config", lambda: config(max_upload_bytes=32))
    response = web_app.app.test_client().post(
        "/api/match", data=_upload(tmp_path), content_type="multipart/form-data"
    )
    assert response.status_code == 413
    assert response.get_json() == {
        "status": "invalid_audio",
        "error_code": "upload_too_large",
        "error": "Audio file exceeds the upload size limit.",
    }


def test_unsupported_web_format_is_rejected(monkeypatch):
    monkeypatch.setattr(web_app, "load_config", lambda: config())
    response = web_app.app.test_client().post(
        "/api/match",
        data={"file": (BytesIO(b"data"), "clip.txt")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert response.get_json()["error_code"] == "unsupported_format"


def test_non_wav_upload_is_converted_by_ffmpeg(monkeypatch, tmp_path):
    cfg = config()
    monkeypatch.setattr(web_app, "load_config", lambda: cfg)
    monkeypatch.setattr(
        web_app,
        "convert_with_ffmpeg",
        lambda source, output, sample_rate, timeout: write_wav(output, valid_samples(), sample_rate=8000),
    )
    monkeypatch.setattr(web_app.matcher, "match_audio", lambda clip, config: {"status": "no_match"})
    response = web_app.app.test_client().post(
        "/api/match", data=_upload(tmp_path, "clip.mp3"), content_type="multipart/form-data"
    )
    assert response.status_code == 200
    assert response.get_json()["status"] == "no_match"


def test_ffmpeg_timeout_and_failure_are_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "load_config", lambda: config())
    for code in ("ffmpeg_timeout", "ffmpeg_conversion_failed"):
        monkeypatch.setattr(
            web_app,
            "convert_with_ffmpeg",
            lambda *args, code=code, **kwargs: (_ for _ in ()).throw(AudioInputError(code, "stable failure")),
        )
        response = web_app.app.test_client().post(
            "/api/match", data=_upload(tmp_path, "clip.mp3"), content_type="multipart/form-data"
        )
        assert response.status_code == 400
        assert response.get_json()["status"] == "invalid_audio"
        assert response.get_json()["error_code"] == code


def test_unexpected_matcher_failure_does_not_expose_details(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "load_config", lambda: config())
    monkeypatch.setattr(web_app.matcher, "match_audio", lambda *args: (_ for _ in ()).throw(RuntimeError("secret C:\\private\\token")))
    response = web_app.app.test_client().post(
        "/api/match", data=_upload(tmp_path), content_type="multipart/form-data"
    )
    body = response.get_json()
    assert response.status_code == 500
    assert body == {"status": "error", "error_code": "internal_error", "error": "Recognition failed."}
    assert "secret" not in response.get_data(as_text=True)
