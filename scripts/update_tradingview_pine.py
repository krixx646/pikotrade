import argparse
import time
import sys
import io
import hashlib
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PINE_FILE = PROJECT_ROOT / "outputs" / "tradingview" / "market_agent_zones.pine"
HASH_FILE = PROJECT_ROOT / "outputs" / "tradingview" / ".pine_hash"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update TradingView Pine Editor with the latest generated script."
    )
    parser.add_argument(
        "--symbol",
        default="EURJPY",
        help="Forex symbol to open (e.g., NZDUSD, EURUSD, GBPJPY, XAUUSD)",
    )
    parser.add_argument(
        "--pine-file",
        default=str(PINE_FILE),
        help="Path to the generated Pine Script file",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Update TradingView even if Pine Script has not changed since last run",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pine_path = Path(args.pine_file)

    if not pine_path.exists():
        print(f"[ERROR] Pine file not found: {pine_path}")
        return 1

    pine_script = pine_path.read_text(encoding="utf-8")
    script_hash = hashlib.sha256(pine_script.encode()).hexdigest()

    print(f"[OK] Read Pine Script ({len(pine_script)} chars, {len(pine_script.split(chr(10)))} lines)")

    # Check if script changed since last update
    if not args.force and _has_unchanged(script_hash):
        print("[SKIP] Pine Script unchanged since last TradingView update. Nothing to do.")
        return 0

    symbol = args.symbol.upper()
    chart_url = f"https://www.tradingview.com/chart/?symbol=FX:{symbol}"

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=r"C:\Users\ADMIN\AppData\Local\Google\Chrome\User Data\Default",
            headless=False,
            viewport={"width": 1920, "height": 1080},
        )
        page = context.pages[0] if context.pages else context.new_page()

        try:
            print(f"[INFO] Opening TradingView: {chart_url}")
            page.goto(chart_url, wait_until="load")
            page.wait_for_timeout(6000)

            # Check for blockers (login, captcha, etc.)
            if _has_blockers(page):
                print("[BLOCKED] Sign-in required. Please sign in to TradingView in the opened browser.")
                print("[WAITING] Polling for chart to load (max 5 minutes)...")
                if not _wait_for_chart_ready(page):
                    print("[FAILED] Chart did not become ready after sign-in. Aborting.")
                    page.wait_for_timeout(5000)
                    context.close()
                    return 2
                print("[OK] Signed in successfully. Session saved — future runs will skip login.")

            print("[INFO] Chart loaded. Opening Pine Editor...")
            if not _open_pine_editor(page):
                print("[BLOCKED] Could not open Pine Editor. The Pine Editor button may not be visible.")
                print("[ACTION] Ensure you are logged into TradingView. Free account is sufficient.")
                page.wait_for_timeout(10000)
                context.close()
                return 3

            print("[INFO] Pasting Pine Script into editor...")
            if not _paste_script(page, pine_script):
                print("[BLOCKED] Could not paste script into Pine Editor.")
                page.wait_for_timeout(10000)
                context.close()
                return 4

            print("[INFO] Adding indicator to chart...")
            _click_add_to_chart(page)

            # Handle any dialog that appears
            _handle_dialogs(page)

            time.sleep(3)
            _save_hash(script_hash)

            print(f"[OK] TradingView Pine Editor updated successfully for {symbol}.")
            print(f"[OK] Zones from: {pine_path}")

        except PlaywrightTimeout as e:
            print(f"[ERROR] Timeout: {e}")
            context.close()
            return 5
        except Exception as e:
            print(f"[ERROR] {e}")
            context.close()
            return 6

        print("[INFO] Closing browser in 5 seconds...")
        page.wait_for_timeout(5000)
        context.close()

    return 0


def _has_blockers(page) -> bool:
    blocker_texts = page.evaluate("""() => {
        const body = document.body.innerText;
        return {
            loginWall: body.includes('Sign in to continue') || body.includes('Log in to access'),
            captcha: document.querySelector('iframe[src*="captcha"], .g-recaptcha') !== null,
            paywall: body.includes('subscription') || body.includes('upgrade'),
        };
    }""")
    if blocker_texts.get('loginWall'):
        print("[BLOCKER] Login wall detected.")
        return True
    if blocker_texts.get('captcha'):
        print("[BLOCKER] Captcha detected.")
        return True
    if blocker_texts.get('paywall'):
        print("[BLOCKER] Paywall detected.")
        return True
    return False


