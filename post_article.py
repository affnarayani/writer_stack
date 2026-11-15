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
SUBSTACK_URL = get("substack_url", "https://substack.com/")
PBKDF2_ITERATIONS = get("pbkdf2_iterations", 200000)
MIN_RANDOM_WAIT = get("min_random_wait", 15)
MAX_RANDOM_WAIT = get("max_random_wait", 30)
MIN_RANDOM_WAIT_LONG = get("min_random_wait_long", 30)
MAX_RANDOM_WAIT_LONG = get("max_random_wait_long", 60)
MIN_RANDOM_WAIT_SHORT = get("min_random_wait_short", 3)
MAX_RANDOM_WAIT_SHORT = get("max_random_wait_short", 5)

STACK_COOKIES_FILE = Path(get("stack_cookies_file", "substack_cookies/stack_cookies.json.encrypted"))
ARTICLE_FILE = Path("json/article.json")
IMAGE_PATH = Path("image/pin.png")

PLATFORM = "substack"


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
# DATA LOADER & UPDATER
# =========================
def load_article_data(file_path: Path) -> Dict[str, Any]:
    print(f"[STEP] Reading article content from {file_path}...", flush=True)
    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def mark_article_as_posted(file_path: Path):
    print(f"[STEP] Updating {file_path} with status 'posted': true...", flush=True)
    try:
        with file_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if "posted" in data:
            del data["posted"]

        data["posted"] = True

        with file_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        print(f"[SUCCESS] {file_path} updated successfully. Status logged.", flush=True)
    except Exception as e:
        print(f"[WARNING] Failed to write status update to file: {e}", flush=True)


