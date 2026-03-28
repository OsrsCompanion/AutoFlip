Patch 14
- Adds market data fetching from the OSRS Wiki prices API
- Adds recommendation endpoint: /market/recommendations
- Uses saved settings:
  - budget
  - available slots
  - hours away
- Suggests replacement items for open slots
- Updates UI with Recommended Swaps and Top Candidates

Test:
1. In backend:
   pip install -r requirements.txt
2. Restart server
3. Open:
   http://127.0.0.1:8000/ui
4. Save settings if needed
5. Click:
   Build Recommendations

Expected:
- current offers still show
- recommended swaps show up
- top candidates show up
