import os
import sys
import json
import time
import random
import re
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
PBKDF2_ITERATIONS = get("pbkdf2_iterations", 200000)

COOKIES_DIR = Path("chatgpt_cookies")

ARTICLE_FILE = Path("json/article.json")
NOTES_FILE = Path("json/notes.json")
TOPICS_FILE = Path("data/topics.txt")
LINKS_FILE = Path("data/links.txt")

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
# FILE PARSERS & WRITERS
# =========================
def get_last_topic() -> str:
    print("[STEP] Reading data/topics.txt...", flush=True)
    if not TOPICS_FILE.exists():
        raise FileNotFoundError("'data/topics.txt' file not found.")

    with TOPICS_FILE.open("r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    if not lines:
        raise ValueError("'data/topics.txt' is empty.")

    selected_topic = lines[-1]
    print(f"[OK] Selected last topic: '{selected_topic}'", flush=True)
    return selected_topic


def get_random_promo_link() -> str:
    print("[STEP] Reading data/links.txt...", flush=True)
    if not LINKS_FILE.exists():
        raise FileNotFoundError("'data/links.txt' file not found.")

    with LINKS_FILE.open("r", encoding="utf-8") as f:
        links = [line.strip() for line in f if line.strip()]

    if not links:
        raise ValueError("'data/links.txt' is empty. No promotion link found.")

    selected_link = random.choice(links)
    print(f"[OK] Randomly selected promo link: '{selected_link}'", flush=True)
    return selected_link


# =========================
# MAIN
# =========================
def run():
    apply_auto_correct()

    print("[START] Script started", flush=True)

    notes_file = Path("json/notes.json")

    # Fetch topic, promo link, and article.json data
    try:
        next_article_topic = get_last_topic()
        promo_link = get_random_promo_link()

        if not ARTICLE_FILE.exists():
            raise FileNotFoundError("'json/article.json' file not found.")

        with ARTICLE_FILE.open("r", encoding="utf-8") as f:
            content_str = f.read().strip()
            if not content_str:
                raise ValueError("'json/article.json' is empty.")
            article_data = json.loads(content_str)

        if article_data.get("posted") != True:
            print("The article has not been posted yet. Notes generation skipped. Exiting.", flush=True)
            sys.exit(0)

        current_article_title = article_data.get('title', next_article_topic)

        if notes_file.exists():
            try:
                with notes_file.open("r", encoding="utf-8") as nf:
                    notes_content = nf.read().strip()
                    if notes_content:
                        notes_data = json.loads(notes_content)
                        if notes_data.get("title") == current_article_title:
                            print("[SKIP] json/notes.json already has the same title as json/article.json. Notes generation skipped. Exiting.", flush=True)
                            sys.exit(0)
            except json.JSONDecodeError:
                print("[INFO] json/notes.json exists but is empty or invalid JSON. Proceeding...", flush=True)

        topic = current_article_title

        article = f"""Title: {article_data.get('title', '')}
Subtitle: {article_data.get('subtitle', '')}

Content:
{article_data.get('p1', '')}

CTA/Promo:
{article_data.get('p_cta', '')}

More Content:
{article_data.get('p9', '')}

Conclusion:
{article_data.get('conclusion', '')}

Keywords: {', '.join(article_data.get('keywords', []))}"""

        print("[OK] Input configurations and article data parsed successfully.", flush=True)

        print("\n" + "="*60, flush=True)
        print(f"--- NEXT ARTICLE TOPIC ---\n{next_article_topic}\n", flush=True)
        print(f"--- GENERATED ARTICLE ---\n{article}", flush=True)
        print("="*60 + "\n", flush=True)

    except Exception as e:
        print(f"[ERROR] Error reading config/article files: {e}", flush=True)
        sys.exit(1)

    encrypted_files = list(COOKIES_DIR.glob("*.encrypted"))
    if not encrypted_files:
        raise RuntimeError("No .encrypted cookie files found in 'chatgpt_cookies/' folder")

    CHATGPT_COOKIES_FILE = random.choice(encrypted_files)
    print(f"[OK] Randomly selected cookie file: {CHATGPT_COOKIES_FILE.name}", flush=True)

    cookies = load_cookies_local(Path(CHATGPT_COOKIES_FILE))
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

        print("[STEP] Opening ChatGPT Main URL...", flush=True)
        page.goto(CHATGPT_URL, wait_until="load")
        print("[OK] URL opened successfully (Logged In)", flush=True)

        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

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
        # AUTOMATION FLOW (With Textbox Fallbacks)
        # ============================================
        print("[STEP] Locating chat textbox...", flush=True)
        textbox = resolve_locator(page, PLATFORM, "chatgpt_textbox", "Chat textbox")

        if textbox is None or textbox.count() == 0:
            suggested = gemini_vision_pass(
                page, PLATFORM, "chatgpt_textbox",
                "page.get_by_role('textbox', name='Chat with ChatGPT')",
                "Locating chat textbox",
            )
            print(f"[INFO] Vision pass for textbox: {suggested}", flush=True)
            if textbox is None:
                raise RuntimeError("Textbox locator could not be loaded (All strategies failed).")

        textbox.first.click()
        print("[OK] Textbox located and clicked successfully.", flush=True)

        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        # Smart prompt engineering (UNCHANGED)
        prompt = (
            f"IMPORTANT: Your entire response must be wrapped in a single ```json code block. "
            f"STRICTLY: Do not print any JSON outside of a code block under any circumstances. "
            f"Do not add any text, explanation, or markdown before or after the code block.\n\n"
            f"You are a Substack Notes writer for a mental wellness brand. Based on the article provided, "
            f"write 9 standalone Substack Notes. Each note must feel native to Substack — conversational, "
            f"thought-provoking, and written for a US audience interested in mental wellness.\n\n"

            f"ARTICLE:\n{article}\n\n"

            f"NOTE REQUIREMENTS:\n\n"

            f"Note 1 — Hook:\n"
            f"Write a single punchy note that grabs attention instantly. It must be based on the article's "
            f"opening idea but rewritten to stop the scroll. Use a bold claim, a surprising contrast, or a "
            f"short uncomfortable truth. Maximum 3-4 lines. No fluff. No 'I' statements.\n\n"

            f"Note 2 — Main Problem:\n"
            f"Describe the core problem the article addresses, but frame it as an observation the reader "
            f"will recognize in their own life. Make it feel like you're naming something they've felt but "
            f"never articulated. Write 3-5 lines. Avoid academic tone. Keep it personal and direct.\n\n"

            f"Note 3 — Personal Story:\n"
            f"Write a short first-person story (real or illustrative) that connects to the article's theme. "
            f"It should feel vulnerable and specific — not motivational. End with a single line that pivots "
            f"to the broader insight. 4-6 lines total.\n\n"

            f"Note 4 — Tip in Detail:\n"
            f"Extract the most actionable insight from the article and expand it into a practical, "
            f"standalone tip. Write it as a mini-lesson: state the tip, explain why it works, give one "
            f"concrete example. 5-7 lines. Avoid bullet points — write in flowing prose.\n\n"

            f"Note 5 — Question to Audience:\n"
            f"Write a note that ends with a genuine, open-ended question directed at the reader. The first "
            f"2-3 lines should set up the tension or idea from the article. The question must feel like it "
            f"invites real reflection — not a rhetorical device. Make readers want to answer in the comments.\n\n"

            f"Note 6 — Misconception:\n"
            f"Identify a common belief related to the article's topic that the article challenges. Open with "
            f"'Most people think...' or a similar framing. Then flip it. The contrast must feel surprising "
            f"but logical. 3-5 lines. End with the corrected perspective stated confidently.\n\n"

            f"Note 7 — Restack + Opinion:\n"
            f"Write a note that reads like a strong personal opinion rooted in the article's core argument. "
            f"It should sound like something worth restacking — a perspective people either strongly agree "
            f"with or find thought-provoking enough to share. 2-4 lines. Bold and direct tone.\n\n"

            f"Note 8 — Mini Recap:\n"
            f"Summarize the article's key idea in a way that feels like a takeaway, not a summary. Write it "
            f"as if you're sharing what you personally learned or realized. 3-4 lines. First-person tone. "
            f"End with one line that makes the reader feel the insight is worth remembering.\n\n"

            f"Note 9 — Tease Next Article:\n"
            f"Write a curiosity-building note that teases the upcoming article on this topic: '{next_article_topic}'. "
            f"Connect it naturally to what was just discussed in the current article — make it feel like a "
            f"logical and compelling next step. Do NOT reveal the full angle or argument of the next article. "
            f"Create a sense of 'I need to read that.' 3-4 lines. End with an open loop, not a call to action.\n\n"

            f"OUTPUT FORMATTING:\n"
            f"You MUST deliver all 9 notes strictly inside a single JSON code block. No conversational text "
            f"or markdown outside of it. The JSON structure must match this layout exactly:\n"
            f"{{\n"
            f'  "note1": "Hook note content...",\n'
            f'  "note2": "Main problem note content...",\n'
            f'  "note3": "Personal story note content...",\n'
            f'  "note4": "Tip in detail note content...",\n'
            f'  "note5": "Question to audience note content...",\n'
            f'  "note6": "Misconception note content...",\n'
            f'  "note7": "Restack + opinion note content...",\n'
            f'  "note8": "Mini recap note content...",\n'
            f'  "note9": "Tease next article note content..."\n'
            f"}}\n\n"

            f"REMINDER 1: Every note must be self-contained — a reader who hasn't read the article must "
            f"still find it engaging and meaningful.\n"
            f"REMINDER 2: Do not use hashtags, emojis, or promotional language in any note.\n"
            f"REMINDER 3: Each note must have a distinct voice and structure — avoid repeating the same "
            f"sentence patterns across notes.\n"
            f"REMINDER 4: Note 3 must feel personal and grounded, not inspirational or motivational.\n"
            f"REMINDER 5: Note 9 must tease the next article through implication and curiosity — do NOT "
            f"state the full topic or reveal the article's argument. The reader should feel intrigued, not informed.\n"
        )

        print("[STEP] Entering prompt into textbox...", flush=True)
        textbox.first.fill(prompt)
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        print("[STEP] Locating and clicking send button...", flush=True)
        send_button = resolve_locator(page, PLATFORM, "chatgpt_send_button", "Send button")
        if send_button is None or send_button.count() == 0:
            suggested = gemini_vision_pass(
                page, PLATFORM, "chatgpt_send_button",
                "page.get_by_test_id('send-button')",
                "Locating send button",
            )
            print(f"[INFO] Vision pass for send button: {suggested}", flush=True)
            if send_button is None:
                raise RuntimeError("Send button locator could not be loaded (All strategies failed).")
        send_button.first.click()

        custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)

        # ============================================
        # STABLE POLLING LIVE STREAM CHECK
        # ============================================
        print("[STEP] Waiting for generated JSON code block to complete writing...", flush=True)
        code_block_locator = resolve_locator(page, PLATFORM, "chatgpt_code_block", "JSON code block")

        json_content = None
        for attempt in range(1, 6):
            print(f"[STEP] Checking code block locator (Attempt {attempt}/5)...", flush=True)

            if code_block_locator and code_block_locator.count() > 0:
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
                print(f"[WARNING] Code block not fully written yet. Next retry window...", flush=True)
                custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)
            else:
                print("Max retries reached. Streaming incomplete. Exiting script...", flush=True)
                if page:
                    try:
                        screenshot_path = capture_error_screenshot(page)
                        if screenshot_path:
                            upload_to_onlyfiles(screenshot_path)
                    except Exception as screenshot_err:
                        print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
                try:
                    browser.close()
                except:
                    pass
                sys.exit(1)

        # JSON parsing, validation and Output Saving
        if json_content:
            try:
                print("[STEP] Parsing content as JSON...", flush=True)
                if json_content.startswith("```json"):
                    json_content = json_content.split("```json", 1)[1]
                if json_content.endswith("```"):
                    json_content = json_content.rsplit("```", 1)[0]

                parsed_json = json.loads(json_content.strip())

                processed_json = {}
                for key, value in parsed_json.items():
                    if key == "title":
                        continue

                    if key.startswith("note") and not key.endswith("_posted"):
                        if isinstance(value, str):
                            cleaned_value = re.sub(r',\s*\n|\n\s*,', '', value)
                            cleaned_value = re.sub(r'\n+', '\n', cleaned_value)
                            processed_json[key] = cleaned_value
                        else:
                            processed_json[key] = value

                        processed_json[f"{key}_posted"] = False
                    else:
                        processed_json[key] = value

                final_output = {}
                final_output["title"] = current_article_title
                final_output.update(processed_json)

                print("[STEP] Saving to json/notes.json with manual title injection...", flush=True)
                with NOTES_FILE.open("w", encoding="utf-8") as f:
                    json.dump(final_output, f, indent=4, ensure_ascii=False)
                print("[OK] Notes successfully saved to json/notes.json with posting tags and clean line breaks", flush=True)

            except json.JSONDecodeError as je:
                print(f"[ERROR] Failed to parse content JSON: {je}. Exiting script...", flush=True)
                if page:
                    try:
                        screenshot_path = capture_error_screenshot(page)
                        if screenshot_path:
                            upload_to_onlyfiles(screenshot_path)
                    except Exception as screenshot_err:
                        print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
                try:
                    browser.close()
                except:
                    pass
                sys.exit(1)
        else:
            print("[ERROR] Save skipped because no data was fetched. Exiting script...", flush=True)
            if page:
                try:
                    screenshot_path = capture_error_screenshot(page)
                    if screenshot_path:
                        upload_to_onlyfiles(screenshot_path)
                except Exception as screenshot_err:
                    print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
            try:
                browser.close()
            except:
                pass
            sys.exit(1)

        print("[STEP] Performing random wait before normal browser closure...", flush=True)
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR]", e, flush=True)
        if page:
            try:
                screenshot_path = capture_error_screenshot(page)
                if screenshot_path:
                    upload_to_onlyfiles(screenshot_path)
            except Exception as screenshot_err:
                print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
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
