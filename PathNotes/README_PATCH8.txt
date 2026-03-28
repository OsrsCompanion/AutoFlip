Patch 8
- Fixes RuneLite window detection
- Rejects browser/docs titles that contain "runelite" in the URL
- Adds /screen/windows for debugging

Test:
1. In backend:
   pip install -r requirements.txt
2. Restart server
3. Open:
   http://127.0.0.1:8000/screen/windows

Find your real RuneLite window and confirm:
- title starts with RuneLite
- looks_like_runelite is true

Then test:
   http://127.0.0.1:8000/screen/capture-runelite-object-panel
