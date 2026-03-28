# OSRS Flip Assistant

Local screen-reading GE assistant.

## Current patch
Patch 1 adds:
- runnable FastAPI backend
- `GET /health`

## Run
From the `backend` folder:

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Test
Open:

```text
http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok"}
```
