import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from common.config_loader import get
from common.screenshot import capture_error_screenshot

ROOT_DIR = Path(__file__).resolve().parents[1]
LOCATORS_DIR = ROOT_DIR / get("locators_dir", "locators")
JSON_DIR = ROOT_DIR / get("json_dir", "json")
CHANGED_LOCATORS_FILE = LOCATORS_DIR / "changed_locators.json"

_NAME_PATTERN = re.compile(r"^([A-Za-z_][\w]*)\s*=\s*(.+)$")


def _locator_file(platform: str) -> Path:
    return LOCATORS_DIR / f"{platform}_locators.txt"


def load_locator_entries(platform: str, locator_name: str) -> List[str]:
    """Return Playwright expressions for a locator name, in file order.

    The first entry is the primary locator; subsequent same-named entries are
    fallbacks. All expressions are checked immediately (no timeout waiting);
    the first match wins.
    """
    file_path = _locator_file(platform)
    if not file_path.exists():
        return []

    expressions: List[str] = []
    with file_path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = _NAME_PATTERN.match(line)
            if not match:
                continue
            name, expr = match.group(1), match.group(2).strip()
            if name == locator_name:
                expressions.append(expr)
    return expressions


def resolve_locator(page: Any, platform: str, locator_name: str, description: str = "") -> Any:
    """Resolve a locator by checking primary + fallbacks immediately.

    Returns the first Playwright locator that matches (count > 0), or None when
    every strategy fails. No timeouts are waited on; the next fallback is
    checked straight away.
    """
    expressions = load_locator_entries(platform, locator_name)
    for expr in expressions:
        try:
            locator = eval(expr, {"page": page, "re": re})
            if locator is None:
                continue
            if locator.count() > 0:
                print(f"[OK] Locator resolved: {locator_name} ({description})", flush=True)
                return locator
        except Exception:
            continue

    print(f"[WARNING] All locator strategies failed for '{locator_name}' ({description})", flush=True)
    return None


# ---------------------------------------------------------------------------
# Changed locators (auto-correct) handling
# ---------------------------------------------------------------------------


def _load_changed_locators() -> List[Dict[str, Any]]:
    if not CHANGED_LOCATORS_FILE.exists():
        return []
    try:
        with CHANGED_LOCATORS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _save_changed_locators(entries: List[Dict[str, Any]]) -> None:
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    with CHANGED_LOCATORS_FILE.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def _selector_starts_with_hash(suggested_locator: Optional[str]) -> bool:
    if not suggested_locator:
        return False
    inner = suggested_locator
    for prefix in ("page.locator(", "page.get_by_role(", "page.get_by_test_id(", "page.get_by_text("):
        if inner.startswith(prefix):
            inner = inner[len(prefix):].lstrip("'\"")
            break
    return inner.startswith("#")


def record_changed_locator(
    broke_locator: str,
    suggested_locator: Optional[str],
    platform: str,
    locator_name: str,
) -> None:
    """Record a broken locator and its vision-suggested replacement.

    Called in the middle of a run as soon as vision produces a suggestion.
    Entries starting with '#' are never stored.
    """
    if not suggested_locator or _selector_starts_with_hash(suggested_locator):
        return

    entries = _load_changed_locators()
    for entry in entries:
        if entry.get("broke_locator") == broke_locator:
            entry["suggested_locator"] = suggested_locator
            entry["platform"] = platform
            entry["locator_name"] = locator_name
            _save_changed_locators(entries)
            return

    entries.append(
        {
            "broke_locator": broke_locator,
            "suggested_locator": suggested_locator,
            "platform": platform,
            "locator_name": locator_name,
        }
    )
    _save_changed_locators(entries)


def _suggested_locator_in_file(platform: str, suggested_locator: str) -> bool:
    file_path = _locator_file(platform)
    if not file_path.exists():
        return False
    with file_path.open("r", encoding="utf-8") as f:
        return suggested_locator in f.read()


def _insert_after_primary(platform: str, locator_name: str, suggested_locator: str) -> bool:
    """Insert suggested_locator as a fallback right after the primary entry."""
    file_path = _locator_file(platform)
    if not file_path.exists():
        return False

    with file_path.open("r", encoding="utf-8") as f:
        lines = f.readlines()

    primary_index = None
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        match = _NAME_PATTERN.match(stripped)
        if match and match.group(1) == locator_name:
            primary_index = idx
            break

    new_line = f"{locator_name} = {suggested_locator}\n"
    if primary_index is not None:
        lines.insert(primary_index + 1, new_line)
    else:
        lines.append(new_line)

    with file_path.open("w", encoding="utf-8") as f:
        f.writelines(lines)
    return True


