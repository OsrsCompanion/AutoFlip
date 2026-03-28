Patch 22
- Adds current-trade review endpoint: /market/decisions
- Adds Trading UI section for:
  - keep
  - hold
  - replace
  - cancel
- Uses the current market snapshot and your hours-away setting
- Keeps tax math hidden in backend logic

Test:
1. In backend:
   pip install -r requirements.txt
2. Restart server
3. Open:
   http://127.0.0.1:8000/ui
4. Click:
   Review Current Trades
5. Check the Current Trade Decisions section
