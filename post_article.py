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

        # 2. Navigate to Publish Post URL
        post_url = "https://mindtobetter.substack.com/publish/post/"
        print(f"[STEP] Navigating to editor URL: {post_url}", flush=True)
        page.goto(post_url, wait_until="load")
        custom_random_wait(6, 12)

        # =========================
        # EDITOR WORKFLOW
        # =========================

        # Title Input
        if "title" in article_data:
            print("[STEP] Entering Title...", flush=True)
            title_box = page.get_by_test_id('post-title')
            title_box.wait_for(state="visible")
            title_box.click()
            for char in article_data["title"]:
                page.keyboard.type(char)
                time.sleep(random.uniform(0.04, 0.12))
            print("[OK] Title entered", flush=True)
            custom_random_wait(3, 6)

        # Subtitle Input
        if "subtitle" in article_data:
            print("[STEP] Entering Subtitle...", flush=True)
            subtitle_box = page.get_by_role('textbox', name='Add a subtitle…')
            subtitle_box.wait_for(state="visible")
            subtitle_box.click()
            for char in article_data["subtitle"]:
                page.keyboard.type(char)
                time.sleep(random.uniform(0.04, 0.12))
            print("[OK] Subtitle entered", flush=True)
            custom_random_wait(4, 8)

        # Move cursor to Body / Paragraph space
        page.keyboard.press("Enter")
        custom_random_wait(3, 6)

        # Image Upload Flow via Dropdown
        print("[STEP] Clicking Media Attach (Image) button...", flush=True)
        media_btn = page.get_by_role('button', name='Image')
        media_btn.wait_for(state="visible")
        media_btn.click()
        custom_random_wait(2, 4)

        print("[STEP] Triggering file chooser via dropdown option and uploading image...", flush=True)
        dropdown_item = page.get_by_role('menuitem', name='Image', exact=True)
        dropdown_item.wait_for(state="visible")
        
        with page.expect_file_chooser() as fc_info:
            dropdown_item.click()
        
        file_chooser = fc_info.value
        file_chooser.set_files(IMAGE_PATH)
        print("[OK] Image attached successfully", flush=True)
        custom_random_wait(6, 12)

        # Body Paragraphs Typing Flow
        is_first_para = True
        for key in content_keys:
            para_text = article_data[key]
            if not para_text.strip():
                continue

            print(f"[STEP] Processing node: {key}...", flush=True)

            if is_first_para:
                first_para_locator = page.get_by_role('paragraph').first
                first_para_locator.wait_for(state="visible")
                first_para_locator.click()
                is_first_para = False

            # Special Link Parsing workflow for p_cta
            if key == "p_cta" and "http" in para_text:
                print("[STEP] Precision link targeting active for p_cta (Standard speed)", flush=True)
                
                parts = para_text.split("http")
                target_url = "http" + parts[1].strip()
                text_before_url = parts[0].strip()
                
                sub_parts = text_before_url.split("Click Here")
                body_message = sub_parts[0].strip().rstrip(":")
                display_text = "Click Here" + sub_parts[1].rstrip(":")
                
                for char in body_message:
                    page.keyboard.type(char)
                    time.sleep(random.uniform(0.03, 0.10))
                
                page.keyboard.type(" ")
                time.sleep(0.1)
                
                for char in display_text:
                    page.keyboard.type(char)
                    time.sleep(random.uniform(0.04, 0.12))
                custom_random_wait(2, 4)

                print(f"[STEP] Selecting anchor text: '{display_text}'", flush=True)
                page.keyboard.down("Shift")
                for _ in range(len(display_text)):
                    page.keyboard.press("ArrowLeft")
                    time.sleep(0.02)
                page.keyboard.up("Shift")
                custom_random_wait(2, 4)

                print("[STEP] Triggering Link toolbar button...", flush=True)
                link_btn = page.get_by_role('button', name='Link')
                link_btn.wait_for(state="visible")
                link_btn.click()
                custom_random_wait(2, 4)

                print("[STEP] Filling URL textbox...", flush=True)
                url_input = page.get_by_role('textbox', name='Enter URL...')
                url_input.wait_for(state="visible")
                url_input.fill(target_url)
                custom_random_wait(2, 4)

                print("[STEP] Confirming hyperlink creation...", flush=True)
                confirm_link = page.get_by_text('Link', exact=True)
                confirm_link.click()
                custom_random_wait(3, 5)

                print("[STEP] Navigating out of selection and breaking line...", flush=True)
                page.keyboard.press("ArrowRight")
                time.sleep(0.5)
                page.keyboard.press("Enter")
                custom_random_wait(4, 8)

            elif key == "conclusion":
                print("[STEP] Styling Heading 3 block for Conclusion header...", flush=True)
                
                style_picker = page.get_by_test_id('style-picker')
                style_picker.wait_for(state="visible")
                style_picker.click()
                custom_random_wait(1, 3)
                
                heading_item = page.get_by_role('menuitem', name='Heading 3')
                heading_item.wait_for(state="visible")
                heading_item.click()
                custom_random_wait(1, 3)
                
                bold_btn = page.get_by_role('button', name='Bold')
                bold_btn.wait_for(state="visible")
                bold_btn.click()
                custom_random_wait(1, 3)
                
                print("[STEP] Typing heading tag: 'Conclusion'...", flush=True)
                for char in "Conclusion":
                    page.keyboard.type(char)
                    time.sleep(random.uniform(0.04, 0.12))
                custom_random_wait(2, 4)
                
                page.keyboard.press("Enter")
                custom_random_wait(2, 4)
                
                print("[STEP] Re-clicking Bold button to turn off bold formatting...", flush=True)
                bold_btn.wait_for(state="visible")
                bold_btn.click()
                custom_random_wait(2, 4)
                
                print("[STEP] Injecting main conclusion body paragraphs...", flush=True)
                for char in para_text:
                    page.keyboard.type(char)
                    time.sleep(random.uniform(0.03, 0.10))
                
                print("[OK] Conclusion text processed successfully", flush=True)
                custom_random_wait(4, 8)
                page.keyboard.press("Enter")
                custom_random_wait(3, 6)

            else:
                for char in para_text:
                    page.keyboard.type(char)
                    time.sleep(random.uniform(0.03, 0.10))
                
                print(f"[OK] Node ({key}) completed typing", flush=True)
                custom_random_wait(4, 8)
                
                page.keyboard.press("Enter")
                custom_random_wait(3, 6)

        print("[SUCCESS] All dynamic text contents appended successfully.", flush=True)

        # =========================
        # NEW PUBLISHING FLOW
        # =========================
        print("[STEP] Clicking primary 'Publish' action trigger button...", flush=True)
        primary_publish_btn = page.get_by_test_id('publish-button')
        primary_publish_btn.wait_for(state="visible")
        primary_publish_btn.click()
        
        intermediate_publish_wait()

        # Handle Keywords Tag Injection Phase
        if chosen_keywords:
            print("[STEP] Locating 'Select or create tags' tags combobox input...", flush=True)
            tags_input = page.get_by_role('combobox', name='Select or create tags')
            tags_input.wait_for(state="visible")
            tags_input.click()
            custom_random_wait(2, 4)

            for index, kw in enumerate(chosen_keywords, start=1):
                print(f"[STEP] Inserting meta tag {index}/{len(chosen_keywords)}: '{kw}'", flush=True)
                for char in kw:
                    page.keyboard.type(char)
                    time.sleep(random.uniform(0.04, 0.12))
                
                keyword_short_wait()
                print(f"[STEP] Locking tag item sequence via Enter key...", flush=True)
                page.keyboard.press("Enter")
                keyword_short_wait()
            
            intermediate_publish_wait()

        # Execute final submit action button click
        print("[STEP] Dispatching ultimate post submission delivery click...", flush=True)
        final_send_btn = page.get_by_role('button', name='Send to everyone now')
        final_send_btn.wait_for(state="visible")
        final_send_btn.click()
        print("[SUCCESS] Article successfully transmitted to server pipelines!", flush=True)
        
        # ================================================
        # POST-SUCCESS ACTIONS (JSON STATUS RE-WRITE)
        # ================================================
        mark_article_as_posted(ARTICLE_FILE)
        
        intermediate_publish_wait()

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