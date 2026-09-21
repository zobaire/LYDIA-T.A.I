"""Windows window-management: list, focus, minimize/restore, and screen layouts.

Requires: pip install pywin32 psutil
"""
from __future__ import annotations
from typing import Optional, List, Dict

import win32api
import win32con
import win32gui
import win32process
import psutil


# ---------------------------------------------------------------------------
# Screen geometry
# ---------------------------------------------------------------------------

def get_screen_size():
    width = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
    height = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
    return width, height


ZONES = {
    "full":         lambda w, h: (0, 0, w, h),
    "left-half":    lambda w, h: (0, 0, w // 2, h),
    "right-half":   lambda w, h: (w // 2, 0, w // 2, h),
    "top-half":     lambda w, h: (0, 0, w, h // 2),
    "bottom-half":  lambda w, h: (0, h // 2, w, h // 2),
    "top-left":     lambda w, h: (0, 0, w // 2, h // 2),
    "top-right":    lambda w, h: (w // 2, 0, w // 2, h // 2),
    "bottom-left":  lambda w, h: (0, h // 2, w // 2, h // 2),
    "bottom-right": lambda w, h: (w // 2, h // 2, w // 2, h // 2),
}


# ---------------------------------------------------------------------------
# Window discovery
# ---------------------------------------------------------------------------

def list_windows() -> str:
    """Return every visible, titled top-level window with its process name."""
    windows = []

    def _callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title.strip():
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process_name = psutil.Process(pid).name()
        except Exception:
            process_name = "unknown"
        minimized = win32gui.IsIconic(hwnd)
        windows.append(f"{process_name:<20} | {title}{' [minimized]' if minimized else ''}")

    win32gui.EnumWindows(_callback, None)
    if not windows:
        return "No open windows found."
    return "Open windows:\n" + "\n".join(windows)


def _find_window(app_name: str) -> Optional[Dict]:
    """Fuzzy-match a window by process name or title substring."""
    app_name = app_name.lower().strip()
    windows = []

    def _callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title.strip():
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process_name = psutil.Process(pid).name()
        except Exception:
            process_name = "unknown"
        windows.append({
            "hwnd": hwnd,
            "title": title,
            "process": process_name,
        })

    win32gui.EnumWindows(_callback, None)

    # Pass 1: exact-ish process name match (e.g. "chrome" -> "chrome.exe")
    for w in windows:
        proc = w["process"].lower()
        if proc == f"{app_name}.exe" or proc == app_name:
            return w

    # Pass 2: process name contains it
    for w in windows:
        if app_name in w["process"].lower():
            return w

    # Pass 3: window title contains it
    for w in windows:
        if app_name in w["title"].lower():
            return w

    return None


# ---------------------------------------------------------------------------
# Check which apps are open
# ---------------------------------------------------------------------------

def check_apps_status(apps: List[str]) -> str:
    """Report which of the given app names are currently running/open,
    and which are not. Use this BEFORE opening or splitting apps so the
    caller only opens what's genuinely missing."""
    if not apps:
        return "No apps given to check."

    reports = []
    for name in apps:
        win = _find_window(name)
        if win:
            minimized = " (minimized)" if win32gui.IsIconic(win["hwnd"]) else ""
            reports.append(f"{name}: open{minimized}")
        else:
            reports.append(f"{name}: not open")
    return "; ".join(reports)


def apps_open(names: List[str]) -> Dict[str, bool]:
    """Return a dict {app_name: is_open} for each requested app.
    Useful when the caller needs a programmatic result to branch on."""
    return {name: _find_window(name) is not None for name in names}


# ---------------------------------------------------------------------------
# Focus (by app name / process / title)
# ---------------------------------------------------------------------------

def focus_window(app_name: str) -> str:
    """Bring the matched window to the foreground and restore it if minimized."""
    win = _find_window(app_name)
    if not win:
        return f"Couldn't find a window matching '{app_name}'."

    hwnd = win["hwnd"]
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        fg_hwnd = win32gui.GetForegroundWindow()
        fg_thread = win32process.GetWindowThreadProcessId(fg_hwnd)[0]
        target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]

        if fg_thread != target_thread:
            win32process.AttachThreadInput(fg_thread, target_thread, True)
            win32gui.SetForegroundWindow(hwnd)
            win32process.AttachThreadInput(fg_thread, target_thread, False)
        else:
            win32gui.SetForegroundWindow(hwnd)

        return f"Focused {win['process']} ({win['title']})."
    except Exception as e:
        return f"Failed to focus '{app_name}': {e}"


# ---------------------------------------------------------------------------
# Minimize / restore
# ---------------------------------------------------------------------------

def minimize_window(app_name: str) -> str:
    win = _find_window(app_name)
    if not win:
        return f"Couldn't find a window matching '{app_name}'."
    win32gui.ShowWindow(win["hwnd"], win32con.SW_MINIMIZE)
    return f"Minimized {win['process']}."


def restore_window(app_name: str) -> str:
    win = _find_window(app_name)
    if not win:
        return f"Couldn't find a window matching '{app_name}'."
    win32gui.ShowWindow(win["hwnd"], win32con.SW_RESTORE)
    return f"Restored {win['process']}."


# ---------------------------------------------------------------------------
# Move / resize into a zone
# ---------------------------------------------------------------------------

def set_window_zone(app_name: str, zone: str) -> str:
    if zone not in ZONES:
        return f"Unknown zone '{zone}'. Valid zones: {', '.join(ZONES)}"

    win = _find_window(app_name)
    if not win:
        return f"Couldn't find a window matching '{app_name}'."

    hwnd = win["hwnd"]
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    w, h = get_screen_size()
    x, y, width, height = ZONES[zone](w, h)
    win32gui.SetWindowPos(
        hwnd, win32con.HWND_TOP, x, y, width, height, win32con.SWP_SHOWWINDOW
    )
    return f"Moved {win['process']} to {zone}."


def set_window_layout(layout: Dict[str, str]) -> str:
    """Arrange multiple apps into screen zones.
    layout example: {"vscode": "left-half", "chrome": "top-right", "discord": "bottom-right"}
    """
    results = []
    for app_name, zone in layout.items():
        results.append(set_window_zone(app_name, zone))
    return "; ".join(results)


def split_windows_equally(apps: List[str], direction: str = "horizontal") -> List[str]:
    """
    Split the screen into N equal zones and assign one app per zone, in order.
    direction: "horizontal" (side-by-side columns) or "vertical" (stacked rows)

    Example: split_windows_equally(["discord", "spotify", "brave"])
    -> each gets exactly 1/3 of the screen width, side by side.
    """
    results = []
    n = len(apps)
    if n == 0:
        return results

    w, h = get_screen_size()

    for i, app_name in enumerate(apps):
        win = _find_window(app_name)
        if not win:
            results.append(f"Couldn't find a window matching '{app_name}'.")
            continue

        hwnd = win["hwnd"]
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        if direction == "horizontal":
            col_width = w // n
            x, y, width, height = i * col_width, 0, col_width, h
        else:  # vertical
            row_height = h // n
            x, y, width, height = 0, i * row_height, w, row_height

        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOP, x, y, width, height, win32con.SWP_SHOWWINDOW
        )
        results.append(f"Placed {win['process']} in column {i+1}/{n}.")

    return results
