Patch 24
- Forces AI replies toward JSON-only output
- Keeps AI responses short to reduce token usage
- Adds error handling so /ai/advice always returns JSON
- Renders advisor reply from structured JSON in the UI

Test:
1. In backend:
   pip install -r requirements.txt
2. Restart server
3. Open:
   http://127.0.0.1:8000/ui
4. Save a valid OpenAI API key in Personal Settings
5. Open AI Advisor
6. Ask a short question
7. Confirm the advisor reply renders as structured JSON content, not freeform prose
