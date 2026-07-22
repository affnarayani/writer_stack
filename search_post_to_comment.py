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


# =========================
# CONFIG
# =========================
HEADLESS = True

STACK_COOKIES_FILE = "stack_cookies.json.encrypted"
STATUS_FILE = "status.json"
COMMENTED_FILE = "commented.json"  # NEW: Path for checked/commented URLs

PBKDF2_ITERATIONS = 200_000

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


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
# TEXT CLEANING UTILITY
# =========================
def clean_text(text: str) -> str:
    # Next line (\n aur \r) ko space se replace karna
    text = text.replace("\n", " ").replace("\r", " ")
    
    # Emojis ko filter out karne ke liye Regex (BMP aur Extended planes dono handle karega)
    # Yeh un characters ko rakhega jo emojis nahi hain
    emoji_pattern = re.compile(
        "["
        "\U00010000-\U0010FFFF"  # Extended symbols and pictographs (emojis)
        "\u2600-\u27BF"          # Misc symbols & Dingbats
        "\u2300-\u23FF"          # Misc Technical
        "]+", flags=re.UNICODE
    )
    text = emoji_pattern.sub(r'', text)
    
    # Extra spaces ko single space banana aur trim karna
    return " ".join(text.split())

def upload_to_tmpfiles(screenshot_path):
    url = "https://tmpfiles.org/api/v1/upload"
    
    with open(screenshot_path, "rb") as file:
        response = requests.post(url, files={"file": file})
        
    if response.status_code == 200:
        res_data = response.json()
        # Direct view URL banane ke liye '/dl/' replace karte hain
        page_url = res_data["data"]["url"]
        direct_url = page_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
        print(f"👉 DIRECT LINK (Expires in 2 Hours): {direct_url}")
        return direct_url
    else:
        print(f"[WARNING] Upload Failed: {response.status_code}")
        return None

