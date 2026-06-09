import os
import sys
import json
import time
import base64
import random
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv

from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth


# =========================
# CONFIG
# =========================
HEADLESS = True

STACK_COOKIES_FILE = "stack_cookies.json.encrypted"
ARTICLE_FILE = "article.json"
IMAGE_PATH = "image/pin.png"

PBKDF2_ITERATIONS = 200_000

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


# =========================
# DYNAMIC WAITS
# =========================
def custom_random_wait(min_sec=6, max_sec=12):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)


def intermediate_publish_wait():
    seconds = random.uniform(15, 30)
    print(f"[WAIT] Intermediate publishing phase delay: Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)


def keyword_short_wait():
    seconds = random.uniform(3, 6)
    print(f"[WAIT] Keyword input delay: Sleeping for {seconds:.2f} seconds...", flush=True)
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
        raise RuntimeError("❌ Decryption failed (InvalidTag)")


def load_cookies(file_path: Path) -> List[Dict[str, Any]]:
    print("[STEP] Loading cookies...", flush=True)

    with file_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    plaintext = _decrypt_payload(payload, DECRYPT_KEY)
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


# =========================
# DATA LOADER & UPDATER
# =========================
def load_article_data(file_path: str) -> Dict[str, Any]:
    print(f"[STEP] Reading article content from {file_path}...", flush=True)
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def mark_article_as_posted(file_path: str):
    print(f"[STEP] Updating {file_path} with status 'posted': true...", flush=True)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        if "posted" in data:
            del data["posted"]
            
        data["posted"] = True
        
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        print(f"[SUCCESS] {file_path} updated successfully. Status logged.", flush=True)
    except Exception as e:
        print(f"[WARNING] Failed to write status update to file: {e}", flush=True)


# =========================
# MAIN
# =========================
def run():
    print("[START] Script started", flush=True)

    if not os.path.exists(IMAGE_PATH):
        print(f"[ERROR] Required image file not found at: {IMAGE_PATH}. Exiting process.", flush=True)
        sys.exit(1)

    cookies = load_cookies(Path(STACK_COOKIES_FILE))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    article_data = load_article_data(ARTICLE_FILE)
    if article_data.get("posted") is True:
        print("[INFO] Article already posted!", flush=True)
        sys.exit(0)
    chosen_keywords = article_data.get("keywords", [])

    content_keys = [k for k in article_data.keys() if k not in ["title", "subtitle", "keywords", "posted"]]

    # =========================
    # STEALTH SETUP & LOGIN
    # =========================
    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

    browser = None
    page = None

    try:
        # 🟢 NEW: Clean standard launch mechanism without local profile path storage
        browser = pw.chromium.launch(
            headless=HEADLESS,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled"
            ]
        )

        # 🟢 NEW: Direct Context instantiation matching your standard profile architecture
        context = browser.new_context(
            no_viewport=True,
            user_agent=USER_AGENT
        )

        context.grant_permissions(["clipboard-read", "clipboard-write"])
        print("[STEP] Adding cookies to browser context...", flush=True)
        context.add_cookies(cookies)

        page = context.new_page()
        print("[OK] Cookies added successfully", flush=True)

        print("[STEP] Opening Substack URL to check login...", flush=True)
        page.goto("https://substack.com/", wait_until="load")
        custom_random_wait(30, 60)

        # 1. Login Verification via Profile Button
        print("[STEP] Checking if Profile button exists...", flush=True)
        profile_btn = page.get_by_role('button', name='Profile').or_(page.locator("a[aria-label='Profile']"))
        profile_btn.wait_for(state="visible", timeout=60000)
        print("[OK] Profile button found! Login Successful.", flush=True)
        custom_random_wait(3, 6)

        

        print("[SUCCESS] All dynamic text contents appended successfully.", flush=True)


    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR] Script execution broke down due to trace:", e, flush=True)
        if page is not None:
            try:
                page.screenshot(path="error_screenshot.png", full_page=True)
                print("[OK] Error screenshot saved to error_screenshot.png", flush=True)
            except Exception as screenshot_err:
                print(f"[WARNING] Could not take screenshot: {screenshot_err}", flush=True)
        
        if browser:
            try:
                browser.close()
            except:
                pass
        sys.exit(1)

    finally:
        # 🟢 CLEAN ENVIRONMENT TEARDOWN
        if browser:
            try:
                browser.close()
            except:
                pass

        try:
            pw_cm.__exit__(None, None, None)
        except:
            pass

        print("[DONE] Script execution environment torn down cleanly.", flush=True)


if __name__ == "__main__":
    load_dotenv()
    DECRYPT_KEY = os.getenv("DECRYPT_KEY")
    if not DECRYPT_KEY:
        raise RuntimeError("DECRYPT_KEY missing")
    run()