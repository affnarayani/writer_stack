import os
import sys
import json
import time
import base64
import random
import re
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv
import requests

from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from common.config_loader import get
from common.crypto import load_cookies, get_env_decrypt_key
from common.locator_loader import resolve_locator, gemini_vision_pass, apply_auto_correct
from common.screenshot import capture_error_screenshot, upload_to_onlyfiles

# =========================
# ENV
# =========================
load_dotenv()

get_env_decrypt_key()

# =========================
# CONFIG (from json/config.json)
# =========================
HEADLESS = get("headless", True)
USER_AGENT = get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
SUBSTACK_URL = get("substack_url", "https://substack.com/")
PBKDF2_ITERATIONS = get("pbkdf2_iterations", 200000)
MIN_RANDOM_WAIT = get("min_random_wait", 15)
MAX_RANDOM_WAIT = get("max_random_wait", 30)
MIN_RANDOM_WAIT_SHORT = get("min_random_wait_short", 3)
MAX_RANDOM_WAIT_SHORT = get("max_random_wait_short", 5)

STACK_COOKIES_FILE = Path(get("stack_cookies_file", "substack_cookies/stack_cookies.json.encrypted"))

PLATFORM = "substack"


# =========================
# DYNAMIC WAITS
# =========================
def custom_random_wait(min_sec=6, max_sec=12):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)


