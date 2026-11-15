import os
import sys
import json
import time
import random
import re
import base64
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
PBKDF2_ITERATIONS = get("pbkdf2_iterations", 200000)
MIN_RANDOM_WAIT = get("min_random_wait", 15)
MAX_RANDOM_WAIT = get("max_random_wait", 30)
MIN_RANDOM_WAIT_LONG = get("min_random_wait_long", 30)
MAX_RANDOM_WAIT_LONG = get("max_random_wait_long", 60)
MIN_RANDOM_WAIT_SHORT = get("min_random_wait_short", 3)
MAX_RANDOM_WAIT_SHORT = get("max_random_wait_short", 5)

STACK_COOKIES_FILE = Path(get("stack_cookies_file", "substack_cookies/stack_cookies.json.encrypted"))
STATUS_FILE = Path("json/status.json")
COMMENTED_FILE = Path("json/commented.json")

PLATFORM = "substack"


# =========================
# DYNAMIC WAITS
# =========================
def custom_random_wait(min_sec=15, max_sec=30):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
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


# =========================
# TEXT CLEANING UTILITY
# =========================
def clean_text(text: str) -> str:
    text = text.replace("\n", " ").replace("\r", " ")

    emoji_pattern = re.compile(
        "["
        "\U00010000-\U0010FFFF"
        "\u2600-\u27BF"
        "\u2300-\u23FF"
        "]+", flags=re.UNICODE
    )
    text = emoji_pattern.sub(r'', text)

    return " ".join(text.split())


