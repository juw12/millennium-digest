"""Opens a filled-in email in your pinned Gmail app (Chrome, already signed in). Nothing is ever sent.

  * Uses the same Chrome (and profile) as the "Gmail" shortcut pinned to the taskbar, so there is no
    separate browser profile and no extra sign-in.
  * To / Subject / Body come in through Gmail's compose link.
  * The PDF is put on the clipboard as a file and pasted into the message once the compose window is
    ready (Ctrl+V). Keys are only sent when that new Gmail window is in front; otherwise you press
    Ctrl+V yourself. You read it and press Send.

  python gmail_app.py --test     compose a test email to yourself with the BSD report attached

Standard library + ctypes only, so the desktop .exe can run it in-process.
"""
import ctypes
import ctypes.wintypes as wintypes
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import outreach  # noqa: E402

PINNED_GMAIL = (
    Path(os.environ.get("APPDATA", ""))
    / "Microsoft" / "Internet Explorer" / "Quick Launch" / "User Pinned" / "TaskBar" / "Gmail.lnk"
)
CHROME_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
]
WINDOW_WAIT_S = 30
EDITOR_SETTLE_S = 3.0
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TITLE = "Millennium Dashboard"

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def _message(text, warn=False):
    flags = 0x30 if warn else 0x40
    user32.MessageBoxW(None, text, TITLE, flags | 0x40000)


def _pinned_shortcut():
    """(target exe, extra profile args) of the pinned Gmail shortcut, or (None, [])."""
    if not PINNED_GMAIL.exists():
        return None, []
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:GMAIL_LNK); "
        "$s.TargetPath; $s.Arguments"
    )
    try:
        output = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=NO_WINDOW,
            env={**os.environ, "GMAIL_LNK": str(PINNED_GMAIL)},
        ).stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return None, []
    target = Path(output[0].strip()) if output and output[0].strip() else None
    arguments = output[1] if len(output) > 1 else ""
    profile = [part for part in arguments.split() if part.startswith("--profile-directory")]
    return target, profile


def chrome_command():
    target, profile = _pinned_shortcut()
    if target and target.exists():
        return [str(target)] + profile
    for candidate in CHROME_CANDIDATES:
        if candidate.exists():
            return [str(candidate)]
    return None


def _copy_file_to_clipboard(path):
    result = subprocess.run(
        ["powershell", "-NoProfile", "-STA", "-Command", "Set-Clipboard -LiteralPath $env:CLIP_FILE"],
        capture_output=True,
        timeout=20,
        creationflags=NO_WINDOW,
        env={**os.environ, "CLIP_FILE": str(path)},
    )
    return result.returncode == 0


def _process_name(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buffer))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return Path(buffer.value).name.lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _title(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _chrome_windows():
    found = {}
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def collect(hwnd, _):
        if user32.IsWindowVisible(hwnd) and _process_name(hwnd) == "chrome.exe":
            found[hwnd] = _title(hwnd)
        return True

    user32.EnumWindows(callback_type(collect), 0)
    return found


def _wait_for_compose_window(before):
    deadline = time.time() + WINDOW_WAIT_S
    while time.time() < deadline:
        for hwnd, title in _chrome_windows().items():
            if hwnd not in before and "gmail" in title.lower():
                return hwnd
        time.sleep(0.5)
    return None


def _press_ctrl_v():
    vk_control, vk_v, key_up = 0x11, 0x56, 0x2
    user32.keybd_event(vk_control, 0, 0, 0)
    user32.keybd_event(vk_v, 0, 0, 0)
    user32.keybd_event(vk_v, 0, key_up, 0)
    user32.keybd_event(vk_control, 0, key_up, 0)


def _bring_to_front(hwnd):
    """Windows only lets the active app move focus; a tap of Alt lifts that lock for this call."""
    vk_menu, key_up = 0x12, 0x2
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.keybd_event(vk_menu, 0, 0, 0)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(vk_menu, 0, key_up, 0)


def _paste_attachment(hwnd):
    """Ctrl+V into the compose window, only if it is the window in front."""
    time.sleep(EDITOR_SETTLE_S)
    _bring_to_front(hwnd)
    time.sleep(0.4)
    if user32.GetForegroundWindow() != hwnd:
        return False
    _press_ctrl_v()
    return True


def open_compose(to, subject, body, attachment=None, wait=False):
    """Open the email in the Gmail app. Runs in a background thread unless wait=True."""
    command = chrome_command()
    if command is None:
        return False

    def run():
        attachment_path = Path(attachment) if attachment else None
        has_file = bool(attachment_path and attachment_path.exists())
        clipboard_ok = has_file and _copy_file_to_clipboard(attachment_path)
        before = _chrome_windows()
        subprocess.Popen(command + ["--app=" + outreach.gmail_compose_url(to, subject, body)])
        if not has_file:
            return
        hwnd = _wait_for_compose_window(before)
        pasted = bool(hwnd and clipboard_ok and _paste_attachment(hwnd))
        if not pasted:
            note = (
                "The PDF is on your clipboard: click into the message and press Ctrl+V."
                if clipboard_ok
                else f"Attach the PDF yourself:\n{attachment_path}"
            )
            _message("Gmail is open with the email filled in.\n\n" + note)

    if wait:
        run()
    else:
        threading.Thread(target=run, daemon=False).start()
    return True


def main():
    if "--test" in sys.argv:
        from settings import EMAIL

        report = HERE.parent / "bsd_project" / "report" / "Murmuration_amplitude_report.pdf"
        opened = open_compose(
            EMAIL,
            "Test: Gmail app compose with attachment",
            "Test from gmail_app.py. Delete me; nothing was sent.\n",
            report,
            wait=True,
        )
        print("opened" if opened else "Chrome / pinned Gmail app not found")


if __name__ == "__main__":
    main()