def exit_delay_wait():
    seconds = random.uniform(30, 60)
    print(f"[WAIT] Browser closing delay: Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)


# =========================
# CRYPTO
# =========================
def _derive_key(password: bytes, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password)


def _decrypt_payload(payload: Dict[str, Any], password: str) -> bytes:
    salt = base64.b64decode(payload["s"])
    nonce = base64.b64decode(payload["n"])
    ciphertext = base64.b64decode(payload["ct"])

    key = _derive_key(password.encode("utf-8"), salt)
    aesgcm = AESGCM(key)

    try:
        return aesgcm.decrypt(nonce, ciphertext, None)
    except InvalidTag:
        raise RuntimeError("Decryption failed (InvalidTag)")


def load_cookies_local(file_path: Path) -> List[Dict[str, Any]]:
    print("[STEP] Loading cookies...", flush=True)

    with file_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    decrypt_key = os.getenv("DECRYPT_KEY")
    if not decrypt_key:
        raise RuntimeError("DECRYPT_KEY missing")

    plaintext = _decrypt_payload(payload, decrypt_key)
    cookies = json.loads(plaintext.decode("utf-8"))

    for c in cookies:
        if "partitionKey" in c and isinstance(c["partitionKey"], dict):
            if "topLevelSite" in c["partitionKey"]:
                c["partitionKey"] = str(c["partitionKey"]["topLevelSite"])
            else:
                del c["partitionKey"]

        if "sameSite" in c:
            val = str(c["sameSite"]).lower()

            if val in ["no_restriction", "none", "unspecified", "null"]:
                c["sameSite"] = "None"
            elif val == "lax":
                c["sameSite"] = "Lax"
            elif val == "strict":
                c["sameSite"] = "Strict"
            else:
                c["sameSite"] = "Lax"

    print("[OK] Cookies loaded", flush=True)
    return cookies


def upload_to_onlyfiles_local(screenshot_path):
    url = "https://api.onlyfiles.com/v1/upload"

    with open(screenshot_path, "rb") as file:
        response = requests.post(url, files={"file": file}, data={"expire": 172800})

    if response.status_code == 200:
        res_data = response.json()
        direct_url = res_data["data"]["file"]["url"]["full"]
        print(f"DIRECT LINK (Expires in 48 Hours): {direct_url}")
        return direct_url
    else:
        print(f"[WARNING] Upload Failed: {response.status_code}")
        return None


# =========================
# MAIN
# =========================
def run():
    apply_auto_correct()

    print("[START] Script started", flush=True)

    # ===================================================
    # 1. ARTICLE & NOTE PRE-CHECKS
    # ===================================================
    article_path = Path("json/article.json")
    if not article_path.exists():
        print(f"[ERROR] {article_path.name} not found. Exiting.", flush=True)
        sys.exit(0)

    try:
        with article_path.open("r", encoding="utf-8") as f:
            article_data = json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to read {article_path.name}: {e}", flush=True)
        sys.exit(1)

    if article_data.get("posted") != True:
        print("The article has not been posted yet.", flush=True)
        sys.exit(0)

    notes_path = Path("json/notes.json")
    if not notes_path.exists():
        print(f"[ERROR] {notes_path.name} not found. Exiting.", flush=True)
        sys.exit(1)

    try:
        with notes_path.open("r", encoding="utf-8") as f:
            notes_data = json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to read {notes_path.name}: {e}", flush=True)
        sys.exit(1)

    target_note_key = None
    target_note_text = None

    idx = 1
    while True:
        note_key = f"note{idx}"
        status_key = f"{note_key}_posted"

        if note_key not in notes_data:
            break

        if notes_data.get(status_key) is False:
            target_note_key = note_key
            target_note_text = notes_data[note_key]
            break
        idx += 1

    if not target_note_key:
        print("[INFO] All notes have already been posted. Exiting.", flush=True)
        sys.exit(0)

    print(f"[INFO] Target unposted note identified: {target_note_key}", flush=True)

    cookies = load_cookies_local(Path(STACK_COOKIES_FILE))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    # =========================
    # STEALTH SETUP & LOGIN
    # =========================
    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

    try:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled"
            ]
        )

        context = browser.new_context(
            no_viewport=True,
            user_agent=USER_AGENT
        )

        context.grant_permissions(["clipboard-read", "clipboard-write"])
        print("[STEP] Adding cookies to browser context...", flush=True)
        context.add_cookies(cookies)

        page = context.new_page()
        print("[OK] Cookies added successfully", flush=True)

        print("[STEP] Opening Substack URL...", flush=True)
        page.goto(SUBSTACK_URL, wait_until="load")
        print("[OK] Substack URL opened completely (Logged In)", flush=True)
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        # ===================================================
        # NOTE CREATION, SIMULATED TYPING & POST FLOW
        # ===================================================
        print("[STEP] Clicking 'Create' button to open dropdown...", flush=True)
        create_selector = resolve_locator(page, PLATFORM, "substack_create_button", "Create button")
        if create_selector:
            create_selector.first.click()
        custom_random_wait(2, 4)

        print("[STEP] Clicking 'Note' menuitem from the dropdown...", flush=True)
        note_menuitem = resolve_locator(page, PLATFORM, "substack_note_menuitem", "Note menuitem")
        if note_menuitem:
            note_menuitem.first.click()

        # Pop up modal wait and focus
        modal = resolve_locator(page, PLATFORM, "substack_note_modal", "Note modal")
        if modal:
            modal.first.wait_for(state="visible")

        # Note text input inside the modal
        target_input = modal.locator('div').filter(has_text=re.compile(r"^Drop file here to upload$")).first
        target_input.click()
        custom_random_wait(1, 3)

        # Human-like typing configuration splitting new lines
        print(f"[STEP] Processing simulation typing for {target_note_key}...", flush=True)
        lines = target_note_text.split('\n')
        for i, line in enumerate(lines):
            for char in line:
                target_input.type(char)
                time.sleep(random.uniform(0.02, 0.07))

            if i < len(lines) - 1:
                target_input.press("Enter")
                time.sleep(random.uniform(0.2, 0.5))

        # Wait for random 15-30 seconds before final post
        wait_before_post = random.uniform(15, 30)
        print(f"[WAIT] Sleeping for {wait_before_post:.2f} seconds before hitting post button...", flush=True)
        time.sleep(wait_before_post)

        print("[STEP] Clicking on Post button...", flush=True)
        for i in range(9):
            page.keyboard.press('Tab')
            time.sleep(random.uniform(3, 6))
            print(f"[INFO] Pressed TAB {i+1}/9", flush=True)
        print("[STEP] Pressing Enter to post...", flush=True)
        page.keyboard.press('Enter')
        print("[OK] Note successfully shared on Substack!", flush=True)
        wait_after_post = random.uniform(60, 120)
        print(f"[WAIT] Sleeping for {wait_after_post:.2f} seconds after hitting post button...", flush=True)
        time.sleep(wait_after_post)

        # local tracking file updates
        notes_data[f"{target_note_key}_posted"] = True

        any_false_remains = False
        chk_idx = 1
        while True:
            chk_note = f"note{chk_idx}"
            chk_status = f"{chk_note}_posted"
            if chk_note not in notes_data:
                break
            if notes_data[chk_status] is False:
                any_false_remains = True
                break
            chk_idx += 1

        with notes_path.open("w", encoding="utf-8") as f:
            json.dump(notes_data, f, indent=4, ensure_ascii=False)
        print("[OK] Track record updated inside json/notes.json", flush=True)

        if not any_false_remains:
            print("[INFO] No unposted notes left. Clearing 'posted' key from json/article.json...", flush=True)
            if "posted" in article_data:
                del article_data["posted"]

            with article_path.open("w", encoding="utf-8") as f:
                json.dump(article_data, f, indent=4, ensure_ascii=False)
            print("[OK] Cleaned and saved json/article.json perfectly without syntax anomalies", flush=True)

        print("[STEP] Initiating final wait before closing...", flush=True)
        exit_delay_wait()

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR] Automation cycle broke due to runtime trace:", e, flush=True)
        if 'page' in locals() and page:
            try:
                screenshot_path = capture_error_screenshot(page)
                if screenshot_path:
                    upload_to_onlyfiles_local(screenshot_path)
            except Exception as screenshot_err:
                print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
        sys.exit(1)

    finally:
        try:
            browser.close()
        except:
            pass

        try:
            pw_cm.__exit__(None, None, None)
        except:
            pass

        print("[DONE] Script execution phase closed. Terminating process context cleanly.", flush=True)


if __name__ == "__main__":
    run()
