from app.services.capture import capture_runelite_window_top_right_region
from app.services.ge_parser import parse_image


def get_current_offers() -> dict:
    capture_result = capture_runelite_window_top_right_region(
        width=420,
        height=1200,
        right_margin=20,
        top_margin=70,
        auto_anchor=True,
    )

    if "error" in capture_result:
        return {"offers": [], "offer_count": 0, "error": capture_result["error"]}

    path = capture_result["screenshot_path"]
    parsed = parse_image(path)
    parsed["window"] = capture_result.get("window")
    parsed["capture_region"] = capture_result.get("capture_region")
    parsed["anchor"] = capture_result.get("anchor")
    parsed["screenshot_path"] = path
    return parsed
