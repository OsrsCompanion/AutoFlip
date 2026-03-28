Patch 17
- Improves multi-card trade reading for stacked slot lists
- Expands the anchored crop to the bottom of the RuneLite panel
- Uses OCR line data instead of plain text-only parsing
- Budget field now auto-formats on blur, Enter, and Tab
- Recommendation profit math now uses the GE tax rule you specified
- Tax math stays hidden from the UI and is only stored in backend fields

Test:
1. In backend:
   pip install -r requirements.txt
2. Restart server
3. Open:
   http://127.0.0.1:8000/ui
4. Type 2.5m in Budget, then press Tab
5. Confirm it becomes 2,500,000
6. Open a RuneLite panel with many filled slot cards
7. Click Refresh
8. Check whether multiple offers are now detected
