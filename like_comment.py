import os
import sys
import json
import time
import base64
import random
import requests
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
ENABLE_LIKE = False

SUBSTACK_COOKIES_FILE = "stack_cookies.json.encrypted"
STATUS_JSON_FILE = "status.json"
COMMENTED_JSON_FILE = "commented.json"

PBKDF2_ITERATIONS = 200_000

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


# =========================
# RANDOM WAIT (REQUIRED LOCATOR DELAYS)
# =========================
def custom_random_wait(min_sec=5, max_sec=10):
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
# ENV VALIDATION
# =========================
load_dotenv()

DECRYPT_KEY = os.getenv("DECRYPT_KEY")

if not DECRYPT_KEY:
    raise RuntimeError("DECRYPT_KEY missing")


# =========================
# MAIN
# =========================
def run():
    print("[START] Script started", flush=True)

    # ========================================================
    # STATUS JSON VALIDATION
    # ========================================================
    status_path = Path(STATUS_JSON_FILE)
    if not status_path.exists():
        raise FileNotFoundError(f"❌ Status file {STATUS_JSON_FILE} not found!")

    with status_path.open("r", encoding="utf-8") as sf:
        status_data = json.load(sf)

    comment_gen = status_data.get("comment_generated")
    comment_to_post = status_data.get("comment")
    target_url = status_data.get("link_to_post_to_comment")

    # Condition: "comment_generated" must be true AND "comment" key must not be empty
    if not (comment_gen is True and comment_to_post and str(comment_to_post).strip() != ""):
        print("[EXIT] Script will not run: 'comment_generated' is not True or 'comment' is empty.", flush=True)
        sys.exit(0)

    if not target_url:
        print("[ERROR] Substack URL missing in status.json.", flush=True)
        sys.exit(1)

    # Cookies setup
    cookies = load_cookies(Path(SUBSTACK_COOKIES_FILE))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    # =========================
    # STEALTH SETUP
    # =========================
    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

    browser = None
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

        # ========================================================
        # DIRECT NAVIGATION TO SUBSTACK URL
        # ========================================================
        print(f"[STEP] Navigating directly to Substack post URL: {target_url}...", flush=True)
        page.goto(target_url, wait_until="domcontentloaded")
        print(f"[OK] {target_url} opened completely", flush=True)
        
        # 1. URL par navigate hone ke baad 15, 30 seconds ka random wait
        custom_random_wait(15, 30)

        # 2. Optional Like Step Check
        if ENABLE_LIKE:
            print("[STEP] Like functionality enabled. Locating main post like button...", flush=True)
            like_selector = "div[class='pencraft pc-display-flex pc-justifyContent-space-between pc-alignItems-center pc-paddingBottom-8 pc-reset'] button[aria-label='Like']"
            like_btn = page.locator(like_selector).first
            
            # Wait and click with strict locator checks
            like_btn.wait_for(state="visible", timeout=15000)
            like_btn.click()
            print("[OK] Main post like button clicked successfully.", flush=True)

            # 3. Wait 3, 6 seconds random after like
            custom_random_wait(3, 6)
        else:
            print("[INFO] Like functionality disabled via configurations. Skipping like step...", flush=True)

        # 4. Find and click 'New post' button
        print("[STEP] Locating and clicking 'New post' button...", flush=True)
        new_post_btn = page.get_by_role('button', name='New post')
        new_post_btn.wait_for(state="visible", timeout=15000)
        new_post_btn.click()
        print("[OK] 'New post' clicked.", flush=True)

        # 5. Again 15, 30 seconds ka random wait
        custom_random_wait(15, 30)

        # 6. Dynamic Class Selector for text input editor container
        print("[STEP] Locating dynamic text editor input container...", flush=True)
        editor_selector = "div[class^='pencraft pc-display-flex pc-flexDirection-column pc-reset textEditor-']"
        input_field = page.locator(editor_selector).first
        
        input_field.wait_for(state="visible", timeout=15000)
        input_field.click()
        print("[OK] Dynamic text editor field clicked & focused.", flush=True)

        # 7. 3, 6 seconds random wait
        custom_random_wait(3, 6)

        # 8. Start typing comment like a human
        print("[STEP] Typing comment via native keyboard emulation...", flush=True)
        for char in comment_to_post:
            page.keyboard.type(char)
            time.sleep(random.uniform(0.04, 0.09))
            
        print("[OK] Typing completed.", flush=True)

        # 9. Jab comment type ho jaaye to 3, 6 seconds random wait kare
        custom_random_wait(3, 6)

        # 10. Click 'composer-post' button
        print("[STEP] Clicking 'composer-post' submit button...", flush=True)
        submit_btn = page.get_by_test_id('composer-post')
        submit_btn.wait_for(state="visible", timeout=15000)
        submit_btn.click()
        print("[OK] Comment submitted successfully!", flush=True)

        # ========================================================
        # HISTORY UPDATE (APPEND TO TOP)
        # ========================================================
        json_path = Path(COMMENTED_JSON_FILE)
        existing_urls = []
        if json_path.exists():
            try:
                with json_path.open("r", encoding="utf-8") as jf:
                    existing_urls = json.load(jf)
                    if not isinstance(existing_urls, list):
                        existing_urls = []
            except Exception as j_err:
                print(f"[WARNING] Reading history json failed: {j_err}", flush=True)

        if target_url not in existing_urls:
            existing_urls.insert(0, target_url)
            with json_path.open("w", encoding="utf-8") as jf:
                json.dump(existing_urls, jf, indent=4)
            print("[OK] History JSON updated (URL appended to top).", flush=True)

        # ========================================================
        # RESET STATUS JSON ON SUCCESSFUL RUN
        # ========================================================
        print(f"[STEP] Resetting all keys in {STATUS_JSON_FILE}...", flush=True)
        status_data["post_to_comment_found"] = False
        status_data["link_to_post_to_comment"] = ""
        status_data["content_of_post_to_comment"] = ""
        status_data["comment_generated"] = False
        status_data["comment"] = ""
        
        with status_path.open("w", encoding="utf-8") as sf:
            json.dump(status_data, sf, indent=4)
        print("[OK] status.json reset complete.", flush=True)

        # 11. Wait 30, 60 seconds before browser close
        print("[STEP] Final hold before closing browser context...", flush=True)
        custom_random_wait(30, 60)

    except SystemExit:
        raise
    except Exception as e:
        print("[CRITICAL ERROR] Automation pipeline failed or locator timed out:", e, flush=True)
        # ============================================
        # CAPTURE SCREENSHOT ON ERROR WITH IMGBB UPLOAD
        # ============================================
        if 'page' in locals() and page:
            try:
                screenshot_path = "error_screenshot.png"
                page.screenshot(path=screenshot_path, full_page=True)
                print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                
                imgbb_key = os.getenv("IMGBBB_API_KEY")
                if imgbb_key:
                    print("[OK] Uploading screenshot to ImgBB...", flush=True)
                    url = f"https://api.imgbb.com/1/upload?expiration=86400&key={imgbb_key}"
                    
                    with open(screenshot_path, "rb") as file:
                        response = requests.post(url, files={"image": file})
                    
                    if response.status_code == 200:
                        res_data = response.json()
                        direct_url = res_data["data"]["display_url"]
                        print("\n" + "="*50, flush=True)
                        print(f"👉 DIRECT SCREENSHOT LINK: {direct_url}", flush=True)
                        print("="*50 + "\n", flush=True)
                    else:
                        print(f"[WARNING] ImgBB Upload Failed Status: {response.status_code}", flush=True)
                else:
                    print("[WARNING] IMGBBB_API_KEY environment variable not found.", flush=True)
            except Exception as screenshot_err:
                print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
        # ============================================
        sys.exit(1)

    finally:
        if browser:
            try:
                browser.close()
                print("[OK] Browser closed context safely.", flush=True)
            except:
                pass
        try:
            pw_cm.__exit__(None, None, None)
        except:
            pass

        print("[DONE] Process terminated cleanly.", flush=True)


if __name__ == "__main__":
    run()