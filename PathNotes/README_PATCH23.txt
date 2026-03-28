Patch 23
- Adds AI Advisor tab
- Uses the saved OpenAI API key from Personal Settings
- Adds backend endpoint: /ai/advice
- Uses OpenAI Responses API through the official Python SDK
- Sends current offers, current-trade decisions, and recommendations as context

Test:
1. In backend:
   pip install -r requirements.txt
2. Restart server
3. Open:
   http://127.0.0.1:8000/ui
4. Save a valid OpenAI API key in Personal Settings
5. Open the AI Advisor tab
6. Ask a question like:
   What should I cancel and what should I replace for 12 hours away?
