"""Functional UI test for the dashboard and the public pages. Run before every commit / app rebuild.

    python tests/test_dashboard_ui.py            rebuild dashboard/index.html, then test it + docs/*.html
    python tests/test_dashboard_ui.py --no-build test the pages as they are

Every check clicks the real control in a real browser (Playwright/Chromium) and compares what is
*visible on screen* with what the page data says should be visible. A control that changes classes or
attributes but not the screen counts as broken (that is how the filter-chip bug slipped through:
the JS set item.hidden, but a CSS display:flex rule kept the cards visible).

Exit code 0 = all checks passed, 1 = at least one failed (each failure is printed).
"""
import json
import re
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parents[1]
DASHBOARD = HERE / "dashboard" / "index.html"
PUBLIC_PAGES = [HERE / "docs" / "index.html", HERE / "docs" / "results.html"]
VIEWPORTS = [(1280, 900), (390, 844)]

FAILURES = []
PASSES = []


def check(condition, name, detail=""):
    (PASSES if condition else FAILURES).append(f"{name}{(': ' + detail) if detail and not condition else ''}")
    return condition


def known_actions():
    """Actions action.py really handles ('if action == "x"')."""
    source = (HERE / "action.py").read_text(encoding="utf-8")
    return set(re.findall(r'if action == "([a-z]+)"', source))


def app_link_hook():
    """The click hook the desktop app injects (kept in sync by reading it from the app source)."""
    source = (HERE / "app" / "millennium_app.py").read_text(encoding="utf-8")
    match = re.search(r'LINK_HOOK = r"""(.*?)"""', source, re.S)
    return match.group(1) if match else ""


STUB_PYWEBVIEW = """
window.__appCalls = [];
window.pywebview = { api: { handle: function (href) { window.__appCalls.push(href); return Promise.resolve(null); } } };
"""

# Judge by what the browser actually draws, never by the hidden attribute (that was the blind spot).
VISIBLE_JS = """el => {
  if (!el.isConnected) return false;
  const style = getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden') return false;
  return el.getClientRects().length > 0;
}"""


def visible_items(page, box_selector):
    return page.eval_on_selector_all(
        f"{box_selector} [data-kind]",
        f"els => els.filter({VISIBLE_JS}).length",
    )


def expected_items(page, box_selector, want):
    return page.eval_on_selector_all(
        f"{box_selector} [data-kind]",
        """(els, want) => els.filter(e => want === 'all' ||
             (e.getAttribute('data-kind') || '').split(' ').indexOf(want) >= 0).length""",
        want,
    )


def open_page(browser, path, width, height, app_mode):
    context = browser.new_context(viewport={"width": width, "height": height})
    errors = []
    page = context.new_page()
    page.set_default_timeout(5000)
    page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
    page.on("console", lambda message: errors.append(f"console.{message.type}: {message.text}")
            if message.type == "error" else None)
    if app_mode:
        page.add_init_script(STUB_PYWEBVIEW)
    page.goto(path.as_uri())
    if app_mode:
        page.evaluate(app_link_hook())
    page.wait_for_timeout(400)
    return context, page, errors


# ------------------------------------------------------------------ individual checks

