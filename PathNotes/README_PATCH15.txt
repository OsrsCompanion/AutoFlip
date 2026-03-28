Patch 15
- Supports budget shorthand:
  - 15k
  - 1.8m
  - 25m
  - 2.4b
- Expands slots-panel crop for fuller trade lists
- Improves multi-card parsing so more visible filled slots can be detected

Test:
1. In backend:
   pip install -r requirements.txt
2. Restart server
3. Open:
   http://127.0.0.1:8000/ui
4. Try budget values like:
   15k
   1.8m
   25m
5. Save settings
6. Click Refresh with multiple RuneLite slots filled
7. Check whether more current offers are detected