# =========================
# MAIN
# =========================
def run():
    apply_auto_correct()

    print("[START] Script started", flush=True)

    # =========================
    # STATUS JSON CHECK
    # =========================
    status_path = Path(STATUS_FILE)
    if not status_path.exists():
        print(f"[CRITICAL] {STATUS_FILE} not found. Exiting.", flush=True)
        sys.exit(1)

    with status_path.open("r", encoding="utf-8") as sf:
        status_data = json.load(sf)

    if status_data.get("post_to_comment_found") is not False:
        print("[INFO] 'post_to_comment_found' is already True or not False. Script will not run.", flush=True)
        return

    cookies = load_cookies_local(Path(STACK_COOKIES_FILE))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    # =========================
    # STEALTH SETUP & LOGIN
    # =========================
    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

    browser = None
    page = None

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
        page.goto("https://substack.com/", wait_until="load")

        print("[STEP] Initiating post-navigation delay...", flush=True)
        custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)

        # ==================================================
        # LOCATOR & COPY LINK STRATEGY
        # ==================================================
        print("[STEP] Searching for the specified menu target locator...", flush=True)

        target_locator = resolve_locator(page, PLATFORM, "substack_target_locator", "Subscribe / More options target")

        if target_locator is None or target_locator.count() == 0:
            suggested = gemini_vision_pass(
                page, PLATFORM, "substack_target_locator",
                "page.locator('div').filter(has_text=re.compile(r'^.*Subscribe$'))",
                "Searching target menu locator",
            )
            print(f"[INFO] Vision pass for target locator: {suggested}", flush=True)
            print("[CRITICAL] Specified element not found on the page. Exiting with status 1.", flush=True)
            if page:
                try:
                    capture_error_screenshot(page)
                except Exception as screenshot_err:
                    print(f"[WARNING] Could not capture screenshot: {screenshot_err}", flush=True)
            if browser:
                browser.close()
            sys.exit(1)

        print("[STEP] Element found. Clicking on it to open dropdown...", flush=True)
        target_locator.click()

        time.sleep(random.uniform(1, 3))

        # Click on 'Copy link' option in the opened dropdown
        print("[STEP] Clicking on 'Copy link' from dropdown...", flush=True)
        copy_link_btn = resolve_locator(page, PLATFORM, "substack_copy_link_menuitem", "Copy link menuitem")
        if copy_link_btn and copy_link_btn.is_visible():
            copy_link_btn.click()
        else:
            print("[CRITICAL] 'Copy link' menuitem not visible. Exiting with status 1.", flush=True)
            if browser:
                browser.close()
            sys.exit(1)

        time.sleep(random.uniform(1, 2))

        # Extract copied URL from clipboard
        copied_link = page.evaluate("navigator.clipboard.readText()")
        print(f"[OK] Link copied from clipboard: {copied_link}", flush=True)

        if not copied_link or not copied_link.startswith("http"):
            print("[CRITICAL] Invalid or empty link extracted from clipboard. Exiting with status 1.", flush=True)
            if browser:
                browser.close()
            sys.exit(1)

        # Navigate to the copied link
        print(f"[STEP] Navigating to copied link: {copied_link}", flush=True)
        page.goto(copied_link, wait_until="load")

        print("[STEP] Waiting after navigating to copied link...", flush=True)
        custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)

        current_url = page.url
        print(f"[NAVIGATED URL] Current Page URL: {current_url}", flush=True)

        # ==================================================
        # DUPLICATE URL CHECK FROM COMMENTED.JSON
        # ==================================================
        commented_path = Path(COMMENTED_FILE)
        commented_urls = []
        if commented_path.exists():
            try:
                with commented_path.open("r", encoding="utf-8") as cf:
                    commented_urls = json.load(cf)
                    if not isinstance(commented_urls, list):
                        commented_urls = []
            except Exception as e:
                print(f"[WARNING] commented.json read failed, treating as empty: {e}", flush=True)

        if current_url in commented_urls:
            print(f"[CRITICAL] URL '{current_url}' already present in json/commented.json. Skipping, status won't be True. Exiting with status 1.", flush=True)
            if browser:
                browser.close()
            sys.exit(1)

        # ==================================================
        # FETCH TEXT FROM INSIDE NAVIGATED URL
        # ==================================================
        print("[STEP] Locating the note text element...", flush=True)

        comment_locator = resolve_locator(page, PLATFORM, "substack_comment_text", "Note text element")

        if comment_locator and comment_locator.is_visible():
            raw_text = comment_locator.inner_text()
            print(f"[RAW TEXT] Extracted successfully.", flush=True)

            cleaned_content = clean_text(raw_text)
            print(f"[CLEANED TEXT] {cleaned_content}", flush=True)

            # ==================================================
            # LENGTH CHECK
            # ==================================================
            text_length = len(cleaned_content)
            if text_length < 120:
                print(f"[CRITICAL] Extracted text length ({text_length} chars) is less than 120. Skipping and terminating with exit status 1.", flush=True)
                if page:
                    try:
                        capture_error_screenshot(page)
                    except Exception as screenshot_err:
                        print(f"[WARNING] Could not capture screenshot: {screenshot_err}", flush=True)
                if browser:
                    browser.close()
                sys.exit(1)

            # ==================================================
            # USERNAME TOP 100 CHECK AGAINST COMMENTED.JSON
            # ==================================================
            username_match = re.search(r'substack\.com/(@[^/]+)', current_url)
            extracted_username = username_match.group(1).lower() if username_match else None

            top_100_urls = commented_urls[:100]

            username_found_in_top_100 = False
            if extracted_username:
                for link in top_100_urls:
                    link_match = re.search(r'substack\.com/(@[^/]+)', link)
                    if link_match and link_match.group(1).lower() == extracted_username:
                        username_found_in_top_100 = True
                        break

            if username_found_in_top_100:
                print(f"[INFO] Username '{extracted_username}' found in the top 100 of json/commented.json. Executing hide/snooze flow...", flush=True)
                try:
                    print("[STEP] Navigating back to the previous page...", flush=True)
                    page.go_back(wait_until="domcontentloaded")

                    print("[STEP] Waiting 15-30 seconds after going back...", flush=True)
                    custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)

                    print("[STEP] Checking for 'More options' button...", flush=True)
                    more_options_btn = resolve_locator(page, PLATFORM, "substack_more_options_button", "More options button")

                    if more_options_btn and more_options_btn.is_visible():
                        print("[OK] 'More options' button found. Clicking it...", flush=True)
                        more_options_btn.click()

                        print("[STEP] Waiting 3-6 seconds...", flush=True)
                        time.sleep(random.uniform(3, 6))

                        print("[STEP] Clicking 'Hide note' from menu...", flush=True)
                        hide_note = resolve_locator(page, PLATFORM, "substack_hide_note_menuitem", "Hide note menuitem")
                        if hide_note:
                            hide_note.click()

                        print("[STEP] Waiting 6-12 seconds...", flush=True)
                        time.sleep(random.uniform(6, 12))

                        print("[STEP] Clicking snooze option for 30 days...", flush=True)
                        snooze = resolve_locator(page, PLATFORM, "substack_snooze_option", "Snooze 30 days option")
                        if snooze:
                            snooze.click()

                        print("[STEP] Final wait before closing browser (6-12 seconds)...", flush=True)
                        time.sleep(random.uniform(6, 12))

                        print("[OK] All steps completed successfully.", flush=True)
                    else:
                        print("[WARNING] Locator 'More options' button not found on the page.", flush=True)

                except Exception as flow_err:
                    print(f"[WARNING] Error during navigation/snooze flow: {flow_err}", flush=True)

                if browser:
                    browser.close()
                sys.exit(1)

            else:
                # ==================================================
                # UPDATE AND SAVE STATUS JSON (SUCCESS PATH)
                # ==================================================
                status_data["post_to_comment_found"] = True
                status_data["link_to_post_to_comment"] = current_url
                status_data["content_of_post_to_comment"] = cleaned_content

                with status_path.open("w", encoding="utf-8") as sf:
                    json.dump(status_data, sf, indent=4, ensure_ascii=False)

                print("[OK] json/status.json updated successfully with new details.", flush=True)

        else:
            print("[WARNING] Note text element was not visible on the page. status.json not updated.", flush=True)
            if page:
                try:
                    capture_error_screenshot(page)
                except Exception as screenshot_err:
                    print(f"[WARNING] Could not capture screenshot: {screenshot_err}", flush=True)
            if browser:
                browser.close()
            sys.exit(1)

        # Final wait before browser close
        print("[STEP] Initiating final post-execution delay...", flush=True)
        custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)

        print("[SUCCESS] Navigation completed successfully. Closing browser.", flush=True)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR] Script execution broke down due to trace:", e, flush=True)

        if page:
            try:
                capture_error_screenshot(page)
            except Exception as screenshot_err:
                print(f"[WARNING] Could not capture screenshot: {screenshot_err}", flush=True)

        if browser:
            try:
                browser.close()
            except:
                pass
        sys.exit(1)

    finally:
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
