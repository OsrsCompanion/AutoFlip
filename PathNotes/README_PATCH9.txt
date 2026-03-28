Patch 9
- Adds auto-anchor on the "Offers" text inside RuneLite
- Uses full RuneLite window capture first
- If "Offers" is found, crops relative to that text
- Falls back to old manual crop if not found

Test:
1. In backend:
   pip install -r requirements.txt
2. Restart server
3. Keep RuneLite open with the Offers panel visible
4. Open:
   http://127.0.0.1:8000/screen/capture-runelite-object-panel

Expected:
- anchor is not null
- capture_region.mode is "offers_anchor"
- OCR should now see item text from the offers panel
