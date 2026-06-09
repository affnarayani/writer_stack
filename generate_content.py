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

COOKIES_DIR = Path("cookies")
encrypted_files = list(COOKIES_DIR.glob("*.encrypted"))

if not encrypted_files:
    raise RuntimeError("❌ No .encrypted cookie files found in 'cookies/' folder")

CHATGPT_COOKIES_FILE = random.choice(encrypted_files)
print(f"[OK] Randomly selected cookie file: {CHATGPT_COOKIES_FILE.name}", flush=True)

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

    # normalize SameSite and PartitionKey
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
# FILE PARSERS & WRITERS
# =========================
def get_last_topic() -> str:
    print("[STEP] Reading topics.txt...", flush=True)
    topics_file = Path("topics.txt")
    if not topics_file.exists():
        raise FileNotFoundError("❌ 'topics.txt' file nahi mila.")
    
    with topics_file.open("r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    
    if not lines:
        raise ValueError("❌ 'topics.txt' khali hai.")
    
    selected_topic = lines[-1]
    print(f"[OK] Selected last topic: '{selected_topic}'", flush=True)
    return selected_topic


def remove_last_topic_from_file():
    print("[STEP] Removing the processed topic from topics.txt...", flush=True)
    topics_file = Path("topics.txt")
    if not topics_file.exists():
        return
        
    with topics_file.open("r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
        
    if lines:
        lines.pop()  # Remove last elements
        
    with topics_file.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(f"{line}\n")
    print("[OK] Topic successfully removed from topics.txt", flush=True)


def get_random_promo_link() -> str:
    print("[STEP] Reading links.txt...", flush=True)
    links_file = Path("links.txt")
    if not links_file.exists():
        raise FileNotFoundError("❌ 'links.txt' file nahi mila.")
    
    with links_file.open("r", encoding="utf-8") as f:
        links = [line.strip() for line in f if line.strip()]
        
    if not links:
        raise ValueError("❌ 'links.txt' khali hai. Promotion ke liye koi link nahi mila.")
    
    selected_link = random.choice(links)
    print(f"[OK] Randomly selected promo link: '{selected_link}'", flush=True)
    return selected_link


# =========================
# MAIN
# =========================
def run():
    print("[START] Script started", flush=True)

    # File init/clear at the beginning
    article_file = Path("article.json")

    # ============================================
    # NEW: CHECK IF CONTENT IS ALREADY POSTED
    # ============================================
    if article_file.exists():
        try:
            with article_file.open("r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:  # Check if file is not empty
                    data = json.loads(content)
                    if data.get("posted") is True:
                        print("[INFO] Content has already been posted. Aborting process.", flush=True)
                        sys.exit(0)
        except json.JSONDecodeError:
            # Agar JSON invalid hai toh ignore karke aage badhenge taaki nayi file overwrite ho sake
            print("[WARNING] 'article.json' contains invalid JSON. Proceeding to overwrite...", flush=True)
    
    with article_file.open("w", encoding="utf-8") as f:
        f.write("")
    print("[OK] 'article.json' cleared/initialized", flush=True)

    # Get topic and promo link
    try:
        topic = get_last_topic()
        promo_link = get_random_promo_link()
    except Exception as e:
        print(f"[ERROR] Configurations files read karne me dikkat aayi: {e}", flush=True)
        sys.exit(1)

    cookies = load_cookies(Path(CHATGPT_COOKIES_FILE))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    # =========================
    # STEALTH SETUP & LOGIN
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
        page.goto(
            "https://chatgpt.com/",
            wait_until="load"
        )
        print("[OK] URL opened successfully (Logged In)", flush=True)

        # 15 to 30 seconds random wait after page load
        custom_random_wait(30, 60)

        # ============================================
        # NEW: CHECK LOGIN SUCCESS VIA USER PROFILE BUTTON
        # ============================================
        print("[STEP] Checking login success via profile button...", flush=True)
        # Matches any user name followed by 'Free, open'
        profile_button = page.get_by_role('button', name=list(map(lambda x: x.compile(r'.*Free, open'), [__import__('re')]))[0])
        
        if profile_button.count() > 0:
            print(f"[OK] LOGIN SUCCESS: Profile button found -> '{profile_button.first.get_attribute('aria-label') or 'User Account'}'", flush=True)
        else:
            print("[WARNING] Profile button not detected directly, proceeding with caution...", flush=True)

        # =========================
        # AUTOMATION FLOW
        # =========================
        print("[STEP] Locating chat textbox...", flush=True)
        
        # Fallback Strategy for Textbox Locators
        textbox = page.get_by_role('textbox', name='Chat with ChatGPT')
        
        if textbox.count() == 0:
            print("[INFO] Fallback 1: Searching for 'Ask anything' paragraph inside textbox context...", flush=True)
            textbox = page.locator('div[contenteditable="true"]').filter(has=page.locator('p', has_text='Ask anything')).first
            
        if textbox.count() == 0:
            print("[INFO] Fallback 2: Searching via CSS Selector '#prompt-textarea'...", flush=True)
            textbox = page.locator('#prompt-textarea')

        # Trigger action if found
        if textbox.count() > 0:
            textbox.first.click()
            print("[OK] Textbox located and clicked successfully.", flush=True)
        else:
            raise RuntimeError("❌ Textbox locator load nahi ho paya (All strategies failed).")
            
        custom_random_wait(15, 30)

        # Smart prompt engineering with specific separate paragraph format requirements
        prompt = (
            f"IMPORTANT: Your entire response must be wrapped in a single ```json code block. "
            f"STRICTLY: Do not print any JSON outside of a code block under any circumstances. "
            f"Do not add any text, explanation, or markdown before or after the code block.\n\n"
            f"Write a highly engaging article for Substack on the topic: '{topic}'.\n"
            f"Length: 800-1200 words.\n\n"

            f"CRITICAL PROMOTION REQUIREMENT:\n"
            f"You must include a dedicated, standalone paragraph key exactly named \"p_cta\" placed at a natural narrative transition point — roughly in the middle of the article.\n"
            f"The paragraph IMMEDIATELY BEFORE \"p_cta\" must end on a curiosity-triggering note: it should tease a deeper insight, hint at a practical solution, or raise a compelling question that makes the reader feel they need more.\n"
            f"The \"p_cta\" paragraph itself must feel like a natural continuation of that curiosity — not a commercial break. It should directly echo the specific language or idea from the preceding paragraph, making the connection feel precise and inevitable. Never use generic reassuring phrases like 'you're not alone' or 'many people feel this way' — the bridge must feel sharp and specific, not comforting.\n"
            f"The value of \"p_cta\" MUST be exactly formatted in this clean structure:\n"
            f"[1-2 sentences bridging from the preceding paragraph's curiosity hook.] Click Here to Download This Ebook: {promo_link}\n\n"

            f"OUTPUT FORMATTING:\n"
            f"You MUST deliver the entire article strictly inside a single JSON code block. No conversational text or markdown outside of it.\n"
            f"The JSON structure must match this layout exactly (the 'p_cta' key position is flexible but must be placed naturally between your content paragraphs):\n"
            f"{{\n"
            f'  "title": "{topic}",\n'
            f'  "subtitle": "A compelling subtitle that expands on the title and draws the reader in...",\n'
            f'  "p1": "Paragraph 1 content...",\n'
            f'  "p2": "Paragraph 2 content...",\n'
            f'  ... \n'
            f'  "p_cta": "[Bridge sentence(s) continuing from the preceding paragraph.] Click Here to Download This Ebook: {promo_link}",\n'
            f'  ... \n'
            f'  "pn": "Paragraph n content...",\n'
            f'  "conclusion": "Conclusion content...",\n'
            f'  "keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"]\n'
            f"}}\n"

            f"REMINDER 1: The 'title' value must match exactly with: '{topic}'.\n"
            f"REMINDER 2: The 'keywords' array MUST contain strictly exactly 5 relevant keywords.\n"
            f"REMINDER 3: Place 'p_cta' dynamically where it fits the narrative — not forced after a fixed paragraph number.\n"
            f"REMINDER 4: The paragraph before 'p_cta' must end with a curiosity hook. The 'p_cta' must open with 1-2 bridge sentences that directly echo the specific idea or language of that hook — never use generic filler phrases.\n"
            f"REMINDER 5: The 'subtitle' must be a single sentence that complements the title and entices the reader to continue.\n"
        )

        print("[STEP] Entering prompt into textbox...", flush=True)
        textbox.first.fill(prompt)
        custom_random_wait(15, 30)

        print("[STEP] Locating and clicking send button...", flush=True)
        send_button = page.get_by_test_id('send-button')
        send_button.click()
        
        # Initial wait taaki generation properly start ho sake
        custom_random_wait(30, 60)

        # ============================================
        # STABLE 15-SECOND POLLING LIVE STREAM CHECK
        # ============================================
        print("[STEP] Waiting for generated JSON code block to complete writing (15s checks)...", flush=True)
        code_block_locator = page.locator('#code-block-viewer pre')
        
        json_content = None
        for attempt in range(1, 6):
            print(f"[STEP] Checking code block locator (Attempt {attempt}/5)...", flush=True)
            
            if code_block_locator.count() > 0:
                print("[OK] Code block visible, parsing live text size variations...", flush=True)
                
                last_length = 0
                max_check_cycles = 15  # 15 cycles * 15 seconds = Lagbhag 3.7 minutes max wait per attempt
                
                for cycle in range(max_check_cycles):
                    # 15 seconds ka explicit sleep har state capture ke beech me
                    time.sleep(15)
                    
                    current_text = code_block_locator.first.inner_text().strip()
                    current_length = len(current_text)
                    
                    print(f"[STREAM INFO] Cycle {cycle+1}: Previous Length = {last_length}, Current Length = {current_length}", flush=True)
                    
                    # Agar text pichle 15 seconds me 1 char bhi nahi badha aur text khali nahi hai
                    if current_length > 0 and current_length == last_length:
                        # Check text ki end JSON complete bracket `}` par ho rahi hai ya nahi
                        if current_text.endswith("}"):
                            json_content = current_text
                            print("[OK] Content generation is fully finished and finalized.", flush=True)
                            break
                        else:
                            print("[WARNING] Text generation paused but JSON bracket '}' is missing. Waiting further...", flush=True)
                        
                    last_length = current_length
                
                if json_content:
                    break
            
            if attempt < 5:
                print(f"[WARNING] Code block completely write nahi hua ya block mila nahi. Next retry window...", flush=True)
                custom_random_wait(30, 60)
            else:
                print("❌ Max retries reached. Streaming complete nahi ho payi. Exiting script...", flush=True)
                try:
                    browser.close()
                except:
                    pass
                sys.exit(1)

        # JSON parsing, validation and Topic Cleaning
        if json_content:
            try:
                print("[STEP] Parsing content as JSON...", flush=True)
                if json_content.startswith("```json"):
                    json_content = json_content.split("```json", 1)[1]
                if json_content.endswith("```"):
                    json_content = json_content.rsplit("```", 1)[0]
                
                parsed_json = json.loads(json_content.strip())
                
                # Title sync check
                parsed_json["title"] = topic
                
                print("[STEP] Saving to article.json...", flush=True)
                with article_file.open("w", encoding="utf-8") as f:
                    json.dump(parsed_json, f, indent=4, ensure_ascii=False)
                print("[OK] Article successfully saved with embedded promo link to article.json", flush=True)
                
                # Success validation achieved: Safe to remove topic now
                remove_last_topic_from_file()
                
            except json.JSONDecodeError as je:
                print(f"[ERROR] Content JSON parse karne me fail hua: {je}. Exiting script...", flush=True)
                try:
                    browser.close()
                except:
                    pass
                sys.exit(1)
        else:
            print("[ERROR] Save skip kiya gaya kyunki koi data fetch nahi hua. Exiting script...", flush=True)
            try:
                browser.close()
            except:
                pass
            sys.exit(1)

        # 15 to 30 seconds random wait before closing the browser normally
        print("[STEP] Performing random wait before normal browser closure...", flush=True)
        custom_random_wait(15, 30)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR]", e, flush=True)
        # ============================================
        # NEW: CAPTURE SCREENSHOT ON ERROR
        # ============================================
        if 'page' in locals() and page:
            try:
                screenshot_path = "error_screenshot.png"
                page.screenshot(path=screenshot_path, full_page=True)
                print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
            except Exception as screenshot_err:
                print(f"[WARNING] Could not capture screenshot: {screenshot_err}", flush=True)
        # ============================================
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

        print("[DONE] Script finished", flush=True)


if __name__ == "__main__":
    run()