def check_filter_boxes(page, label):
    boxes = page.eval_on_selector_all("[data-fbox]", "els => els.map(e => e.getAttribute('data-fbox'))")
    seen = set()
    for name in boxes:
        if name in seen:
            continue
        seen.add(name)
        box = f'[data-fbox="{name}"]'
        limit = int(page.get_attribute(box, "data-limit") or 0)
        chips = page.locator(f"{box} .fchip")
        total = expected_items(page, box, "all")
        check(chips.count() > 0 and total > 0, f"{label} filter '{name}' has chips and items",
              f"{chips.count()} chips, {total} items")
        for index in range(chips.count()):
            chip = chips.nth(index)
            want = chip.get_attribute("data-filter")
            chip.scroll_into_view_if_needed()
            chip.click()
            page.wait_for_timeout(120)
            matching = expected_items(page, box, want)
            should_show = min(matching, limit) if limit else matching
            shown = visible_items(page, box)
            check_hidden_really_hidden(page, f"{label} filter '{name}' chip '{want}'")
            check(shown == should_show, f"{label} filter '{name}' chip '{want}' shows the right items",
                  f"visible {shown}, expected {should_show} of {total}")
            badge = chip.locator("b")
            if badge.count():
                check(badge.inner_text().strip() == str(matching),
                      f"{label} filter '{name}' chip '{want}' count badge is right",
                      f"badge {badge.inner_text().strip()}, items {matching}")
            check("on" in (chip.get_attribute("class") or ""), f"{label} filter '{name}' chip '{want}' is highlighted")
            unfiltered = min(total, limit) if limit else total
            if want != "all" and should_show < unfiltered:
                check(shown < unfiltered, f"{label} filter '{name}' chip '{want}' visibly narrows the list",
                      f"visible {shown}, unfiltered {unfiltered}")
        # back to "all" so later checks start from a clean state
        page.locator(f'{box} .fchip[data-filter="all"]').click()
        page.wait_for_timeout(100)
        check(visible_items(page, box) == (min(total, limit) if limit else total),
              f"{label} filter '{name}' returns to all")


def check_show_all(page, label):
    buttons = page.locator(".more-row button")
    for index in range(buttons.count()):
        button = buttons.nth(index)
        box_handle = button.evaluate_handle("b => b.closest('[data-fbox]')")
        name = box_handle.evaluate("b => b ? b.getAttribute('data-fbox') : ''")
        box = f'[data-fbox="{name}"]'
        if not button.is_visible():
            continue
        total = expected_items(page, box, "all")
        button.click()
        page.wait_for_timeout(120)
        check(visible_items(page, box) == total, f"{label} '{name}' Show all reveals every item",
              f"visible {visible_items(page, box)}, expected {total}")
        check(not button.is_visible(), f"{label} '{name}' Show all button hides itself afterwards")


def check_search(page, label):
    searches = page.locator("[data-fbox] [data-search]")
    for index in range(searches.count()):
        search = searches.nth(index)
        name = search.evaluate("s => s.closest('[data-fbox]').getAttribute('data-fbox')")
        box = f'[data-fbox="{name}"]'
        before = visible_items(page, box)
        first_text = page.eval_on_selector_all(
            f"{box} [data-kind]",
            f"els => {{ const e = els.find({VISIBLE_JS}); return e ? (e.getAttribute('data-text') || e.textContent) : ''; }}",
        )
        words = re.findall(r"[A-Za-z]{5,}", first_text)
        if not check(bool(words), f"{label} search '{name}' has searchable items"):
            continue
        word = max(words, key=len).lower()
        search.fill(word)
        page.wait_for_timeout(150)
        check_hidden_really_hidden(page, f"{label} search '{name}'")
        shown_texts = page.eval_on_selector_all(
            f"{box} [data-kind]",
            f"els => els.filter({VISIBLE_JS}).map(e => (e.getAttribute('data-text') || e.textContent).toLowerCase())",
        )
        check(0 < len(shown_texts) <= before and all(word in text for text in shown_texts),
              f"{label} search '{name}' filters to matching items", f"'{word}' -> {len(shown_texts)} of {before}")
        search.fill("zzzzqqqq-no-match")
        page.wait_for_timeout(150)
        check(visible_items(page, box) == 0 and page.locator(f"{box} .fempty").is_visible(),
              f"{label} search '{name}' shows the 'no match' note")
        search.fill("")
        page.wait_for_timeout(150)
        check(visible_items(page, box) == before, f"{label} search '{name}' clears back")


def check_bar_rows(page, label):
    rows = page.locator("[data-filter-target]")
    box = '[data-fbox="papers"]'
    for index in range(rows.count()):
        row = rows.nth(index)
        want = row.get_attribute("data-filter-target")
        row.scroll_into_view_if_needed()
        row.click()
        page.wait_for_timeout(150)
        active = page.locator(f"{box} .fchip.on")
        check(active.count() == 1 and active.get_attribute("data-filter") == want,
              f"{label} bar '{want}' selects its paper chip")
        check(visible_items(page, box) == expected_items(page, box, want),
              f"{label} bar '{want}' filters the paper list")
    if rows.count():
        page.locator(f'{box} .fchip[data-filter="all"]').click()


