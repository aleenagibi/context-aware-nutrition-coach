"""Browser layout tests (Playwright + Chromium). Skipped automatically if Playwright is not installed.
Checks that no screen scrolls sideways from 300px up, that the sheet opens, and that nothing throws in the browser."""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.pop("GROQ_API_KEY", None)
os.environ["FUEL_DB"] = tempfile.mktemp(suffix=".db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

import app as A  # noqa: E402

WIDTHS = (300, 320, 360, 390, 768, 1024, 1280)
TABS = ("today", "log", "pantry", "move", "you")


def run():
    from werkzeug.serving import make_server
    A.app.config["DB_PATH"] = os.environ["FUEL_DB"]
    srv = make_server("127.0.0.1", 5077, A.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    problems, errors = [], []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for w in WIDTHS:
                page = browser.new_page(viewport={"width": w, "height": 800})
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto("http://127.0.0.1:5077/")
                for tab in TABS:
                    page.click(f"a[data-tab={tab}]")
                    page.wait_for_timeout(80)
                    sw, iw = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
                    if sw > iw:
                        problems.append(f"{w}px {tab}: page is {sw}px wide")
                page.click("a[data-tab=today]")
                page.click(".idea >> nth=0")
                page.wait_for_selector("#detail.open")
                page.wait_for_timeout(300)
                sw, iw = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
                if sw > iw:
                    problems.append(f"{w}px recipe sheet: page is {sw}px wide")
                box = page.evaluate("(() => { const r = document.querySelector('#detail').getBoundingClientRect(); return [r.left, r.right]; })()")
                if box[0] < -1 or box[1] > w + 1:
                    problems.append(f"{w}px recipe sheet is clipped: {box}")
                page.keyboard.press("Escape")                       # keyboard users can close the sheet
                page.wait_for_selector("#detail:not(.open)", state="attached")
                # the meal-confirmation sheet with its form controls
                page.click("a[data-tab=log]")
                page.fill("textarea[name=meal_text]", "2 roti, 1 katori dal, chutney")
                page.click("#panel-log form[action*=parse_meal] button[type=submit]")
                page.wait_for_selector("#detail .confirm")
                sw, iw = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
                inner = page.evaluate("(() => { const d = document.querySelector('#detail'); return [d.scrollWidth, d.clientWidth]; })()")
                if sw > iw or inner[0] > inner[1] + 1:
                    problems.append(f"{w}px confirm sheet overflows: page {sw}/{iw}, sheet {inner}")
                page.close()
            browser.close()
    finally:
        srv.shutdown()
    return problems, errors


def test_no_horizontal_overflow_and_no_js_errors():
    if sync_playwright is None:
        print("SKIP (playwright not installed)")
        return
    try:
        problems, errors = run()
    except Exception as exc:  # browser binary missing etc.
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
            print("SKIP (Chromium not installed: run `playwright install chromium`)")
            return
        raise
    assert not problems, problems
    assert not errors, errors


if __name__ == "__main__":
    test_no_horizontal_overflow_and_no_js_errors()
    print("PASS test_no_horizontal_overflow_and_no_js_errors")
