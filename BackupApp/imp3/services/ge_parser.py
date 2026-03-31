import re
from difflib import SequenceMatcher, get_close_matches
from typing import Any

import cv2
import numpy as np
import pytesseract
from PIL import Image

from app.services.wiki_prices import get_item_names

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

NOISE_WORDS = {
    "slots", "flipping", "stats", "ai", "search", "offers",
    "@", "|", "x", "gp", "coins", "coin", "®",
    "ofl", "oft", "ofi", "offi", "ol", "ae of", "oo oft", "q", "a", "e", "ch",
    "ff", "bfr", "gel", "bs:", "al.", "ee", "aft",
}
STATE_WORDS = {"buy": "buying", "sell": "selling", "sold": "sold", "bought": "bought"}
STATE_KEYS = tuple(STATE_WORDS.keys())
KNOWN_ITEM_NAMES = get_item_names()


def _preprocess_image(img: Image.Image) -> Image.Image:
    arr = np.array(img.convert("L"))
    arr = cv2.resize(arr, None, fx=2.2, fy=2.2, interpolation=cv2.INTER_CUBIC)
    arr = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    return Image.fromarray(arr)


def clean_word(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("®", "").strip())


def infer_state(text: str) -> str:
    lower = text.lower()
    for key, value in STATE_WORDS.items():
        if re.search(rf"\b{key}\b", lower):
            return value
    if "cancel" in lower:
        return "cancelled"
    return "unknown"


def extract_status_text(text: str) -> str:
    lower = text.lower()
    for key in STATE_KEYS:
        if re.search(rf"\b{key}\b", lower):
            return key.capitalize()
    return "Unknown"


def looks_like_state_header(text: str) -> bool:
    lower = clean_word(text).lower()
    return any(re.search(rf"\b{key}\b", lower) for key in STATE_KEYS)


def looks_like_ratio(text: str) -> bool:
    return bool(re.fullmatch(r"\d+\s*/\s*\d+", text.strip()))


def looks_like_coin_value(text: str) -> bool:
    lower = text.lower()
    return "coin" in lower or bool(re.fullmatch(r"[\d,\.]+", text.strip()))


