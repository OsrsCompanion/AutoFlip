Patch 7
- Adds direct RuneLite window-object capture
- No need for RuneLite to be the active window
- Your own app can sit on top of RuneLite

Test:
1. In backend:
   pip install -r requirements.txt
2. Restart server
3. Keep RuneLite open anywhere on screen
4. Open:
   http://127.0.0.1:8000/screen/capture-runelite-object-panel

Tune if needed:
   http://127.0.0.1:8000/screen/capture-runelite-object-panel?width=420&height=900&right_margin=20&top_margin=120

Expected:
- Finds RuneLite by title
- Captures RuneLite window directly
- Crops its top-right panel region
- Parses returned image
