Patch 6
- Adds pygetwindow
- Adds /screen/capture-runelite-panel
- Finds the active RuneLite window
- Captures from the top-right region of that window

Test:
1. pip install -r requirements.txt
2. Restart server
3. Make RuneLite the active window
4. Open:
   http://127.0.0.1:8000/screen/capture-runelite-panel

You can tune with:
- width
- height
- right_margin
- top_margin

Example:
http://127.0.0.1:8000/screen/capture-runelite-panel?width=280&height=820&right_margin=0&top_margin=0
