import os
from pathlib import Path

import requests

from common.config_loader import get

ERROR_DIR = Path(get("error_screenshot_dir", "error"))
ERROR_NAME = get("error_screenshot_name", "error_screenshot.png")
ONLYFILES_URL = get("onlyfiles_upload_url", "https://api.onlyfiles.com/v1/upload")
ONLYFILES_EXPIRE = get("onlyfiles_expire", 172800)
ONLYFILES_ENABLED = bool(get("onlyfiles_enabled", True))


def capture_error_screenshot(page) -> str:
    """Capture a full-page error screenshot to /error/error_screenshot.png.

    Replaces any existing file with the same name. Returns the saved path, or
    None on failure.
    """
    try:
        ERROR_DIR.mkdir(parents=True, exist_ok=True)
        screenshot_path = ERROR_DIR / ERROR_NAME
        page.screenshot(path=str(screenshot_path), full_page=True)
        print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
        return str(screenshot_path)
    except Exception as e:
        print(f"[WARNING] Could not capture error screenshot: {e}", flush=True)
        return None


def upload_to_onlyfiles(screenshot_path: str) -> str:
    """Upload a screenshot to onlyfiles and return the direct view URL."""
    if not ONLYFILES_ENABLED:
        print("[INFO] onlyfiles upload disabled via config.", flush=True)
        return None

    try:
        path = Path(screenshot_path)
        if not path.exists():
            print(f"[WARNING] Screenshot not found: {screenshot_path}", flush=True)
            return None

        with path.open("rb") as file:
            response = requests.post(
                ONLYFILES_URL,
                files={"file": file},
                data={"expire": str(ONLYFILES_EXPIRE)},
                timeout=60,
            )

        if response.status_code == 200:
            res_data = response.json()
            direct_url = res_data["data"]["file"]["url"]["full"]
            print(f"DIRECT LINK (Expires in 48 Hours): {direct_url}", flush=True)
            return direct_url
        else:
            print(f"[WARNING] Upload Failed: {response.status_code}", flush=True)
            return None
    except Exception as e:
        print(f"[WARNING] Could not upload screenshot: {e}", flush=True)
        return None
