from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.routes import board, market, player, settings
from app.routes.auth import router as auth_router
from app.routes.plugin import router as plugin_router

app = FastAPI(title="OSRS Companion")

# TEMPORARY OPERATIONAL PROBE:
# Proves Restart Web is restarting the live Pi web process.
import time
print("STARTUP_DELAY_PROBE_ACTIVE: sleeping 25 seconds before web startup")
time.sleep(25)
print("STARTUP_DELAY_PROBE_COMPLETE")
print("?? USING UPDATED MAIN.PY (NO AI / NO SCREEN) ??")

ALLOWED_ORIGINS = [
    "https://www.autoflip.gg",
    "https://autoflip.gg",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market.router)
app.include_router(board.router)
app.include_router(settings.router)
app.include_router(player.router)
app.include_router(auth_router)
app.include_router(plugin_router)

UI_DIR = Path(__file__).resolve().parent / "ui"
NVME_STATIC_DIR = Path("/mnt/nvme/autoflip-data/static")

# Mount NVMe static assets before the root UI mount so /static/item_icons/*
# is served from persistent runtime data instead of the app code folder.
NVME_STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(NVME_STATIC_DIR)), name="static")


@app.get("/market/item/{slug_or_id}", include_in_schema=False)
def market_item_page(slug_or_id: str):
    """Serve the app shell for canonical item market pages."""
    return FileResponse(str(UI_DIR / "app" / "index.html"))


app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")




