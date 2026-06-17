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
# MAIN
# =========================
def run():
    print("[START] Script started", flush=True)

    # =========================
    # STATUS CHECK
    # =========================
    status_file = Path("status.json")
    if not status_file.exists():
        print("[ERROR] status.json file nahi mila. Exiting...", flush=True)
        sys.exit(0)
        
    try:
        with status_file.open("r", encoding="utf-8") as f:
            status_data = json.load(f)
    except Exception as e:
        print(f"[ERROR] status.json parse nahi ho paya: {e}. Exiting...", flush=True)
        sys.exit(0)

    post_found = status_data.get("post_to_comment_found", False)
    comment_gen = status_data.get("comment_generated", False)

    # Condition Check (Pylance typo fixed here)
    if post_found is True and comment_gen is False:
        print("[OK] Status check passed (post_to_comment_found is True & comment_generated is False). Proceeding...", flush=True)
    else:
        if post_found is False:
            print("Comment Not Generated Yet!", flush=True)
        elif comment_gen is True:
            print("Comment already generated!", flush=True)
        sys.exit(0)

    # Target content extract karna prompt ke liye
    post_content = status_data.get("content_of_post_to_comment", "")
    if not post_content:
        print("[ERROR] content_of_post_to_comment khali hai. Exiting...", flush=True)
        sys.exit(0)

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
        # CHECK LOGIN SUCCESS VIA USER PROFILE BUTTON
        # ============================================
        print("[STEP] Checking login success via profile button...", flush=True)
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

        # ============================================
        # PROMPT FOR SUBSTACK NOTES (WITH 50-150 CHAR LIMIT)
        # ============================================
        prompt = (
            f"IMPORTANT: Your entire response must be wrapped in a single ```json code block. "
            f"Do not print any JSON outside of a code block. "
            f"Do not add any text, explanation, or markdown before or after the code block.\n\n"

            f"Read the following Substack Note carefully:\n"
            f"\"\"\"\n{post_content}\n\"\"\"\n\n"

            f"Your task is to write a thoughtful, discussion-worthy comment for this Substack Note.\n\n"

            f"PRIMARY OBJECTIVE:\n"
            f"Write the kind of comment that thoughtful Substack readers naturally like or reply to because it contributes a genuinely new idea.\n\n"

            f"The goal is NOT to:\n"
            f"- Praise the author\n"
            f"- Summarize the note\n"
            f"- Rephrase the author's point\n"
            f"- Sound impressive for its own sake\n\n"

            f"The goal IS to:\n"
            f"- Advance the discussion\n"
            f"- Add a fresh observation\n"
            f"- Introduce a useful distinction\n"
            f"- Surface an implication, tension, paradox, tradeoff, or unanswered question\n"
            f"- Contribute a thought that wasn't already fully stated in the note\n\n"

            f"SILENT ANALYSIS:\n"
            f"Before writing the comment, determine:\n"
            f"1. The note's central claim, insight, question, or argument.\n"
            f"2. The most interesting implication of that claim.\n"
            f"3. A tension, paradox, contradiction, limitation, or unresolved question created by that claim.\n"
            f"4. A distinction that could deepen the discussion.\n\n"

            f"Whenever possible, write from #2, #3, or #4 rather than simply reacting to #1.\n\n"

            f"COMMENTS THAT PERFORM WELL ON SUBSTACK OFTEN:\n"
            f"- Add a second-order implication\n"
            f"- Extend the author's idea into a new area\n"
            f"- Introduce a useful distinction\n"
            f"- Surface a paradox or tension\n"
            f"- Offer a concise counterpoint\n"
            f"- Add context readers may not have considered\n"
            f"- Create a natural opening for discussion\n\n"

            f"COMMENTS THAT UNDERPERFORM OFTEN:\n"
            f"- Repeat the note\n"
            f"- Rephrase the strongest sentence\n"
            f"- Merely signal agreement\n"
            f"- Congratulate the author\n"
            f"- Explain what the note already explained\n\n"

            f"SPECIFICITY RULE:\n"
            f"The comment must clearly connect to a specific idea, phrase, observation, argument, or question in the note.\n"
            f"It should feel difficult to copy-paste under another note without sounding out of place.\n\n"

            f"TONE:\n"
            f"Write like a thoughtful Substack subscriber.\n"
            f"Sound curious, observant, intelligent, conversational, and natural.\n\n"

            f"Do NOT sound like:\n"
            f"- A motivational speaker\n"
            f"- A therapist\n"
            f"- A life coach\n"
            f"- A marketer\n"
            f"- A LinkedIn influencer\n"
            f"- An AI assistant\n"
            f"- A productivity guru\n\n"

            f"HUMANNESS RULE:\n"
            f"The comment should feel like something written by a sharp reader who had one genuinely interesting thought while reading.\n"
            f"Do not sound overly polished or artificially profound.\n"
            f"Do not try to impress.\n"
            f"Try to contribute.\n\n"

            f"ENGAGEMENT RULE:\n"
            f"Comments that create discussion are preferred over comments that merely demonstrate understanding.\n"
            f"When natural, leave room for response from either the author or other readers.\n"
            f"Never use obvious engagement bait.\n\n"

            f"QUESTION RULE:\n"
            f"Questions are optional.\n"
            f"Use a question only if it genuinely deepens the discussion.\n"
            f"Do not add a question merely to increase engagement.\n\n"

            f"ORIGINALITY RULE:\n"
            f"Prioritize:\n"
            f"- New observations\n"
            f"- New implications\n"
            f"- New distinctions\n"
            f"- New tensions\n\n"

            f"Over:\n"
            f"- Agreement\n"
            f"- Validation\n"
            f"- Summary\n"
            f"- Paraphrasing\n\n"

            f"A fresh idea is more valuable than a perfect summary.\n\n"

            f"LENGTH:\n"
            f"Preferred range: 80-220 characters including spaces.\n"
            f"Use fewer words if the thought is complete.\n"
            f"Avoid filler.\n\n"

            f"FORMAT RULES:\n"
            f"- Single continuous line\n"
            f"- No newline characters\n"
            f"- No emojis\n"
            f"- No hashtags\n"
            f"- No markdown\n"
            f"- No bullet points\n"
            f"- No greetings\n"
            f"- No sign-offs\n\n"

            f"AVOID COMMON AI PHRASES:\n"
            f"- This resonates\n"
            f"- You're onto something\n"
            f"- You've captured something important\n"
            f"- Well said\n"
            f"- 100% agree\n"
            f"- Thanks for sharing\n"
            f"- Important reminder\n"
            f"- What stood out to me\n"
            f"- You've nailed it\n"
            f"- You've articulated this perfectly\n"
            f"- Or similar generic praise\n\n"

            f"FINAL QUALITY CHECK:\n"
            f"Silently generate multiple candidate comments.\n"
            f"Select the one that:\n"
            f"- Adds the most value\n"
            f"- Introduces the freshest insight\n"
            f"- Feels most human\n"
            f"- Is most likely to earn thoughtful replies\n"
            f"- Does not merely summarize the note\n\n"

            f"OUTPUT FORMAT — strictly inside a single JSON code block:\n"
            f"{{\n"
            f'  "comment": "Your direct single-line Substack reply here"\n'
            f"}}\n"
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
                max_check_cycles = 15
                
                for cycle in range(max_check_cycles):
                    time.sleep(15)
                    
                    current_text = code_block_locator.first.inner_text().strip()
                    current_length = len(current_text)
                    
                    print(f"[STREAM INFO] Cycle {cycle+1}: Previous Length = {last_length}, Current Length = {current_length}", flush=True)
                    
                    if current_length > 0 and current_length == last_length:
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

        # JSON parsing, validation and Status Sync
        if json_content:
            try:
                print("[STEP] Parsing content as JSON...", flush=True)
                if json_content.startswith("```json"):
                    json_content = json_content.split("```json", 1)[1]
                if json_content.endswith("```"):
                    json_content = json_content.rsplit("```", 1)[0]
                
                parsed_json = json.loads(json_content.strip())
                generated_comment_text = parsed_json.get("comment", "").strip()

                # Double safety: Remove any stray newlines from string
                generated_comment_text = generated_comment_text.replace("\n", " ").replace("\r", "")
                
                # =====================================
                # UPDATE STATUS.JSON ONLY (NO TOPICS.TXT INTERACTION)
                # =====================================
                print("[STEP] Updating status.json with comment data...", flush=True)
                status_data["comment"] = generated_comment_text
                status_data["comment_generated"] = True
                
                with status_file.open("w", encoding="utf-8") as f:
                    json.dump(status_data, f, indent=4, ensure_ascii=False)
                print("[OK] status.json successfully updated (comment appended & comment_generated=True)", flush=True)
                
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
        # CAPTURE SCREENSHOT ON ERROR
        if 'page' in locals() and page:
            try:
                screenshot_path = "error_screenshot.png"
                page.screenshot(path=screenshot_path, full_page=True)
                print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
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

        print("[DONE] Script finished", flush=True)


if __name__ == "__main__":
    run()