def extract_coin_amount(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return None
    try:
        return int(digits)
    except Exception:
        return None


def clean_coin_text(text: str) -> str:
    text = clean_word(text)
    m = re.search(r"(\d[\d,\.]*\s*coins?)", text, flags=re.IGNORECASE)
    if m:
        text = m.group(1)
    text = text.replace(".", ",")
    text = re.sub(r"\b(?:bd|ee|ai|fl|fi|gp|a|cains|y|b|f)\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_item_name(text: str) -> str:
    text = clean_word(text)
    text = re.sub(r"^[^A-Za-z]+", "", text)
    text = re.sub(r"^[A-Za-z][\.\~\-\>]+\s*", "", text)
    text = re.sub(r"^[A-Za-z]\\\s*", "", text)
    text = text.replace("\\", "").replace("|", "").replace("*", "").strip()
    text = re.sub(r"\s+\d+$", "", text)
    return text


def _manual_name_fix(cleaned: str) -> str:
    lower = cleaned.lower()

    if "trout" in lower:
        return "Trout"
    if "twisted cane" in lower:
        return "Twisted cane"
    if "diamond amulet" in lower or "dismond amulet" in lower:
        return "Diamond amulet"
    if "bucket helm" in lower:
        return "Bucket helm"
    if "catherby teleport" in lower:
        return "Catherby teleport"
    if "saradomin bracers" in lower or "saradamin bracers" in lower:
        return "Saradomin bracers"
    if "stew" in lower or "skew" in lower or "ct wo" in lower:
        return "Stew"
    if "saradomin brew" in lower or "breu(1)" in lower or "brew(1" in lower:
        return "Saradomin brew(1)"

    return cleaned


def correct_item_name(text: str) -> str:
    cleaned = _manual_name_fix(clean_item_name(text))
    if not cleaned:
        return cleaned

    if cleaned in KNOWN_ITEM_NAMES:
        return cleaned

    lower_lookup = {name.lower(): name for name in KNOWN_ITEM_NAMES}
    if cleaned.lower() in lower_lookup:
        return lower_lookup[cleaned.lower()]

    matches = get_close_matches(cleaned, KNOWN_ITEM_NAMES, n=1, cutoff=0.78)
    if matches:
        return matches[0]

    matches_lower = get_close_matches(cleaned.lower(), list(lower_lookup.keys()), n=1, cutoff=0.78)
    if matches_lower:
        return lower_lookup[matches_lower[0]]

    return cleaned


def looks_like_item_name(text: str) -> bool:
    lower = text.lower().strip()
    if not lower:
        return False
    if lower in NOISE_WORDS:
        return False
    if looks_like_state_header(lower):
        return False
    if looks_like_coin_value(lower):
        return False
    if looks_like_ratio(lower):
        return False
    if lower.startswith("@"):
        return False
    if len(lower) <= 2:
        return False
    if sum(ch.isalpha() for ch in lower) < 3:
        return False
    alnum = sum(ch.isalnum() for ch in lower)
    alpha = sum(ch.isalpha() for ch in lower)
    if alnum == 0 or alpha / max(len(lower), 1) < 0.35:
        return False
    return True


def _ocr_lines(img: Image.Image) -> list[dict[str, Any]]:
    processed = _preprocess_image(img)
    data = pytesseract.image_to_data(
        processed,
        output_type=pytesseract.Output.DICT,
        config="--psm 6",
    )

    grouped: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    for i, raw in enumerate(data.get("text", [])):
        text = clean_word(raw or "")
        if not text:
            continue

        conf_raw = data.get("conf", ["-1"])[i]
        try:
            conf = float(conf_raw)
        except Exception:
            conf = -1.0

        if conf < -0.5:
            continue

        key = (
            int(data["block_num"][i]),
            int(data["par_num"][i]),
            int(data["line_num"][i]),
        )
        grouped.setdefault(key, []).append(
            {
                "text": text,
                "left": int(data["left"][i]),
                "top": int(data["top"][i]),
                "width": int(data["width"][i]),
                "height": int(data["height"][i]),
            }
        )

    lines: list[dict[str, Any]] = []
    for words in grouped.values():
        words.sort(key=lambda w: w["left"])
        joined = " ".join(word["text"] for word in words).strip()
        lines.append(
            {
                "text": joined,
                "left": min(word["left"] for word in words),
                "top": min(word["top"] for word in words),
                "right": max(word["left"] + word["width"] for word in words),
                "bottom": max(word["top"] + word["height"] for word in words),
            }
        )

    lines.sort(key=lambda line: (line["top"], line["left"]))
    return lines


def _candidate_name_score(raw_text: str, corrected: str, index_offset: int) -> float:
    cleaned_raw = clean_item_name(raw_text)
    ratio = SequenceMatcher(None, cleaned_raw.lower(), corrected.lower()).ratio()
    score = ratio
    score += max(0, 0.8 - (index_offset * 0.12))
    if corrected != cleaned_raw:
        score += 0.25
    if corrected in KNOWN_ITEM_NAMES:
        score += 0.2
    weird = sum(not ch.isalnum() and not ch.isspace() for ch in raw_text)
    score -= weird * 0.05
    return score


def _find_item_near_state(lines: list[dict[str, Any]], start_index: int) -> tuple[str | None, int]:
    best_text = None
    best_index = start_index + 1
    best_score = -999.0

    for i in range(start_index + 1, min(len(lines), start_index + 8)):
        text = lines[i]["text"]
        if looks_like_state_header(text):
            break
        if looks_like_coin_value(text) or looks_like_ratio(text):
            continue
        if not looks_like_item_name(text):
            continue

        corrected = correct_item_name(text)
        score = _candidate_name_score(text, corrected, i - start_index)

        if score > best_score:
            best_score = score
            best_text = corrected
            best_index = i

    return best_text, best_index


def _find_ratio_and_coin(lines: list[dict[str, Any]], start_index: int) -> tuple[str | None, str | None, int]:
    ratio_text = None
    coin_text = None
    next_index = start_index + 1

    for i in range(start_index + 1, min(len(lines), start_index + 10)):
        text = lines[i]["text"]
        if looks_like_state_header(text):
            break
        if ratio_text is None and looks_like_ratio(text):
            ratio_text = text
            next_index = i + 1
            continue
        if coin_text is None and looks_like_coin_value(text):
            coin_text = clean_coin_text(text)
            next_index = i + 1
            continue

    return ratio_text, coin_text, next_index


def _parse_card_from_lines(lines: list[dict[str, Any]], start_index: int) -> tuple[dict[str, Any] | None, int]:
    state_text = lines[start_index]["text"]
    if not looks_like_state_header(state_text):
        return None, start_index + 1

    item_name, item_index = _find_item_near_state(lines, start_index)
    if not item_name:
        return None, start_index + 1

    ratio_text, coin_text, next_index = _find_ratio_and_coin(lines, item_index)

    entry: dict[str, Any] = {
        "item_name": item_name,
        "status_text": extract_status_text(state_text),
        "state": infer_state(state_text),
    }

    if ratio_text is not None:
        entry["quantity_text"] = ratio_text
    if coin_text is not None:
        entry["coin_text"] = coin_text
        entry["coin_amount"] = extract_coin_amount(coin_text)

    return entry, next_index


def extract_offers_from_line_data(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    offers: list[dict[str, Any]] = []
    i = 0

    while i < len(lines):
        if not looks_like_state_header(lines[i]["text"]):
            i += 1
            continue

        entry, next_index = _parse_card_from_lines(lines, i)
        if entry is not None:
            duplicate = any(
                existing.get("item_name") == entry.get("item_name")
                and existing.get("state") == entry.get("state")
                and existing.get("coin_amount") == entry.get("coin_amount")
                and existing.get("quantity_text") == entry.get("quantity_text")
                for existing in offers
            )
            if not duplicate:
                offers.append(entry)

        i = max(next_index, i + 1)

    return offers


def parse_image(path: str) -> dict[str, Any]:
    try:
        img = Image.open(path)
        line_data = _ocr_lines(img)
        raw_text = pytesseract.image_to_string(_preprocess_image(img), config="--psm 6")
        lines = [line["text"] for line in line_data]
        offers = extract_offers_from_line_data(line_data)

        return {
            "source_type": "runelite_slots_panel",
            "raw_text": raw_text,
            "lines": lines,
            "offers": offers,
            "offer_count": len(offers),
        }
    except Exception as e:
        return {"error": str(e)}
