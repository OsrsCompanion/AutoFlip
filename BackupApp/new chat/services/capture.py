import ctypes
import os
import time
from typing import Any

import mss
import mss.tools
from PIL import Image
import pygetwindow as gw
import pytesseract
import win32con
import win32gui
import win32ui

SCREENSHOT_DIR = "screenshots"

EXCLUDED_WINDOW_TERMS = [
    "edge",
    "chrome",
    "firefox",
    "127.0.0.1",
    "localhost",
    "capture-runelite",
    "swagger",
    "docs",
]

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def _make_output_path(prefix: str) -> str:
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    filename = f"{prefix}_{int(time.time())}.png"
    return os.path.join(SCREENSHOT_DIR, filename)


def capture_screen() -> str:
    path = _make_output_path("ge")

    with mss.mss() as sct:
        monitor = sct.monitors[1]
        screenshot = sct.grab(monitor)
        mss.tools.to_png(screenshot.rgb, screenshot.size, output=path)

    return path


def capture_region(left: int, top: int, width: int, height: int) -> str:
    path = _make_output_path("panel")
    monitor = {"left": left, "top": top, "width": width, "height": height}

    with mss.mss() as sct:
        screenshot = sct.grab(monitor)
        mss.tools.to_png(screenshot.rgb, screenshot.size, output=path)

    return path


def _is_valid_runelite_title(title: str) -> bool:
    lower = title.lower().strip()
    if not lower:
        return False
    if not lower.startswith("runelite"):
        return False
    return not any(term in lower for term in EXCLUDED_WINDOW_TERMS)


def list_window_titles() -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []

    def callback(hwnd: int, extra: Any) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title.strip():
            return
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        windows.append(
            {
                "hwnd": hwnd,
                "title": title,
                "left": left,
                "top": top,
                "width": right - left,
                "height": bottom - top,
                "looks_like_runelite": _is_valid_runelite_title(title),
            }
        )

    win32gui.EnumWindows(callback, None)
    return windows


def _find_active_runelite_window():
    try:
        active = gw.getActiveWindow()
    except Exception:
        active = None

    if active and active.title and _is_valid_runelite_title(active.title):
        return active

    try:
        windows = gw.getAllWindows()
    except Exception:
        windows = []

    for window in windows:
        title = getattr(window, "title", "") or ""
        if _is_valid_runelite_title(title):
            return window

    return None


def _find_runelite_hwnd() -> int | None:
    matches: list[int] = []

    def callback(hwnd: int, extra: Any) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if _is_valid_runelite_title(title):
            matches.append(hwnd)

    win32gui.EnumWindows(callback, None)
    return matches[0] if matches else None


def capture_runelite_top_right_region(width: int, height: int, right_margin: int = 0, top_margin: int = 0) -> dict[str, Any]:
    window = _find_active_runelite_window()
    if window is None:
        return {"error": "Could not find a RuneLite window."}

    left = window.left + window.width - width - right_margin
    top = window.top + top_margin

    if left < 0 or top < 0:
        return {"error": "Calculated capture region is outside the screen."}

    screenshot_path = capture_region(left=left, top=top, width=width, height=height)
    return {
        "screenshot_path": screenshot_path,
        "window": {
            "title": window.title,
            "left": window.left,
            "top": window.top,
            "width": window.width,
            "height": window.height,
        },
        "capture_region": {
            "left": left,
            "top": top,
            "width": width,
            "height": height,
            "right_margin": right_margin,
            "top_margin": top_margin,
        },
    }


