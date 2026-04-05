from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routes import ai, market, player, screen, settings
from app.routes.auth import router as auth_router
from app.routes.plugin import router as plugin_router

app = FastAPI(title="OSRS Companion")
print("🔥 USING UPDATED MAIN.PY 🔥")
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
app.include_router(settings.router)
app.include_router(screen.router)
app.include_router(ai.router)
app.include_router(player.router)
app.include_router(auth_router)
app.include_router(plugin_router)

UI_DIR = Path(__file__).resolve().parent / "ui"
app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")
