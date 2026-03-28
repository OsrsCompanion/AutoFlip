Patch 21
- Adds tabs:
  - Trading
  - Personal Settings
  - Debug
- Moves OpenAI API key to Personal Settings
- Moves Debug JSON to its own Debug tab
- Keeps Trading focused on controls, offers, and recommendations

Test:
1. In backend:
   pip install -r requirements.txt
2. Restart server
3. Open:
   http://127.0.0.1:8000/ui
4. Check the 3 tabs
5. Save the API key from Personal Settings
6. Use Trading for Refresh / Build Recommendations
