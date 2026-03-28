Patch 10
- Prefers anchoring on "slots"
- Falls back to "offers" if needed
- Parses the better plugin card view
- Extracts:
  - item_name
  - state
  - quantity_text
  - coin_text
  - coin_amount

Test:
1. In backend:
   pip install -r requirements.txt
2. Restart server
3. Open RuneLite with the slots panel visible
4. Open:
   http://127.0.0.1:8000/screen/capture-runelite-object-panel

Expected:
- anchor.normalized_text should be "slots"
- capture_region.mode should be "slots_anchor"
- offers[0] should include coin_amount if OCR reads it clearly