def check_nav(page, label, height):
    links = page.locator("a[href^='#']")
    for index in range(links.count()):
        link = links.nth(index)
        target_id = (link.get_attribute("href") or "#")[1:]
        if not target_id:
            check(False, f"{label} link '{link.inner_text().strip()}' has a real target", "href='#'")
            continue
        exists = page.locator(f"[id='{target_id}']").count() == 1
        if not check(exists, f"{label} link '#{target_id}' points at an existing section"):
            continue
        if not link.is_visible():
            continue
        page.evaluate("window.scrollTo(0, 0)")
        link.click()
        page.wait_for_timeout(700)
        top, at_bottom = page.evaluate(
            """id => { const r = document.getElementById(id).getBoundingClientRect();
                       return [r.top, window.scrollY + innerHeight >= document.documentElement.scrollHeight - 2]; }""",
            target_id,
        )
        check(at_bottom or -5 <= top <= height * 0.5, f"{label} link '#{target_id}' scrolls to its section",
              f"section top at {top:.0f}px")


def check_app_links(page, label):
    actions = known_actions()
    links = page.locator("a[href^='millennium:']")
    for index in range(links.count()):
        link = links.nth(index)
        href = link.get_attribute("href")
        action = href.split("//", 1)[-1].split("/", 1)[0]
        check(action in actions, f"{label} app button '{href}' is handled by action.py", f"known: {sorted(actions)}")
        if not link.is_visible():
            continue
        page.evaluate("window.__appCalls = []")
        link.click()
        page.wait_for_timeout(100)
        calls = page.evaluate("window.__appCalls")
        check(calls == [href], f"{label} app button '{href}' reaches the app", f"calls {calls}")


def check_buttons_are_covered(page, label):
    """Every <button> must be a control this test exercises; a new kind of button fails until tested."""
    unknown = page.eval_on_selector_all(
        "button",
        "els => els.filter(b => !b.classList.contains('fchip') && !b.closest('.more-row'))"
        ".map(b => (b.innerText || b.outerHTML).trim().slice(0, 60))",
    )
    check(not unknown, f"{label} every button has a functional test", f"untested buttons: {unknown}")


def check_details(page, label):
    summaries = page.locator("details > summary")
    for index in range(summaries.count()):
        summary = summaries.nth(index)
        if not summary.is_visible():
            continue
        was_open = summary.evaluate("s => s.parentElement.open")
        summary.click()
        page.wait_for_timeout(80)
        check(summary.evaluate("s => s.parentElement.open") != was_open, f"{label} expandable section {index + 1} toggles")
        summary.click()


def check_local_links(page, label, page_path):
    """Every link to a file on this PC (file:/// or relative) must point at a file that exists."""
    import urllib.parse

    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(a => a.getAttribute('href'))")
    for href in sorted(set(hrefs)):
        if not href or href.startswith(("#", "http", "millennium:", "mailto:")):
            continue
        if href.startswith("file:"):
            target = Path(urllib.parse.unquote(urllib.parse.urlparse(href).path).lstrip("/"))
        else:
            target = (page_path.parent / urllib.parse.unquote(href.split("#")[0])).resolve()
        check(target.exists(), f"{label} link to a local file opens", f"missing: {target}")


def check_hidden_really_hidden(page, label):
    leaks = page.eval_on_selector_all(
        "[hidden]",
        "els => els.filter(e => getComputedStyle(e).display !== 'none').map(e => e.tagName + '.' + e.className)",
    )
    check(not leaks, f"{label} hidden elements are not drawn", f"{leaks[:5]}")


def check_layout(page, label, width):
    overflow = page.evaluate("document.documentElement.scrollWidth - innerWidth")
    check(overflow <= 1, f"{label} no sideways scrolling at {width}px", f"{overflow}px too wide")


