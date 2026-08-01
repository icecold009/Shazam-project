from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image
import requests


def show_result(result: dict[str, Any], open_image: bool = True) -> None:
    status = result.get("status")
    if status == "not_configured":
        print("Recognition is not configured: add a provider credential or local fingerprint index.")
        return
    if status == "invalid_audio":
        print(f"Invalid audio [{result.get('error_code', 'invalid_audio')}]: {result.get('error', 'Audio was rejected.')}")
        return
    if status == "rate_limited":
        print(result.get("error", "Recognition is temporarily rate limited."))
        return
    if status == "error":
        print(f"Recognition error [{result.get('error_code', 'error')}]: {result.get('error', 'Recognition failed.')}")
        return
    if status == "no_match":
        print("No match found for the provided audio.")
        return

    title = result.get("title") or "(unknown)"
    artist = result.get("artist") or "(unknown)"
    album = result.get("album") or "(unknown)"
    print("")
    print(f"Song:   {title}")
    print(f"Artist: {artist}")
    print(f"Album:  {album}")

    image_url = result.get("image")
    if image_url and open_image:
        try:
            response = requests.get(image_url, timeout=10)
            response.raise_for_status()
            Image.open(BytesIO(response.content)).show()
        except Exception:
            print("Album art could not be loaded.")
