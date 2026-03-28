Patch 25
- Adds OpenAI model dropdown in Personal Settings
- Defaults to gpt-5.4-mini
- Adds optional .txt API key path field
- Backend now reads the key from the path if no inline key is saved

Test:
1. In backend:
   pip install -r requirements.txt
2. Restart server
3. Open:
   http://127.0.0.1:8000/ui
4. Open Personal Settings
5. Choose model:
   gpt-5.4-mini
6. Either:
   - paste your API key
   - or enter a path to a .txt file containing only the key
7. Save Personal Settings
8. Open AI Advisor and ask a question