def _wait_for_chart_ready(page, timeout: int = 300) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if not _has_blockers(page):
                return True
        except Exception:
            pass
        page.wait_for_timeout(3000)
    return False


def _open_pine_editor(page) -> bool:
    # The Pine Editor tab is at the bottom of the TradingView chart page.
    # It is accessed via a button whose aria-label contains "Pine".
    # The button is only visible after the chart has fully loaded.

    pine_button_selectors = [
        'button[aria-label*="Pine Editor"]',
        'button[aria-label*="Pine"]',
        '[data-role="tab"][title*="Pine"]',
    ]

    for sel in pine_button_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=3000):
                label = btn.get_attribute("aria-label") or btn.get_attribute("title") or ""
                print(f"  [DEBUG] Clicking: {sel} (label: {label})")
                btn.click()
                page.wait_for_timeout(2000)

                # Verify editor appeared
                time.sleep(1)
                if _editor_visible(page):
                    return True
        except Exception:
            continue

    return False


def _editor_visible(page) -> bool:
    return page.evaluate("""() => {
        const text = document.body.innerText;
        return text.includes('Pine Editor') || text.includes('编辑器');
    }""")


def _paste_script(page, script: str) -> bool:
    # Wait for the Monaco editor textarea to appear
    max_attempts = 5
    for i in range(max_attempts):
        try:
            textarea = page.locator(".monaco-editor textarea.inputarea, .view-lines").first
            if textarea.is_visible(timeout=2000):
                break
        except Exception:
            page.wait_for_timeout(1000)
    else:
        print("[WARN] Could not find Monaco editor textarea.")

    # Strategy: use JavaScript to set Monaco editor content directly
    js_result = page.evaluate("""(code) => {
        try {
            // Try Monaco editor API
            const editors = window.monaco && window.monaco.editor ? window.monaco.editor.getEditors() : [];
            if (editors.length > 0) {
                const model = editors[0].getModel();
                model.setValue(code);
                return 'monaco_setValue';
            }
        } catch(e) {}

        try {
            // Try to find a textarea and set value
            const ta = document.querySelector('textarea.inputarea, .view-lines textarea');
            if (ta) {
                ta.value = code;
                ta.dispatchEvent(new Event('input', { bubbles: true }));
                return 'textarea_value';
            }
        } catch(e) {}

        return 'no_editor_found';
    }""", script)

    print(f"  [DEBUG] Paste method: {js_result}")

    if js_result == 'no_editor_found':
        # Fallback: Ctrl+A, then type
        try:
            page.keyboard.press("Control+a")
            page.wait_for_timeout(200)
            page.keyboard.insert_text(script)
            return True
        except Exception:
            return False

    page.wait_for_timeout(1000)
    return True


def _click_add_to_chart(page) -> None:
    add_selectors = [
        'button[aria-label*="Add to chart"]',
        'button:has-text("Add to chart")',
        'button[aria-label*="添加到图表"]',
    ]

    for sel in add_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=3000):
                print(f"  [DEBUG] Clicking: {sel}")
                btn.click()
                page.wait_for_timeout(2000)
                return
        except Exception:
            continue

    # Keyboard shortcut fallback
    try:
        page.keyboard.press("Control+s")
        page.wait_for_timeout(2000)
    except Exception:
        pass


def _handle_dialogs(page) -> None:
    dialog_buttons = [
        'button:has-text("Save")',
        'button:has-text("Replace")',
        'button:has-text("Add")',
        'button:has-text("Continue")',
        'button:has-text("Yes")',
        'button:has-text("OK")',
        '[data-name="dialog-ok-button"]',
    ]
    for sel in dialog_buttons:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=2000):
                print(f"  [DEBUG] Handling dialog via: {sel}")
                btn.click()
                page.wait_for_timeout(1000)
        except Exception:
            pass


def _has_unchanged(new_hash: str) -> bool:
    try:
        old_hash = HASH_FILE.read_text(encoding="utf-8").strip()
        return old_hash == new_hash
    except Exception:
        return False


def _save_hash(hash_value: str) -> None:
    HASH_FILE.parent.mkdir(parents=True, exist_ok=True)
    HASH_FILE.write_text(hash_value, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
