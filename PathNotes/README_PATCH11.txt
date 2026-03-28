Patch 11
- Improves slots-card parsing
- Ignores junk OCR lines
- Cleans item names with trailing OCR digits
- Extracts state + coin amount even when ratio line is missing

Test:
1. In backend:
   pip install -r requirements.txt
2. Restart server
3. Open RuneLite with the slots panel visible
4. Open:
   http://127.0.0.1:8000/screen/capture-runelite-object-panel

Expected:
- offers[0].item_name should be Dragon cannon barrel
- offers[0].state should be selling
- offers[0].coin_amount should be 3248696
