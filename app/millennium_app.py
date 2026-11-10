"""Millennium Dashboard - desktop app window (no browser bar or tabs).

Shows the dashboard page in its own window using the Windows WebView2 engine. Buttons work in-process:
  Open draft   -> Gmail compose (in your browser) + PDF highlighted in File Explorer
  I sent it    -> logs it in the outreach ledger, then the page refreshes itself
  Refresh      -> rebuilds the page from the latest local data
  web links    -> open in your normal browser; PDF/Markdown links -> open with their default app

The code it runs (dashboard.py, action.py, outreach.py) is read from the automation folder at start, so
improvements there show up without rebuilding the .exe.
"""
# Standard-library modules used by the dynamically loaded automation scripts (PyInstaller must bundle them).
import ctypes  # noqa: F401
import datetime  # noqa: F401
import hashlib  # noqa: F401
import html  # noqa: F401
import importlib
import json  # noqa: F401
import math  # noqa: F401
import os
import re  # noqa: F401
import shutil  # noqa: F401
import subprocess  # noqa: F401
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request  # noqa: F401
import webbrowser
from pathlib import Path

import webview

if getattr(sys, "frozen", False):
    # exe lives in automation\app\Millennium Dashboard\
    AUTOMATION = Path(sys.executable).resolve().parents[2]
else:
    AUTOMATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AUTOMATION))
os.chdir(AUTOMATION)

LINK_HOOK = r"""
document.addEventListener('click', function (event) {
  var link = event.target.closest('a');
  if (!link) { return; }
  var href = link.getAttribute('href') || '';
  if (/^(millennium:|https?:|file:)/i.test(href)) {
    event.preventDefault();
    window.pywebview.api.handle(href);
  }
}, true);
"""


def load_modules():
    modules = {}
    # order matters: helpers first, so dashboard/action pick up the freshly reloaded versions
    for name in ("dashboard_charts", "history", "progress", "dashboard_extras", "dashboard_results", "daily_progress", "dashboard", "outreach", "action"):
        if name in sys.modules:
            modules[name] = importlib.reload(sys.modules[name])
        else:
            modules[name] = importlib.import_module(name)
    return modules


class Api:
    """Buttons from the page. Work always runs on a background thread so the window never stops
    responding (Windows closes 'not responding' windows, which looked like a crash)."""

    def __init__(self):
        self._window = None  # underscore: pywebview must not walk the window object (it recursed forever and froze the app)

    def rebuild_and_show(self):
        modules = load_modules()
        modules["dashboard"].build()
        # a new ?t= value makes WebView2 really reload; the same URL is ignored
        url = modules["dashboard"].OUT.resolve().as_uri() + f"?t={int(time.time())}"
        self._window.load_url(url)

    def handle(self, href):
        threading.Thread(target=self._handle, args=(href,), daemon=True).start()

    def _handle(self, href):
        try:
            parsed = urllib.parse.urlparse(href)
            scheme = parsed.scheme.lower()
            if scheme == "millennium":
                action = parsed.netloc.lower()
                argument = urllib.parse.unquote(parsed.path.strip("/"))
                modules = load_modules() if action != "poll" else _cached_modules()
                if action != "seen":
                    argument = argument.lower()
                # all button logic lives in action.handle(), read from the folder: new buttons need no rebuild
                if modules["action"].handle(action, argument, in_app=True):
                    self.rebuild_and_show()
            elif scheme in ("http", "https"):
                webbrowser.open(href)
            elif scheme == "file":
                local = urllib.request.url2pathname(parsed.path)
                if Path(local).exists():
                    os.startfile(local)
        except Exception:
            _log_error(traceback.format_exc())


_MODULES = {}


def _cached_modules():
    """Polls run every 10 s: reuse the loaded modules instead of re-importing everything each time."""
    if not _MODULES:
        _MODULES.update(load_modules())
    return _MODULES


def _log_error(text):
    with (AUTOMATION / "app_errors.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{text}\n")


def on_loaded(window):
    window.evaluate_js(LINK_HOOK)


def main():
    sys.excepthook = lambda *info: _log_error("".join(traceback.format_exception(*info)))
    modules = load_modules()
    modules["dashboard"].build()
    api = Api()
    window = webview.create_window(
        "Millennium Dashboard",
        url=modules["dashboard"].OUT.resolve().as_uri(),
        js_api=api,
        width=1320,
        height=900,
        min_size=(380, 600),
        background_color="#f6f7f9",
        text_select=True,
    )
    api._window = window
    window.events.loaded += lambda: on_loaded(window)
    webview.start(private_mode=False, storage_path=str(AUTOMATION / "app" / "webview_data"))


if __name__ == "__main__":
    main()
