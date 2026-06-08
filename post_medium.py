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

MEDIUM_COOKIES_FILE = "medium_cookies.json.encrypted"
ARTICLE_FILE = "article.json"
IMAGE_PATH = "image/pin.png"

PBKDF2_ITERATIONS = 200_000

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


# =========================
# ENV
# =========================
load_dotenv()

DECRYPT_KEY = os.getenv("DECRYPT_KEY")

if not DECRYPT_KEY:
    raise RuntimeError("DECRYPT_KEY missing")


# =========================
# DYNAMIC WAITS
# =========================
def custom_random_wait(min_sec=6, max_sec=12):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)


def long_publish_wait():
    seconds = random.uniform(15, 30)
    print(f"[WAIT] Publishing phase delay: Sleeping for {seconds:.2f} seconds...", flush=True)
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
# DATA LOADER
# =========================
def load_article_data(file_path: str) -> Dict[str, Any]:
    print(f"[STEP] Reading article content from {file_path}...", flush=True)
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


# =========================
# MAIN
# =========================
def run():
    print("[START] Script started", flush=True)

    if not os.path.exists(IMAGE_PATH):
        print(f"[ERROR] Required image file not found at: {IMAGE_PATH}. Exiting process.", flush=True)
        sys.exit(1)

    cookies = load_cookies(Path(MEDIUM_COOKIES_FILE))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    article_data = load_article_data(ARTICLE_FILE)
    article_title = article_data.get("title", "Untitled Story")
    
    # Naye json se keywords extract karna (Default empty list agar key missing ho)
    chosen_keywords = article_data.get("keywords", [])
    print(f"[OK] Extracted keywords from JSON: {chosen_keywords}", flush=True)

    # Content keys nikalte waqt 'title' aur 'keywords' dono ko exclude kar rahe hain
    raw_keys = [k for k in article_data.keys() if k not in ["title", "keywords"]]
    content_keys = [key for key in raw_keys]

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

        print("[STEP] Opening Medium URL...", flush=True)
        page.goto(
            "https://medium.com/new-story",
            wait_until="networkidle"
        )
        print("[OK] Medium URL opened completely (Logged In)", flush=True)
        custom_random_wait(6, 12)
        
        # =========================
        # EDITOR WORKFLOW
        # =========================
        
        # 1. Title Input
        print("[STEP] Entering Title...", flush=True)
        title_para = page.get_by_test_id('editorTitleParagraph')
        title_para.wait_for(state="visible")
        title_para.click()
        
        for char in article_title:
            page.keyboard.type(char)
            time.sleep(random.uniform(0.05, 0.15))
            
        print("[OK] Title entered successfully", flush=True)
        custom_random_wait(6, 12)

        print("[STEP] Pressing Enter after title to shift to body...", flush=True)
        page.keyboard.press("Enter")
        custom_random_wait(6, 12)

        # 2. Image Upload
        print("[STEP] Clicking Add Button for Image...", flush=True)
        add_btn = page.get_by_test_id('editorAddButton')
        add_btn.wait_for(state="visible")
        add_btn.click()
        print("[OK] Add button clicked", flush=True)
        custom_random_wait(6, 12)

        print("[STEP] Uploading Image...", flush=True)
        image_btn = page.get_by_role('button', name='Add an image', exact=True)
        image_btn.wait_for(state="visible")

        with page.expect_file_chooser() as fc_info:
            image_btn.click()
        
        file_chooser = fc_info.value
        file_chooser.set_files(IMAGE_PATH)
        print("[OK] Image attached successfully", flush=True)
        custom_random_wait(6, 12)
        
        # 3. Image Caption / Alt Text Input
        print("[STEP] Entering Image Caption...", flush=True)
        caption_element = page.get_by_text('Type caption for image (')
        caption_element.wait_for(state="visible")
        caption_element.click()
        custom_random_wait(6, 12)
        
        for char in article_title:
            page.keyboard.type(char)
            time.sleep(random.uniform(0.05, 0.15))
            
        print("[OK] Image caption added successfully", flush=True)
        custom_random_wait(6, 12)

        print("[STEP] Pressing Enter to move past image into paragraph blocks...", flush=True)
        page.keyboard.press("Enter")
        custom_random_wait(6, 12)

        # 4. Dynamic Paragraphs Typing
        for key in content_keys:
            para_text = article_data[key]
            if not para_text.strip():
                continue
                
            print(f"[STEP] Processing paragraph node ({key})...", flush=True)
            
            if key == "p_cta" and "http" in para_text:
                print(f"[STEP] Hyperlink formatting detected for p_cta", flush=True)
                
                parts = para_text.split("http")
                display_text = parts[0].strip().rstrip(":")
                target_url = "http" + parts[1].strip()
                
                for char in display_text:
                    page.keyboard.type(char)
                    time.sleep(random.uniform(0.03, 0.12))
                
                custom_random_wait(2, 4)
                
                print(f"[STEP] Selecting text for embedding hyperlink...", flush=True)
                page.keyboard.down("Shift")
                for _ in range(len(display_text)):
                    page.keyboard.press("ArrowLeft")
                    time.sleep(0.02)
                page.keyboard.up("Shift")
                
                custom_random_wait(2, 4)
                
                print(f"[STEP] Clicking hyperlink action button...", flush=True)
                link_btn = page.locator('button[data-action="link"]')
                link_btn.wait_for(state="visible")
                link_btn.click()
                custom_random_wait(3, 5)
                
                print(f"[STEP] Filling URL into link input textbox...", flush=True)
                link_input = page.get_by_role('textbox', name='Paste or type a link…')
                link_input.wait_for(state="visible")
                link_input.fill(target_url)
                custom_random_wait(2, 4)
                
                print(f"[STEP] Pressing 1st Enter to embed/save the link...", flush=True)
                link_input.press("Enter")
                custom_random_wait(2, 4)
                
                print(f"[STEP] Pressing 2nd Enter to break into next paragraph block...", flush=True)
                page.keyboard.press("Enter")
                custom_random_wait(6, 12)
                
                print(f"[OK] Paragraph ({key}) finished typing (hyperlink + block break handled)", flush=True)
                continue
                
            else:
                for char in para_text:
                    page.keyboard.type(char)
                    time.sleep(random.uniform(0.03, 0.12)) 
                    
            print(f"[OK] Paragraph ({key}) finished typing", flush=True)
            custom_random_wait(6, 12)
            
            print(f"[STEP] Pressing Enter to create next section break...", flush=True)
            page.keyboard.press("Enter")
            custom_random_wait(6, 12)

        print("[SUCCESS] All dynamic contents appended safely.", flush=True)

        # =========================
        # PUBLISHING WORKFLOW
        # =========================
        print("[STEP] Post-writing cool down phase...", flush=True)
        long_publish_wait()

        # 1. Click First Publish Button
        print("[STEP] Clicking primary 'Publish' drop-down button...", flush=True)
        publish_trigger = page.get_by_role('button', name='Publish', exact=True)
        publish_trigger.wait_for(state="visible")
        publish_trigger.click()
        print("[OK] Publish panel opened", flush=True)
        
        long_publish_wait()

        # 2. Add Topics / Keywords (Ab direct variables JSON se aa rahe hain)
        if chosen_keywords:
            print("[STEP] Locating 'Add a topic...' combobox input...", flush=True)
            topic_input = page.get_by_role('combobox', name='Add a topic...')
            topic_input.wait_for(state="visible")
            topic_input.click()

            for index, kw in enumerate(chosen_keywords, start=1):
                print(f"[STEP] Inserting keyword {index}/{len(chosen_keywords)}: '{kw}'", flush=True)
                
                for char in kw:
                    page.keyboard.type(char)
                    time.sleep(random.uniform(0.05, 0.15))
                    
                keyword_short_wait()
                
                print(f"[STEP] Pressing Enter to lock tag '{kw}'...", flush=True)
                page.keyboard.press("Enter")
                
                keyword_short_wait()

            long_publish_wait()
        else:
            print("[WARNING] No keywords found in JSON metadata, skipping tags injection phase...", flush=True)

        # 3. Click Final Publish Button
        print("[STEP] Executing final story submission button click...", flush=True)
        final_publish_btn = page.get_by_role('button', name='Publish', exact=True)
        final_publish_btn.wait_for(state="visible")
        final_publish_btn.click()
        print("[SUCCESS] Article successfully published!", flush=True)

        long_publish_wait()

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR] Automation cycle broke or publish failed due to runtime trace:", e, flush=True)
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