def _capture_window_image(hwnd: int) -> tuple[Image.Image, dict[str, Any]]:
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = right - left
    height = bottom - top

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()

    save_bitmap = win32ui.CreateBitmap()
    save_bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
    save_dc.SelectObject(save_bitmap)

    result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0)
    if result != 1:
        save_dc.BitBlt((0, 0), (width, height), mfc_dc, (0, 0), win32con.SRCCOPY)

    bmpinfo = save_bitmap.GetInfo()
    bmpstr = save_bitmap.GetBitmapBits(True)

    image = Image.frombuffer(
        "RGB",
        (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
        bmpstr,
        "raw",
        "BGRX",
        0,
        1,
    )

    win32gui.DeleteObject(save_bitmap.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)

    return image, {
        "left": left,
        "top": top,
        "width": width,
        "height": height,
        "title": win32gui.GetWindowText(hwnd),
    }


def _find_best_anchor(image: Image.Image) -> dict[str, Any] | None:
    gray = image.convert("L")
    data = pytesseract.image_to_data(gray, output_type=pytesseract.Output.DICT)

    best: dict[str, Any] | None = None
    preferred = {"slots": 3, "flipping": 2, "offers": 1}

    for i, raw in enumerate(data.get("text", [])):
        text = (raw or "").strip()
        lower = text.lower()
        if lower not in preferred:
            continue

        conf_raw = data.get("conf", ["-1"])[i]
        try:
            conf = float(conf_raw)
        except Exception:
            conf = -1.0

        candidate = {
            "text": text,
            "normalized_text": lower,
            "confidence": conf,
            "left": int(data["left"][i]),
            "top": int(data["top"][i]),
            "width": int(data["width"][i]),
            "height": int(data["height"][i]),
            "priority": preferred[lower],
        }

        if best is None:
            best = candidate
            continue
        if candidate["priority"] > best["priority"]:
            best = candidate
            continue
        if candidate["priority"] == best["priority"] and candidate["confidence"] > best["confidence"]:
            best = candidate

    return best


def _crop_from_anchor(image: Image.Image, anchor: dict[str, Any]) -> tuple[Image.Image, dict[str, Any]]:
    img_w, img_h = image.size

    crop_left = max(0, anchor["left"] - 60)
    crop_top = max(0, anchor["top"] - 75)
    crop_right = img_w
    crop_bottom = img_h

    return image.crop((crop_left, crop_top, crop_right, crop_bottom)), {
        "left": crop_left,
        "top": crop_top,
        "width": crop_right - crop_left,
        "height": crop_bottom - crop_top,
        "mode": "slots_anchor",
    }


def capture_runelite_window_top_right_region(
    width: int,
    height: int,
    right_margin: int = 20,
    top_margin: int = 70,
    auto_anchor: bool = True,
) -> dict[str, Any]:
    hwnd = _find_runelite_hwnd()
    if hwnd is None:
        return {"error": "Could not find a RuneLite window."}

    try:
        image, window_info = _capture_window_image(hwnd)
    except Exception as e:
        return {"error": f"Failed to capture RuneLite window: {e}"}

    anchor = _find_best_anchor(image) if auto_anchor else None

    if anchor is not None:
        cropped, capture_region = _crop_from_anchor(image, anchor)
        screenshot_path = _make_output_path("runelite_object_panel")
        cropped.save(screenshot_path)
        return {
            "screenshot_path": screenshot_path,
            "window": window_info,
            "capture_region": capture_region,
            "anchor": anchor,
        }

    crop_left = max(0, window_info["width"] - width - right_margin)
    crop_top = max(0, top_margin)
    crop_right = min(window_info["width"], crop_left + width)
    crop_bottom = min(window_info["height"], crop_top + height)

    if crop_right <= crop_left or crop_bottom <= crop_top:
        return {"error": "Calculated crop region is invalid."}

    cropped = image.crop((crop_left, crop_top, crop_right, crop_bottom))
    screenshot_path = _make_output_path("runelite_object_panel")
    cropped.save(screenshot_path)

    return {
        "screenshot_path": screenshot_path,
        "window": window_info,
        "capture_region": {
            "left": crop_left,
            "top": crop_top,
            "width": crop_right - crop_left,
            "height": crop_bottom - crop_top,
            "right_margin": right_margin,
            "top_margin": top_margin,
            "mode": "fallback_manual",
        },
        "anchor": None,
    }