# =========================
# MAIN
# =========================
def run():
    print("[START] Script started", flush=True)

    # =========================
    # STATUS JSON CHECK
    # =========================
    status_path = Path(STATUS_FILE)
    if not status_path.exists():
        print(f"[CRITICAL] {STATUS_FILE} nahi mila. Exiting.", flush=True)
        sys.exit(1)
        
    with open(status_path, "r", encoding="utf-8") as sf:
        status_data = json.load(sf)
        
    # Check condition: post_to_comment_found false hona chahiye
    if status_data.get("post_to_comment_found") is not False:
        print("[INFO] 'post_to_comment_found' is already True or not False. Script run nahi hoga.", flush=True)
        return

    cookies = load_cookies(Path(STACK_COOKIES_FILE))
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
        # Launch browser with stealth args
        browser = pw.chromium.launch(
            headless=HEADLESS,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled"
            ]
        )

        # Context instantiation with custom user agent
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
        
        # 15 to 30 seconds random wait as requested
        print("[STEP] Initiating post-navigation delay...", flush=True)
        custom_random_wait(15, 30)
        
        # ==================================================
        # STRICT SINGLE LOCATOR STRATEGY
        # ==================================================
        print("[STEP] Searching strictly for the specified CSS locator...", flush=True)
        
        # Sirf wahi locator jo aapne specify kiya hai
        target_locator = page.locator('.pencraft.pc-display-flex.pc-gap-12.pc-alignItems-flex-start').first

        # Check if the element is visible on the page
        if not target_locator.is_visible():
            print("[CRITICAL] Specified element not found on the page. Exiting with status 1.", flush=True)
            if page is not None:
                try:
                    screenshot_path = "locator_not_found_screenshot.png"
                    page.screenshot(path=screenshot_path, full_page=True)
                    print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                    
                    upload_to_tmpfiles(screenshot_path)
                except Exception as screenshot_err:
                    print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
            if browser:
                browser.close()
            sys.exit(1)

        print("[STEP] Element found. Clicking on it...", flush=True)
        target_locator.click()

        # Naye page/note page par jane ke baad fir se 15-30 second wait
        print("[STEP] Waiting after clicking the note...", flush=True)
        custom_random_wait(15, 30)

        # Navigated page ka URL print karna
        current_url = page.url
        print(f"[NAVIGATED URL] Current Page URL: {current_url}", flush=True)
        
        # ==================================================
        # NEW: DUPLICATE URL CHECK FROM COMMENTED.JSON
        # ==================================================
        commented_path = Path(COMMENTED_FILE)
        commented_urls = []
        if commented_path.exists():
            try:
                with open(commented_path, "r", encoding="utf-8") as cf:
                    commented_urls = json.load(cf)
                    if not isinstance(commented_urls, list):
                        commented_urls = []
            except Exception as e:
                print(f"[WARNING] commented.json read failed, treating as empty: {e}", flush=True)

        if current_url in commented_urls:
            print(f"[CRITICAL] URL '{current_url}' pehle se hi commented.json mein maujood hai. Skipping append, status won't be True. Exiting with status 1.", flush=True)
            if browser:
                browser.close()
            sys.exit(1)
        
        # ==================================================
        # FETCH TEXT FROM INSIDE NAVIGATED URL
        # ==================================================
        print("[STEP] Locating the new comment/text element...", flush=True)
        
        # Aapka bataya hua CSS selector
        comment_locator = page.locator('#reader-nav-page-scroll > div > div > div > div > div:nth-child(2) > div > div > div.pencraft.pc-display-flex.pc-flexDirection-column.pc-gap-8.pc-reset.permalinkHeader-bQJTnJ > div > div.pencraft.pc-display-flex.pc-flexDirection-column.pc-reset.feedCommentBody-UWho7S > div > div').first

        if comment_locator.is_visible():
            raw_text = comment_locator.inner_text()
            print(f"[RAW TEXT] Extracted successfully.", flush=True)
            
            # Emojis aur New lines (\n) ko filter out karna
            cleaned_content = clean_text(raw_text)
            print(f"[CLEANED TEXT] {cleaned_content}", flush=True)
            
            # ==================================================
            # LENGTH CHECK (NEW MODIFICATION)
            # ==================================================
            text_length = len(cleaned_content)
            if text_length < 150:
                print(f"[CRITICAL] Extracted text length ({text_length} chars) is less than 150. Skipping and terminating with exit status 1.", flush=True)
                if page is not None:
                    try:
                        screenshot_path = "text_too_short_screenshot.png"
                        page.screenshot(path=screenshot_path, full_page=True)
                        print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                        
                        upload_to_tmpfiles(screenshot_path)
                    except Exception as screenshot_err:
                        print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
                if browser:
                    browser.close()
                sys.exit(1)
            
            # ==================================================
            # UPDATE AND SAVE STATUS JSON (SUCCESS PATH)
            # ==================================================
            status_data["post_to_comment_found"] = True
            status_data["link_to_post_to_comment"] = current_url
            status_data["content_of_post_to_comment"] = cleaned_content
            
            with open(status_path, "w", encoding="utf-8") as sf:
                json.dump(status_data, sf, indent=4, ensure_ascii=False)
                
            print("[OK] status.json updated successfully with new details.", flush=True)
            
        else:
            print("[WARNING] New text element was not visible on the page. status.json not updated.", flush=True)
            try:
                screenshot_path = "comment_element_not_found.png"
                page.screenshot(path=screenshot_path, full_page=True)
                print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                
                upload_to_tmpfiles(screenshot_path)
            except Exception as screenshot_err:
                print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
        
        # Final wait browser close karne se pehle (as usual 15 to 30 seconds)
        print("[STEP] Initiating final post-execution delay...", flush=True)
        custom_random_wait(15, 30)

        print("[SUCCESS] Navigation completed successfully. Closing browser.", flush=True)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR] Script execution broke down due to trace:", e, flush=True)
        
        # ============================================
        # NEW: CAPTURE SCREENSHOT ON ERROR
        # ============================================
        if 'page' in locals() and page:
            try:
                screenshot_path = "error_screenshot.png"
                page.screenshot(path=screenshot_path, full_page=True)
                print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                
                upload_to_tmpfiles(screenshot_path)
            except Exception as screenshot_err:
                print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
        # ============================================
        
        if browser:
            try:
                browser.close()
            except:
                pass
        sys.exit(1)

    finally:
        # CLEAN ENVIRONMENT TEARDOWN
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