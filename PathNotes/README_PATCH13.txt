Patch 13
- Adds local saved settings
- Adds settings UI for:
  - OpenAI API key
  - Budget
  - Available slots
  - Hours away
- Saves to data/settings.json

Test:
1. In backend:
   pip install -r requirements.txt
2. Restart server
3. Open:
   http://127.0.0.1:8000/ui
4. Change settings and click Save Settings
5. Refresh page and confirm settings persisted