# =========================
# MAIN
# =========================
def run():
    apply_auto_correct()

    print("[START] Script started", flush=True)

    if not IMAGE_PATH.exists():
        print(f"[ERROR] Required image file not found at: {IMAGE_PATH}. Exiting process.", flush=True)
        sys.exit(1)

    cookies = load_cookies(STACK_COOKIES_FILE)
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

        print("[STEP] Opening Substack URL to check login...", flush=True)
        page.goto(SUBSTACK_URL, wait_until="load")
        custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)

        # 1. Login Verification via Profile Button
        print("[STEP] Checking if Profile button exists...", flush=True)
        profile_btn = resolve_locator(page, PLATFORM, "substack_profile_button", "Substack profile button")
        if profile_btn:
            try:
                profile_btn.first.wait_for(state="visible", timeout=60000)
            except Exception:
                pass
            print("[OK] Profile button found! Login Successful.", flush=True)
        else:
            print("[WARNING] Profile button not found, proceeding with caution...", flush=True)
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        # 2. Navigate to Publish Post URL
        post_url = "https://mindtobetter.substack.com/publish/post/"
        print(f"[STEP] Navigating to editor URL: {post_url}", flush=True)
        page.goto(post_url, wait_until="load")
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        # =========================
        # EDITOR WORKFLOW
        # =========================

        # Title Input
        if "title" in article_data:
            print("[STEP] Entering Title...", flush=True)
            title_box = resolve_locator(page, PLATFORM, "substack_post_title", "Post title input")
            if title_box is None or title_box.count() == 0:
                suggested = gemini_vision_pass(page, PLATFORM, "substack_post_title",
                    "page.get_by_test_id('post-title')",
                    "Locating post title input")
                print(f"[INFO] Vision pass for title box: {suggested}", flush=True)
                if title_box is None:
                    raise RuntimeError("Title input locator could not be loaded (All strategies failed).")
            title_box.first.wait_for(state="visible")
            title_box.first.click()
            title_box.first.fill(article_data["title"])
            print("[OK] Title entered", flush=True)
            custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        # Subtitle Input
        if "subtitle" in article_data:
            print("[STEP] Entering Subtitle...", flush=True)
            subtitle_box = resolve_locator(page, PLATFORM, "substack_subtitle", "Subtitle input")
            if subtitle_box is None or subtitle_box.count() == 0:
                suggested = gemini_vision_pass(page, PLATFORM, "substack_subtitle",
                    "page.get_by_role('textbox', name='Add a subtitle…')",
                    "Locating subtitle input")
                print(f"[INFO] Vision pass for subtitle box: {suggested}", flush=True)
                if subtitle_box is None:
                    raise RuntimeError("Subtitle input locator could not be loaded (All strategies failed).")
            subtitle_box.first.wait_for(state="visible")
            subtitle_box.first.click()
            subtitle_box.first.fill(article_data["subtitle"])
            print("[OK] Subtitle entered", flush=True)
            custom_random_wait(4, 8)

        page.keyboard.press("Enter")
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        # Image Upload Flow via Dropdown
        print("[STEP] Clicking Media Attach (Image) button...", flush=True)
        media_btn = resolve_locator(page, PLATFORM, "substack_media_image_button", "Image attach button")
        if media_btn is None or media_btn.count() == 0:
            suggested = gemini_vision_pass(page, PLATFORM, "substack_media_image_button",
                "page.get_by_role('button', name='Image')",
                "Locating image attach button")
            print(f"[INFO] Vision pass for media button: {suggested}", flush=True)
            if media_btn is None:
                raise RuntimeError("Image attach button locator could not be loaded (All strategies failed).")
        media_btn.first.wait_for(state="visible")
        media_btn.first.click()
        custom_random_wait(2, 4)

        print("[STEP] Triggering file chooser via dropdown option and uploading image...", flush=True)
        dropdown_item = resolve_locator(page, PLATFORM, "substack_dropdown_image_item", "Dropdown image menuitem")
        if dropdown_item is None or dropdown_item.count() == 0:
            suggested = gemini_vision_pass(page, PLATFORM, "substack_dropdown_image_item",
                "page.get_by_role('menuitem', name='Image', exact=True)",
                "Locating dropdown image menuitem")
            print(f"[INFO] Vision pass for dropdown item: {suggested}", flush=True)
            if dropdown_item is None:
                raise RuntimeError("Dropdown image menuitem locator could not be loaded (All strategies failed).")
        dropdown_item.first.wait_for(state="visible")

        with page.expect_file_chooser() as fc_info:
            dropdown_item.first.click()

        file_chooser = fc_info.value
        file_chooser.set_files(str(IMAGE_PATH))
        print("[OK] Image attached successfully", flush=True)
        custom_random_wait(6, 12)

        # Body Paragraphs Typing Flow
        for key in content_keys:
            para_text = article_data[key]
            if not para_text.strip():
                continue

            print(f"[STEP] Processing node: {key}...", flush=True)

            # Click into the current (new) paragraph to focus cursor there
            para_locator = page.get_by_role("paragraph").last
            para_locator.wait_for(state="visible")
            para_locator.click()

            # Special Link Parsing workflow for p_cta
            if key == "p_cta" and "http" in para_text:
                print("[STEP] Precision link targeting active for p_cta (Standard speed)", flush=True)

                # Extract URL from multiple formats:
                #   [text](url), [url], (url), plain url
                target_url = ""

                # Pattern 1: [display_text](url) - markdown link
                md_match = re.search(r'\[([^\]]*)\]\s*\(([^)]+)\)', para_text)
                if md_match:
                    target_url = md_match.group(2).strip().strip('"').strip("'")
                    display_text = md_match.group(1).strip()
                    full_body = para_text[:md_match.start()].strip().rstrip(':').strip()

                    if display_text.startswith("http"):
                        # Markdown is [url](url) — anchor text is the last sentence of full_body
                        if ". " in full_body:
                            anchor_text = full_body.rsplit(". ", 1)[-1].strip()
                        elif ": " in full_body:
                            anchor_text = full_body.rsplit(": ", 1)[-1].strip()
                        else:
                            anchor_text = display_text
                    else:
                        # Markdown is [anchor_text](url)
                        anchor_text = display_text
                else:
                    # Pattern 2: [url] - bracketed url
                    bracket_match = re.search(r'\[(https?://[^\]]+)\]', para_text)
                    if bracket_match:
                        target_url = bracket_match.group(1).strip().strip('"').strip("'")
                        full_body = para_text[:bracket_match.start()].strip()
                        anchor_text = ""
                    else:
                        # Pattern 3: (url) - parenthesized url
                        paren_match = re.search(r'\((https?://[^)]+)\)', para_text)
                        if paren_match:
                            target_url = paren_match.group(1).strip().strip('"').strip("'")
                            full_body = para_text[:paren_match.start()].strip()
                            anchor_text = ""
                        else:
                            # Pattern 4: plain url
                            url_match = re.search(r'(https?://[^\s\)"]+)', para_text)
                            if url_match:
                                target_url = url_match.group(1).strip().strip('"').strip("'")
                                full_body = para_text[:url_match.start()].strip()
                                anchor_text = ""

                # Clean body text - remove trailing colon, quotes, whitespace
                full_body = full_body.rstrip(':').strip().strip('"').strip("'")
                anchor_text = anchor_text.rstrip(':').strip().strip('"').strip("'")

                if not target_url:
                    print("[WARNING] No URL found in p_cta, filling full text as fallback", flush=True)
                    para_locator.first.fill(para_text)
                    custom_random_wait(4, 8)
                    page.keyboard.press("Enter")
                    custom_random_wait(3, 6)
                else:
                    if not anchor_text:
                        # No anchor_text extracted (non-markdown format), fill all and select from end
                        para_locator.first.fill(full_body)
                        custom_random_wait(2, 4)
                        print(f"[STEP] Selecting last {len(full_body)} chars as anchor", flush=True)
                        page.keyboard.down("Shift")
                        for _ in range(len(full_body)):
                            page.keyboard.press("ArrowLeft")
                            time.sleep(0.02)
                        page.keyboard.up("Shift")
                        custom_random_wait(1, 2)
                    else:
                        # Split full_body into: pre_anchor + anchor_text + trailing
                        anchor_idx = full_body.rfind(anchor_text)
                        pre_anchor = full_body[:anchor_idx]
                        trailing = full_body[anchor_idx + len(anchor_text):]

                        # Step 1: Fill text before anchor_text
                        para_locator.first.fill(pre_anchor)
                        custom_random_wait(1, 2)

                        # Step 2: Type anchor_text (cursor now at end of pre_anchor + anchor_text)
                        print(f"[STEP] Typing anchor text: '{anchor_text}'", flush=True)
                        for char in anchor_text:
                            page.keyboard.type(char)
                            time.sleep(random.uniform(0.005, 0.02))

                        # Step 3: Type trailing chars (e.g. ": ")
                        for char in trailing:
                            page.keyboard.type(char)
                            time.sleep(0.01)

                        custom_random_wait(1, 2)

                        # Step 4: Select anchor_text with Shift+ArrowLeft
                        print(f"[STEP] Selecting anchor text: '{anchor_text}'", flush=True)
                        page.keyboard.down("Shift")
                        for _ in range(len(anchor_text)):
                            page.keyboard.press("ArrowLeft")
                            time.sleep(0.02)
                        page.keyboard.up("Shift")
                        custom_random_wait(1, 2)

                    print("[STEP] Triggering Link toolbar button...", flush=True)
                    link_btn = resolve_locator(page, PLATFORM, "substack_link_button", "Link toolbar button")
                    if link_btn is None or link_btn.count() == 0:
                        suggested = gemini_vision_pass(page, PLATFORM, "substack_link_button",
                            "page.get_by_role('button', name='Link')",
                            "Locating link toolbar button")
                        print(f"[INFO] Vision pass for link button: {suggested}", flush=True)
                        if link_btn is None:
                            raise RuntimeError("Link toolbar button locator could not be loaded (All strategies failed).")
                    link_btn.first.wait_for(state="visible")
                    link_btn.first.click()
                    custom_random_wait(2, 4)

                    print("[STEP] Filling URL textbox...", flush=True)
                    url_input = resolve_locator(page, PLATFORM, "substack_url_input", "URL input textbox")
                    if url_input is None or url_input.count() == 0:
                        suggested = gemini_vision_pass(page, PLATFORM, "substack_url_input",
                            "page.get_by_role('textbox', name='Enter URL...')",
                            "Locating URL input textbox")
                        print(f"[INFO] Vision pass for url input: {suggested}", flush=True)
                        if url_input is None:
                            raise RuntimeError("URL input textbox locator could not be loaded (All strategies failed).")
                    url_input.first.wait_for(state="visible")
                    url_input.first.fill(target_url)
                    custom_random_wait(2, 4)

                    print("[STEP] Confirming hyperlink creation...", flush=True)
                    confirm_link = resolve_locator(page, PLATFORM, "substack_confirm_link_text", "Confirm link text")
                    if confirm_link is None or confirm_link.count() == 0:
                        suggested = gemini_vision_pass(page, PLATFORM, "substack_confirm_link_text",
                            "page.get_by_text('Link', exact=True)",
                            "Locating confirm link text")
                        print(f"[INFO] Vision pass for confirm link: {suggested}", flush=True)
                        if confirm_link is None:
                            raise RuntimeError("Confirm link text locator could not be loaded (All strategies failed).")
                    confirm_link.first.click()
                    custom_random_wait(3, 5)

                    print("[STEP] Navigating out of selection and breaking line...", flush=True)
                    page.keyboard.press("ArrowRight")
                    time.sleep(0.5)
                    page.keyboard.press("Enter")
                    custom_random_wait(4, 8)

            elif key == "conclusion":
                print("[STEP] Styling Heading 3 block for Conclusion header...", flush=True)

                style_picker = resolve_locator(page, PLATFORM, "substack_style_picker", "Style picker")
                if style_picker is None or style_picker.count() == 0:
                    suggested = gemini_vision_pass(page, PLATFORM, "substack_style_picker",
                        "page.get_by_test_id('style-picker')",
                        "Locating style picker")
                    print(f"[INFO] Vision pass for style picker: {suggested}", flush=True)
                    if style_picker is None:
                        raise RuntimeError("Style picker locator could not be loaded (All strategies failed).")
                style_picker.first.wait_for(state="visible")
                style_picker.first.click()
                custom_random_wait(1, 3)

                heading_item = resolve_locator(page, PLATFORM, "substack_heading3_item", "Heading 3 menuitem")
                if heading_item is None or heading_item.count() == 0:
                    suggested = gemini_vision_pass(page, PLATFORM, "substack_heading3_item",
                        "page.get_by_role('menuitem', name='Heading 3')",
                        "Locating Heading 3 menuitem")
                    print(f"[INFO] Vision pass for heading3: {suggested}", flush=True)
                    if heading_item is None:
                        raise RuntimeError("Heading 3 menuitem locator could not be loaded (All strategies failed).")
                heading_item.first.wait_for(state="visible")
                heading_item.first.click()
                custom_random_wait(1, 3)

                bold_btn = resolve_locator(page, PLATFORM, "substack_bold_button", "Bold button")
                if bold_btn is None or bold_btn.count() == 0:
                    suggested = gemini_vision_pass(page, PLATFORM, "substack_bold_button",
                        "page.get_by_role('button', name='Bold')",
                        "Locating Bold button")
                    print(f"[INFO] Vision pass for bold button: {suggested}", flush=True)
                    if bold_btn is None:
                        raise RuntimeError("Bold button locator could not be loaded (All strategies failed).")
                bold_btn.first.wait_for(state="visible")
                bold_btn.first.click()
                custom_random_wait(1, 3)

                print("[STEP] Typing heading tag: 'Conclusion'...", flush=True)
                page.keyboard.type("Conclusion", delay=0)
                custom_random_wait(2, 4)

                page.keyboard.press("Enter")
                custom_random_wait(2, 4)

                print("[STEP] Re-clicking Bold button to turn off bold formatting...", flush=True)
                if bold_btn is None or bold_btn.count() == 0:
                    suggested = gemini_vision_pass(page, PLATFORM, "substack_bold_button",
                        "page.get_by_role('button', name='Bold')",
                        "Re-locating Bold button (turn off)")
                    print(f"[INFO] Vision pass for bold button: {suggested}", flush=True)
                    if bold_btn is None:
                        raise RuntimeError("Bold button locator could not be loaded (All strategies failed).")
                bold_btn.first.wait_for(state="visible")
                bold_btn.first.click()
                custom_random_wait(2, 4)

                print("[STEP] Injecting main conclusion body paragraphs...", flush=True)
                para_locator.first.fill(para_text)

                print("[OK] Conclusion text processed successfully", flush=True)
                custom_random_wait(4, 8)
                page.keyboard.press("Enter")
                custom_random_wait(3, 6)

            else:
                # Use fill() for all paragraphs
                para_locator.first.fill(para_text)

                print(f"[OK] Node ({key}) completed typing", flush=True)
                custom_random_wait(4, 8)

                page.keyboard.press("Enter")
                custom_random_wait(3, 6)

        print("[SUCCESS] All dynamic text contents appended successfully.", flush=True)

        # =========================
        # NEW PUBLISHING FLOW
        # =========================
        print("[STEP] Clicking primary 'Publish' action trigger button...", flush=True)
        primary_publish_btn = resolve_locator(page, PLATFORM, "substack_publish_button", "Publish button")
        if primary_publish_btn is None or primary_publish_btn.count() == 0:
            raise RuntimeError("Publish button locator failed (all strategies failed). Aborting before marking posted.")
        primary_publish_btn.first.wait_for(state="visible")
        primary_publish_btn.first.click()

        intermediate_publish_wait()

        # Handle Keywords Tag Injection Phase
        if chosen_keywords:
            print("[STEP] Locating 'Select or create tags' tags combobox input...", flush=True)
            tags_input = resolve_locator(page, PLATFORM, "substack_tags_combobox", "Tags combobox")
            if tags_input is None or tags_input.count() == 0:
                suggested = gemini_vision_pass(page, PLATFORM, "substack_tags_combobox",
                    "page.get_by_role('combobox', name='Select or create tags')",
                    "Locating tags combobox")
                print(f"[INFO] Vision pass for tags combobox: {suggested}", flush=True)
                if tags_input is None or tags_input.count() == 0:
                    raise RuntimeError("Tags combobox locator could not be loaded (All strategies failed).")

            tags_input.first.wait_for(state="visible")
            tags_input.first.click()
            custom_random_wait(1, 2)

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
        final_send_btn = resolve_locator(page, PLATFORM, "substack_send_button", "Send to everyone now button")
        if final_send_btn is None or final_send_btn.count() == 0:
            raise RuntimeError("Send to everyone now button locator failed (all strategies failed). Aborting before marking posted.")
        final_send_btn.first.wait_for(state="visible")
        final_send_btn.first.click()
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

        print("[DONE] Script execution environment torn down cleanly.", flush=True)


if __name__ == "__main__":
    run()
