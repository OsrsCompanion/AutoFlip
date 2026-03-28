from fastapi import APIRouter, File, UploadFile

from app.services.capture import (
    capture_region,
    capture_runelite_top_right_region,
    capture_runelite_window_top_right_region,
    capture_screen,
    list_window_titles,
)
from app.services.ge_parser import parse_image
from app.services.settings_store import load_settings, save_settings

router = APIRouter(prefix="/screen", tags=["screen"])


def persist_last_scan(result: dict) -> dict:
    settings = load_settings()
    settings["last_scan"] = {
        "offers": result.get("offers", []),
        "offer_count": result.get("offer_count", 0),
        "capture_mode": result.get("capture_mode"),
        "anchor": result.get("anchor"),
        "capture_region": result.get("capture_region"),
        "window": result.get("window"),
        "screenshot_path": result.get("screenshot_path"),
    }
    save_settings(settings)
    return result


@router.get("/capture")
def capture():
    path = capture_screen()
    return {"screenshot_path": path}


@router.get("/parse")
def parse():
    path = capture_screen()
    result = parse_image(path)
    result["screenshot_path"] = path
    return persist_last_scan(result)


@router.post("/parse-upload")
async def parse_upload(file: UploadFile = File(...)):
    temp_path = "screenshots/uploaded_panel.png"

    content = await file.read()
    with open(temp_path, "wb") as f:
        f.write(content)

    result = parse_image(temp_path)
    result["screenshot_path"] = temp_path
    return persist_last_scan(result)


@router.get("/capture-panel")
def capture_panel(left: int, top: int, width: int, height: int):
    path = capture_region(left=left, top=top, width=width, height=height)
    result = parse_image(path)
    result["capture_region"] = {
        "left": left,
        "top": top,
        "width": width,
        "height": height,
    }
    result["screenshot_path"] = path
    return persist_last_scan(result)


@router.get("/capture-runelite-panel")
def capture_runelite_panel(width: int = 280, height: int = 820, right_margin: int = 0, top_margin: int = 0):
    capture_result = capture_runelite_top_right_region(
        width=width,
        height=height,
        right_margin=right_margin,
        top_margin=top_margin,
    )

    if "error" in capture_result:
        return capture_result

    path = capture_result["screenshot_path"]
    parse_result = parse_image(path)
    parse_result["window"] = capture_result["window"]
    parse_result["capture_region"] = capture_result["capture_region"]
    parse_result["screenshot_path"] = path
    return persist_last_scan(parse_result)


@router.get("/capture-runelite-object-panel")
def capture_runelite_object_panel(
    width: int = 420,
    height: int = 1100,
    right_margin: int = 20,
    top_margin: int = 90,
    auto_anchor: bool = True,
):
    capture_result = capture_runelite_window_top_right_region(
        width=width,
        height=height,
        right_margin=right_margin,
        top_margin=top_margin,
        auto_anchor=auto_anchor,
    )

    if "error" in capture_result:
        return capture_result

    path = capture_result["screenshot_path"]
    parse_result = parse_image(path)
    parse_result["window"] = capture_result["window"]
    parse_result["capture_region"] = capture_result["capture_region"]
    parse_result["screenshot_path"] = path
    parse_result["capture_mode"] = "window_object"
    parse_result["anchor"] = capture_result.get("anchor")
    return persist_last_scan(parse_result)


@router.get("/windows")
def windows():
    return {"windows": list_window_titles()}
