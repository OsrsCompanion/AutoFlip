Patch 20
- Tightens item-line selection so nearby real item names beat junk lines
- Cleans coin text by keeping the first numeric "X coins" segment
- Fixes the reported cases:
  - Catherby teleport should stop becoming junk
  - Stew should beat "ct wo"
  - Saradomin brew(1) stays brew(1)

Test:
1. In backend:
   pip install -r requirements.txt
2. Restart server
3. Open:
   http://127.0.0.1:8000/ui
4. Click Refresh with the same stacked slots visible
5. Check whether Catherby teleport and Stew are now correct, and whether coin text is cleaner