def check_remembered_filter(browser, path, label):
    """A filter remembered from last time must still match what is drawn after a reload."""
    context, page, _ = open_page(browser, path, 1280, 900, app_mode=True)
    boxes = page.eval_on_selector_all("[data-fbox]", "els => [...new Set(els.map(e => e.getAttribute('data-fbox')))]")
    for name in boxes:
        box = f'[data-fbox="{name}"]'
        chip = page.locator(f'{box} .fchip:not([data-filter="all"])').first
        if not chip.count():
            continue
        want = chip.get_attribute("data-filter")
        chip.scroll_into_view_if_needed()
        chip.click()
        page.reload()
        page.wait_for_timeout(400)
        limit = int(page.get_attribute(box, "data-limit") or 0)
        matching = expected_items(page, box, want)
        active = page.locator(f"{box} .fchip.on")
        check(active.count() == 1 and active.get_attribute("data-filter") == want,
              f"{label} filter '{name}' is remembered after reload")
        check(visible_items(page, box) == (min(matching, limit) if limit else matching),
              f"{label} remembered filter '{name}' matches what is drawn")
        page.locator(f'{box} .fchip[data-filter="all"]').click()
    context.close()


def test_page(browser, path, app_mode):
    label = path.relative_to(HERE).as_posix() if path.is_relative_to(HERE) else path.name
    if not check(path.exists(), f"{label} exists"):
        return
    for width, height in VIEWPORTS:
        tag = f"{label}@{width}"
        context, page, errors = open_page(browser, path, width, height, app_mode)
        steps = [
            lambda: check_layout(page, tag, width),
            lambda: check_buttons_are_covered(page, tag),
            lambda: check_filter_boxes(page, tag),
            lambda: check_search(page, tag),
            lambda: check_show_all(page, tag),
            lambda: check_bar_rows(page, tag),
            lambda: check_details(page, tag),
            lambda: check_nav(page, tag, height),
            lambda: check_app_links(page, tag) if app_mode else None,
            lambda: check_hidden_really_hidden(page, tag),
            lambda: check_local_links(page, tag, path),
            lambda: check_layout(page, tag + " after clicks", width),
        ]
        for step in steps:
            try:
                step()
            except Exception as error:  # a control that cannot even be clicked is a failure, not a crash
                check(False, f"{tag} check crashed", f"{type(error).__name__}: {str(error).splitlines()[0]}")
        check(not errors, f"{tag} no script errors", "; ".join(errors[:3]))
        context.close()
    if app_mode:
        try:
            check_remembered_filter(browser, path, label)
        except Exception as error:
            check(False, f"{label} remembered-filter check crashed", f"{type(error).__name__}: {str(error).splitlines()[0]}")


def run_browser_checks():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            if "--page" in sys.argv:
                test_page(browser, Path(sys.argv[sys.argv.index("--page") + 1]).resolve(), app_mode=True)
            else:
                test_page(browser, DASHBOARD, app_mode=True)
                for path in PUBLIC_PAGES:
                    test_page(browser, path, app_mode=False)
        finally:
            browser.close()


def start_watchdog(limit_seconds=600):
    """Fail (exit 1) instead of hanging forever if the browser stalls; a stuck gate is worse than a failed one."""
    import os
    import threading

    def expire():
        print(f"FAIL whole test exceeded {limit_seconds}s (browser stalled) - treated as a failure")
        os._exit(1)

    timer = threading.Timer(limit_seconds, expire)
    timer.daemon = True
    timer.start()


def main():
    start_watchdog()
    if "--no-build" not in sys.argv:
        result = subprocess.run([sys.executable, "dashboard.py", "--no-open"], cwd=HERE,
                                capture_output=True, text=True, timeout=180)
        check(result.returncode == 0, "dashboard.py builds", (result.stderr or result.stdout)[-400:])
    build_checks = (list(PASSES), list(FAILURES))
    for attempt in (1, 2):
        try:
            run_browser_checks()
            break
        except PlaywrightError as error:
            # The browser itself crashed (not a failed check): start over once, then give up loudly.
            PASSES[:], FAILURES[:] = list(build_checks[0]), list(build_checks[1])
            if attempt == 2:
                check(False, "browser ran to the end", f"crashed twice: {str(error).splitlines()[0]}")
    for failure in FAILURES:
        print("FAIL", failure)
    print(f"{len(PASSES)} passed, {len(FAILURES)} failed")
    (HERE / "tests" / "last_ui_test.json").write_text(
        json.dumps({"passed": len(PASSES), "failed": FAILURES}, indent=1), encoding="utf-8")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
