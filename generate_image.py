import os
import sys
import json
import time
import random
import base64
import requests
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv

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
CHATGPT_URL = get("chatgpt_url", "https://chatgpt.com/")
MIN_RANDOM_WAIT = get("min_random_wait", 15)
MAX_RANDOM_WAIT = get("max_random_wait", 30)
MIN_RANDOM_WAIT_LONG = get("min_random_wait_long", 30)
MAX_RANDOM_WAIT_LONG = get("max_random_wait_long", 60)
MIN_RANDOM_WAIT_SHORT = get("min_random_wait_short", 3)
MAX_RANDOM_WAIT_SHORT = get("max_random_wait_short", 5)
MAX_RETRIES = get("max_retries", 10)
PBKDF2_ITERATIONS = get("pbkdf2_iterations", 200000)

COOKIES_DIR = Path("chatgpt_cookies")
IMAGE_DIR = Path("image")
IMAGE_DIR.mkdir(exist_ok=True)

PLATFORM = "chatgpt"


# =========================
# RANDOM WAIT
# =========================
def custom_random_wait(min_sec, max_sec):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)


# =========================
# CRYPTO
# =========================
def _derive_key(password: bytes, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password)


def _decrypt_payload(payload: Dict[str, Any], password: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag
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
    print(f"[OK] Cookies loaded ({len(cookies)})", flush=True)
    return cookies


# =========================
# MAIN
# =========================
def run():
    apply_auto_correct()

    article_file = Path("json/article.json")

    if article_file.exists():
        try:
            with article_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("posted") is True:
                    print("[INFO] Article has already been posted. Skipping Image Generation.", flush=True)
                    sys.exit(0)
        except Exception as e:
            print(f"[WARNING] Could not read article.json: {e}, proceeding anyway...", flush=True)

    print("[START] Script started", flush=True)

    encrypted_files = list(COOKIES_DIR.glob("*.encrypted"))
    if not encrypted_files:
        raise RuntimeError("No .encrypted cookie files found in 'chatgpt_cookies/' folder")

    CHATGPT_COOKIES_FILE = random.choice(encrypted_files)
    print(f"[OK] Randomly selected cookie file: {CHATGPT_COOKIES_FILE.name}", flush=True)

    cookies = load_cookies_local(Path(CHATGPT_COOKIES_FILE))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    print("[STEP] Loading article JSON...", flush=True)
    with article_file.open("r", encoding="utf-8") as json_file:
        article_data = json.load(json_file)
    article_title = article_data["title"]
    print(f"[OK] Article Title extracted: {article_title}", flush=True)

    article_keywords = article_data["keywords"]
    print(f"[OK] Article Keywords extracted: {article_keywords}", flush=True)

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

        print("[STEP] Opening ChatGPT Main URL...", flush=True)
        page.goto(CHATGPT_URL, wait_until="load")
        print("[OK] URL opened", flush=True)

        print("[STEP] Performing initial random wait (30-60 seconds)...", flush=True)
        custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)

        # ============================================
        # CHECK LOGIN SUCCESS VIA PROFILE BUTTON
        # ============================================
        print("[STEP] Checking login success via profile button...", flush=True)
        profile_button = resolve_locator(page, PLATFORM, "chatgpt_profile_button", "Login profile button")
        if profile_button and profile_button.count() > 0:
            print(f"[OK] LOGIN SUCCESS: Profile button found", flush=True)
        else:
            print("[WARNING] Profile button not detected directly, proceeding with caution...", flush=True)

        # ============================================
        # CREATE IMAGE BUTTON
        # ============================================
        create_image_btn = resolve_locator(page, PLATFORM, "chatgpt_create_image_button", "Create an image button")
        if create_image_btn and create_image_btn.first.is_visible():
            create_image_btn.first.click()
            print("[STEP] Create an image button clicked!..", flush=True)
            custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        # ============================================
        # LOCATE CHAT TEXTBOX AND TYPE PROMPT
        # ============================================
        print("[STEP] Locating chat textbox...", flush=True)
        chat_box = resolve_locator(page, PLATFORM, "chatgpt_textbox", "Chat textbox")

        if chat_box is None or chat_box.count() == 0:
            suggested = gemini_vision_pass(
                page, PLATFORM, "chatgpt_textbox",
                "page.get_by_role('textbox', name='Chat with ChatGPT')",
                "Locating chat textbox",
            )
            print(f"[INFO] Vision pass for textbox: {suggested}", flush=True)
            if chat_box is None:
                raise RuntimeError("Textbox locator could not be loaded (All strategies failed).")

        chat_box.first.click()
        print("[OK] Textbox located and clicked successfully.", flush=True)

        prompt = (
        "You are an expert AI image engineer specializing in editorial and article cover visuals. "
        "Based on the following article details, generate a highly descriptive, cinematic, and emotionally resonant image "
        "suitable for a Substack article header image. The image should NOT look like a book cover. "
        "Instead, it should feel like a high-quality editorial photograph or digital artwork — "
        "think conceptual, atmospheric, and thought-provoking, the kind used in top editorial publications. "
        "The scene should metaphorically represent the core theme without showing any text, titles, or book covers. "
        "Vary the visual style intentionally — choose ONE of the following that best fits the article's emotional tone: "
        "surreal illustration, cinematic photography, abstract art, minimalist concept art, or moody atmospheric scene. "
        "Do NOT default to clichéd imagery like figures surrounded by swirling thoughts or cracked heads. "
        "Push toward unexpected, elegant, and original visual metaphors that feel fresh and premium. "
        "Respond ONLY with the final optimized image. Do not include any introduction, explanation, or markdown.\n\n"
        "Article Details:\n"
        f"Title: {article_title}\n"
        f"Core Keywords: {', '.join(article_keywords)}\n"
        "STRICT RULES: No text, no words, no letters, no book covers, no logos anywhere in the image. "
        "Suitable as a Substack article hero image."
        )

        print(f"[STEP] Filling prompt", flush=True)
        chat_box.first.fill(prompt)
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        page.keyboard.press("Enter")
        print("[OK] Prompt sent successfully", flush=True)

        # ============================================
        # WAIT FOR SHARE BUTTON (IMAGE GENERATED)
        # ============================================
        share_button = None
        found_share = False

        for attempt in range(1, MAX_RETRIES + 1):
            print(f"[STEP] Waiting for image generation... Attempt {attempt}/{MAX_RETRIES}", flush=True)
            custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)

            try:
                locator = resolve_locator(page, PLATFORM, "chatgpt_share_image_button", "Share this image button")
                if locator and locator.is_visible():
                    share_button = locator
                    found_share = True
                    print("'Share this image' button located successfully!", flush=True)
                    break
            except Exception as loc_err:
                print(f"[INFO] Share locator exception: {loc_err}", flush=True)

        if not found_share or not share_button:
            print("Error: 'Share this image' button not found after retries. Exiting program.", flush=True)
            if 'page' in locals() and page:
                try:
                    screenshot_path = capture_error_screenshot(page)
                    if screenshot_path:
                        upload_to_onlyfiles(screenshot_path)
                except Exception as screenshot_err:
                    print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
            sys.exit(1)

        print("[STEP] Clicking 'Share this image' button...", flush=True)
        share_button.click()
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        # ============================================
        # DOWNLOAD BUTTON
        # ============================================
        print("[STEP] Clicking 'Download' button...", flush=True)
        download_btn = resolve_locator(page, PLATFORM, "chatgpt_download_button", "Download button")

        if download_btn is None or download_btn.count() == 0:
            suggested = gemini_vision_pass(
                page, PLATFORM, "chatgpt_download_button",
                "page.get_by_role('button', name='Download')",
                "Locating download button",
            )
            print(f"[INFO] Vision pass for download button: {suggested}", flush=True)
            if download_btn is None:
                raise RuntimeError("Download button locator could not be loaded (All strategies failed).")

        with page.expect_download(timeout=60000) as download_info:
            download_btn.first.click()

        download = download_info.value
        local_filename = IMAGE_DIR / "pin.png"
        download.save_as(local_filename)
        print(f"Image downloaded successfully: {local_filename}", flush=True)

        print("[STEP] Performing final random wait before exit (30-60 seconds)...", flush=True)
        custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)
        print("[DONE] Image download successfully processed.", flush=True)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR]", e, flush=True)
        if 'page' in locals() and page:
            try:
                screenshot_path = capture_error_screenshot(page)
                if screenshot_path:
                    upload_to_onlyfiles(screenshot_path)
            except Exception as screenshot_err:
                print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
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
        print("[DONE] Script finished", flush=True)


if __name__ == "__main__":
    run()
