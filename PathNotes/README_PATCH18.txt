Patch 18
- Uses OSRS Wiki item mapping as a correction dictionary for OCR names
- Fuzzy-matches OCR item names to real item names
- Improves repeated card parsing for stacked slots
- Attempts to capture quantity text like 0/1 more consistently

Test:
1. In backend:
   pip install -r requirements.txt
2. Restart server
3. Open:
   http://127.0.0.1:8000/ui
4. Click Refresh with many filled slots visible
5. Check whether more entries are detected and names are corrected
