Patch 19
- Fixes state-header detection when lines contain timer noise like "J Buy 01:01:45"
- Adds targeted OCR cleanup for the exact misses you reported:
  - Twisted cane
  - Saradomin brew(1)
  - Diamond amulet
  - Saradomin bracers
  - Stew
- Strips more leading OCR junk from item names
- Improves candidate item selection so "Stew" is preferred over junk like "Ofl"

Test:
1. In backend:
   pip install -r requirements.txt
2. Restart server
3. Open:
   http://127.0.0.1:8000/ui
4. Click Refresh with the same stacked slots visible
5. Check whether Twisted cane and Stew now appear, and whether brew is corrected to Saradomin brew(1)