def apply_auto_correct() -> None:
    """Process changed_locators.json at the start of a run.

    If auto_correct_locator is on: append each suggested locator as a fallback
    below its primary locator in the corresponding locator txt, then remove the
    entry from changed_locators.json.
    If off: remove an entry only when its suggested locator is already present
    in the locator txt; otherwise keep it.
    """
    from common.config_loader import get as _get

    auto_correct = bool(_get("auto_correct_locator", False))
    entries = _load_changed_locators()
    if not entries:
        return

    remaining: List[Dict[str, Any]] = []

    for entry in entries:
        platform = entry.get("platform")
        locator_name = entry.get("locator_name")
        suggested = entry.get("suggested_locator")
        broke = entry.get("broke_locator")

        if not platform or not locator_name or not suggested:
            remaining.append(entry)
            continue

        if auto_correct:
            _insert_after_primary(platform, locator_name, suggested)
            # Entry consumed: do not keep it in changed_locators.json
            print(
                f"[AUTO-CORRECT] Appended suggested locator for '{locator_name}' "
                f"to {platform}_locators.txt",
                flush=True,
            )
        else:
            if _suggested_locator_in_file(platform, suggested):
                print(
                    f"[AUTO-CORRECT OFF] Suggested locator already present for "
                    f"'{locator_name}'; removing from changed_locators.json",
                    flush=True,
                )
            else:
                remaining.append(entry)

    _save_changed_locators(remaining)


# ---------------------------------------------------------------------------
# Gemini vision fallback
# ---------------------------------------------------------------------------


def _gemini_client():
    model_name = get("gemini_model", "gemini-3.5-flash-lite")
    api_key = os.getenv(get("gemini_api_key_env", "GEMINI_API_KEY"))
    if not api_key:
        print("[WARNING] GEMINI_API_KEY not set in environment/.env; vision fallback disabled", flush=True)
        return None, None
    client = genai.Client(api_key=api_key)
    return client, model_name


def _query_gemini_vision(screenshot_path: str, platform: str, locator_name: str, description: str) -> Optional[Dict[str, Any]]:
    """Ask Gemini Vision to suggest a Playwright locator + click coordinates.

    Returns a dict with suggested_locator (page.locator('...')) and x/y, or None.
    Selectors starting with '#' are rejected.
    """
    client, model_name = _gemini_client()
    if client is None:
        return None

    try:
        with open(screenshot_path, "rb") as f:
            image_bytes = f.read()
    except Exception as e:
        print(f"[WARNING] Could not load screenshot for vision: {e}", flush=True)
        return None

    prompt = (
        "You are a Playwright automation assistant. Given a screenshot of a webpage and a description of a UI "
        "element that could not be located, suggest a single Playwright Python locator expression to find that "
        "element and approximate click coordinates (x, y) in pixels on the screenshot.\n\n"
        f"Platform: {platform}\n"
        f"Locator name: {locator_name}\n"
        f"Element description: {description}\n\n"
        "Respond ONLY with a JSON object: {\"suggested_locator\": \"page.locator('CSS_SELECTOR')\", \"x\": 123, \"y\": 456}\n"
        "Rules:\n"
        "- suggested_locator must be a Python expression starting with page.locator(\n"
        "- Do NOT use a CSS selector that starts with '#'\n"
        "- x and y must be integers\n"
        "- If you cannot determine the element, return {\"suggested_locator\": null, \"x\": null, \"y\": null}"
    )

    image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/png")

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=[prompt, image_part],
        )
        text = response.text or ""
    except Exception as e:
        print(f"[WARNING] Gemini vision request failed: {e}", flush=True)
        return None

    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end <= start:
            return None
        data = json.loads(text[start:end])
    except Exception:
        return None

    suggested = data.get("suggested_locator")
    if not suggested or _selector_starts_with_hash(suggested):
        return None

    return {
        "suggested_locator": suggested,
        "x": data.get("x"),
        "y": data.get("y"),
    }


def gemini_vision_pass(
    page: Any,
    platform: str,
    locator_name: str,
    broke_expression: str,
    description: str = "",
) -> Optional[str]:
    """Use Gemini Vision to pass the current run when a locator fails.

    Captures a screenshot, asks Gemini for a suggested locator and coordinates,
    clicks the coordinates to get past the step in the current run, and records
    the suggested locator in changed_locators.json for the next run.

    The vision-suggested locator is NOT used as a Playwright locator in the
    current run. Returns the suggested_locator string, or None.
    """
    screenshot_path = capture_error_screenshot(page)
    if not screenshot_path:
        return None

    result = _query_gemini_vision(screenshot_path, platform, locator_name, description)
    if not result:
        return None

    suggested = result.get("suggested_locator")
    x, y = result.get("x"), result.get("y")

    # Click coordinates to pass the current run (do not adopt the locator now)
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        try:
            page.mouse.click(float(x), float(y))
            print(f"[VISION] Clicked at ({x}, {y}) to pass '{locator_name}' in current run", flush=True)
        except Exception as e:
            print(f"[WARNING] Vision click failed: {e}", flush=True)

    record_changed_locator(broke_expression, suggested, platform, locator_name)
    return suggested

